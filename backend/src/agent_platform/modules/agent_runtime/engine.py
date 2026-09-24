import asyncio
import json
from typing import TypedDict

from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage, ToolMessage
from langgraph.graph import END, START, StateGraph

from agent_platform.modules.agent_runtime.schemas import RuntimeFault


class RunState(TypedDict):
    messages: list[BaseMessage]
    rounds: int
    tool_calls: int
    signatures: list[str]
    output: str


class RuntimeEngine:
    def __init__(self, model, tools, settings):
        self.model, self.tools, self.settings = model, tools, settings
        graph = StateGraph(RunState)
        graph.add_node("model", self._model)
        graph.add_node("tools", self._tools)
        graph.add_edge(START, "model")
        graph.add_conditional_edges("model", self._route, {"tools": "tools", "end": END})
        graph.add_edge("tools", "model")
        self.graph = graph.compile()

    async def _model(self, state, config):
        emit = config["configurable"]["emit"]
        if state["rounds"] >= self.settings.max_model_rounds:
            raise RuntimeFault("model_round_limit")
        await emit("model.started", {"round": state["rounds"] + 1})
        try:
            async with asyncio.timeout(self.settings.model_timeout_seconds):
                reply = await self.model.ainvoke(state["messages"])
        except TimeoutError as exc:
            raise RuntimeFault("model_timeout") from exc
        if not isinstance(reply, AIMessage) or reply.invalid_tool_calls:
            raise RuntimeFault("invalid_model_response")
        if len({call["id"] for call in reply.tool_calls}) != len(reply.tool_calls):
            raise RuntimeFault("duplicate_tool_call_id")
        if state["tool_calls"] + len(reply.tool_calls) > self.settings.max_tool_calls:
            raise RuntimeFault("tool_call_limit")
        output = (
            reply.content
            if isinstance(reply.content, str)
            else "".join(
                block.get("text", "")
                for block in reply.content
                if isinstance(block, dict) and block.get("type") == "text"
            )
        )
        if not reply.tool_calls and not output.strip():
            raise RuntimeFault("empty_model_response")
        if len(output) > 64000:
            raise RuntimeFault("model_output_too_large")
        await emit(
            "model.completed",
            {
                "round": state["rounds"] + 1,
                "tool_calls": len(reply.tool_calls),
                "usage": reply.usage_metadata or {},
                "gateway_call_id": reply.response_metadata.get("gateway_call_id"),
            },
        )
        return {
            "messages": [*state["messages"], reply],
            "rounds": state["rounds"] + 1,
            "output": output if not reply.tool_calls else "",
        }

    def _route(self, state):
        return "tools" if state["messages"][-1].tool_calls else "end"

    async def _tools(self, state, config):
        emit = config["configurable"]["emit"]
        calls = state["messages"][-1].tool_calls
        signatures = list(state["signatures"])
        for call in calls:
            signature = json.dumps([call["name"], call["args"]], sort_keys=True)
            if signature in signatures:
                raise RuntimeFault("repeated_tool_call")
            signatures.append(signature)

        async def invoke(call):
            await emit("tool.started", {"name": call["name"], "call_id": call["id"]})
            result = await self.tools.execute(call["name"], call["args"])
            await emit(
                "tool.completed",
                {
                    "name": call["name"],
                    "call_id": call["id"],
                    "ok": result["ok"],
                    "error": result.get("error"),
                    "gateway_call_id": result.get("call_id"),
                    "execution_status": result.get("execution_status"),
                    "tool_version": result.get("tool_version"),
                },
            )
            return ToolMessage(
                content=json.dumps(result, ensure_ascii=False), tool_call_id=call["id"]
            )

        # The gateway only exposes independent read-only GET tools in this release.
        async with asyncio.TaskGroup() as group:
            tasks = [group.create_task(invoke(call)) for call in calls]
        return {
            "messages": [*state["messages"], *(task.result() for task in tasks)],
            "tool_calls": state["tool_calls"] + len(calls),
            "signatures": signatures,
        }

    async def run(self, message, history, emit):
        messages: list[BaseMessage] = [SystemMessage(content=self.settings.system_prompt)]
        kept = history[-self.settings.history_turns * 2 :] if self.settings.history_turns else []
        for item in kept:
            messages.append(
                HumanMessage(content=item["content"])
                if item["role"] == "user"
                else AIMessage(
                    content=item["content"],
                    additional_kwargs={"reasoning_content": item["reasoning_content"]}
                    if item.get("reasoning_content") is not None
                    else {},
                )
            )
        messages.append(HumanMessage(content=message))
        async with asyncio.timeout(self.settings.run_timeout_seconds):
            result = await self.graph.ainvoke(
                {
                    "messages": messages,
                    "rounds": 0,
                    "tool_calls": 0,
                    "signatures": [],
                    "output": "",
                },
                config={
                    "recursion_limit": self.settings.max_model_rounds * 2 + 2,
                    "configurable": {"emit": emit},
                },
            )
        new_history = [
            *kept,
            {"role": "user", "content": message},
            {
                "role": "assistant",
                "content": result["output"],
                **(
                    {
                        "reasoning_content": result["messages"][-1].additional_kwargs[
                            "reasoning_content"
                        ]
                    }
                    if "reasoning_content" in result["messages"][-1].additional_kwargs
                    else {}
                ),
            },
        ]
        return result["output"], new_history[
            -self.settings.history_turns * 2 :
        ] if self.settings.history_turns else []
