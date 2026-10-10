from typing import Protocol

from langchain_core.messages import AIMessage, BaseMessage, ToolMessage

from agent_platform.modules.agent_runtime.schemas import RuntimeFault
from agent_platform.settings import Settings


class ChatModel(Protocol):
    async def ainvoke(self, messages: list[BaseMessage]) -> AIMessage: ...


class DemoModel:
    """Deterministic plumbing demo. Never represents real model or business results."""

    def __init__(self, schemas=None):
        self.available = {s["function"]["name"] for s in (schemas or [])}

    async def ainvoke(self, messages):
        if isinstance(messages[-1], ToolMessage):
            return AIMessage(content="[演示模式] API 工具结果：" + str(messages[-1].content))
        text = str(messages[-1].content)
        if "工单" in text and "propose_ticket" in self.available:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "ticket_proposal",
                        "name": "propose_ticket",
                        "args": {"title": text[:100], "description": text},
                    }
                ],
            )
        if "knowledge_search" in self.available and "查询演示" not in text:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "knowledge_query",
                        "name": "knowledge_search",
                        "args": {"query": text[:500]},
                    }
                ],
            )
        # Never emit a tool call the caller did not offer: summary-style chats
        # pass no tools, and quoting an earlier demo reply must not change that.
        if "查询演示" in str(messages[-1].content) and "demo_order_lookup" in self.available:
            return AIMessage(
                content="",
                tool_calls=[
                    {
                        "id": "demo_query",
                        "name": "demo_order_lookup",
                        "args": {"order_id": "DEMO-001"},
                    }
                ],
            )
        return AIMessage(
            content="[演示模式] Runtime 已收到消息。发送“查询演示”可验证 HTTP 工具链路。"
        )


class UnconfiguredModel:
    async def ainvoke(self, messages):
        raise RuntimeFault("model_not_configured")


def build_model(settings: Settings, schemas: list[dict]) -> ChatModel:
    if settings.model_backend == "unconfigured":
        return UnconfiguredModel()
    if settings.model_backend == "demo":
        return DemoModel(schemas)
    from langchain_openai import ChatOpenAI

    model = ChatOpenAI(
        model=settings.model_name,
        api_key=settings.model_api_key,
        base_url=settings.model_base_url,
        use_responses_api=settings.model_use_responses_api,
        timeout=settings.model_timeout_seconds,
        max_retries=0,
        max_tokens=settings.model_max_output_tokens,
    )
    return model.bind_tools(schemas) if schemas else model
