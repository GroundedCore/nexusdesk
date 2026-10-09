import asyncio
import json
import time
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
        round_no = state["rounds"] + 1
        started = time.monotonic()
        await emit("model.started", {"round": round_no})
        # Time to the first visible character of this round. Recorded so the console
        # can report how long a customer waited for the answer to start, which is
        # what the typewriter effect is really for.
        first_token_ms = None

        async def on_delta(delta):
            nonlocal first_token_ms
            # Forward only content and reasoning; tool_calls here are partial JSON
            # arguments and arrive complete with model.completed instead.
            piece = {}
            if delta.get("content"):
                piece["text"] = delta["content"]
            if delta.get("reasoning_content"):
                piece["reasoning"] = delta["reasoning_content"]
            if piece:
                if first_token_ms is None:
                    first_token_ms = int((time.monotonic() - started) * 1000)
                await emit("model.delta", {"round": round_no, **piece})

        try:
            async with asyncio.timeout(self.settings.model_timeout_seconds):
                stream = getattr(self.model, "ainvoke_stream", None)
                if self.settings.stream_model_deltas and stream is not None:
                    reply = await stream(state["messages"], on_delta)
                else:
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
                # Milliseconds from model.started to the first visible character, or
                # null when the round produced no text at all (a pure tool call).
                "first_token_ms": first_token_ms,
                "duration_ms": int((time.monotonic() - started) * 1000),
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
            tool_started = time.monotonic()
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
                    "duration_ms": int((time.monotonic() - tool_started) * 1000),
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
