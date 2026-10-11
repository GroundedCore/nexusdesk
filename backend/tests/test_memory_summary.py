"""Phase 1 conversation summary — local unit tests (no database required).

Case IDs refer to docs/requirement/agent-memory/phase-1-conversation-summary/04-test-plan.md.
Database-bound cases with UT numbers (task merge, retry transitions) live in
test_memory_postgres.py.
"""

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent_platform.modules.agent_config.service import AgentConfig
from agent_platform.modules.agent_runtime.engine import RuntimeEngine
from agent_platform.modules.memory.bootstrap import embedded_memory_worker
from agent_platform.modules.memory.summary import (
    INJECTION_HEADER,
    SummaryError,
    backoff_seconds,
    build_prompt,
    clean_output,
    render_injection,
    require_binding,
    trim_dropped,
)
from agent_platform.modules.memory.worker import MemoryWorker
from agent_platform.modules.tool_gateway.service import ToolGateway
from agent_platform.settings import Settings

asyncio_mark = pytest.mark.asyncio


def dropped(count, prefix="旧消息"):
    return [
        {"role": "user" if i % 2 == 0 else "assistant", "content": f"{prefix}-{i}"}
        for i in range(count)
    ]


class SequenceModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.seen = []

    async def ainvoke(self, messages):
        self.seen.append(messages)
        return self.responses.pop(0)


async def ignore_event(kind, data):
    pass


def test_p1_ut_01_prompt_without_old_summary_contains_all_dropped_messages():
    messages = build_prompt(None, dropped(4), 1000)
    assert messages[0]["role"] == "system"
    assert "【此前的对话摘要】" not in messages[1]["content"]
    for i in range(4):
        assert f"旧消息-{i}" in messages[1]["content"]
    assert "user: 旧消息-0" in messages[1]["content"]


def test_p1_ut_02_prompt_with_old_summary_merges_both_sections():
    messages = build_prompt("客户对花生过敏，订单号 A123。", dropped(2), 1000)
    assert "【此前的对话摘要】" in messages[1]["content"]
    assert "客户对花生过敏，订单号 A123。" in messages[1]["content"]
    assert "【新被截断的对话片段】" in messages[1]["content"]
    assert "合并" in messages[0]["content"]
    assert "1000" in messages[0]["content"]


def test_p1_ut_03_blank_model_output_is_rejected():
    assert clean_output("", 1000) is None
    assert clean_output("   \n\t ", 1000) is None
    assert clean_output(None, 1000) is None
    assert clean_output(" 有效摘要 ", 1000) == "有效摘要"


def test_p1_ut_04_oversized_output_keeps_tail():
    text = "头" * 500 + "尾" * 800
    cleaned = clean_output(text, 1000)
    assert cleaned is not None and len(cleaned) == 1000
    assert cleaned == text[-1000:]
    assert cleaned.endswith("尾" * 100)


def test_p1_ut_07_dropped_messages_trimmed_to_max_input():
    messages = dropped(50)
    trimmed = trim_dropped(messages, 40)
    assert trimmed == messages[-40:]
    assert trim_dropped(messages, 100) == messages


def test_p1_ut_08_injection_truncates_to_token_budget_keeping_tail():
    summary = "旧" * 100 + "新" * 2000
    rendered = render_injection(summary)
    head, _, body = rendered.partition("\n")
    assert head == INJECTION_HEADER
    assert len(body) == 800 * 2  # 800 tokens at ~2 chars/token
    assert body == summary[-1600:]
    # A short summary passes through untouched.
    assert render_injection("短摘要") == f"{INJECTION_HEADER}\n短摘要"


def test_p1_ut_09_profile_binding_selection():
    assert require_binding({"profile_id": "p", "profile_version": 3}) == ("p", 3)
    for binding in (None, {}, {"profile_id": None, "profile_version": None}):
        with pytest.raises(SummaryError, match="no_chat_profile") as exc:
            require_binding(binding)
        assert exc.value.retryable is False


def test_p1_ut_10_backoff_grows_exponentially_and_is_capped():
    assert backoff_seconds(1) == 30
    assert backoff_seconds(2) == 60
    assert backoff_seconds(3) == 120
    assert backoff_seconds(99) == 300


class _DummyServices:
    """Assembly only stores engine/gateway; no database is touched."""

    class repository:
        engine = None

    class platform:
        gateway = None


def test_p1_ut_12_embedded_worker_assembly_follows_deployment_flags():
    services = _DummyServices()
    # All four embedded_worker × summary_enabled combinations.
    both_off = Settings(_env_file=None, embedded_worker=False, summary_enabled=False)
    assert embedded_memory_worker(services, both_off) is None
    # Production form: the API process never assembles the loop.
    assert embedded_memory_worker(services, Settings(_env_file=None, embedded_worker=False)) is None
    # Feature switched off: nothing to run even in the embedded form.
    assert embedded_memory_worker(services, Settings(_env_file=None, summary_enabled=False)) is None
    # Quickstart/development form: embedded mode assembles the claim loop.
    worker = embedded_memory_worker(services, Settings(_env_file=None, embedded_worker=True))
    assert isinstance(worker, MemoryWorker) and worker.stopping.is_set() is False


def test_p1_cm_02_summary_settings_defaults_and_env_override(monkeypatch):
    settings = Settings(_env_file=None)
    assert settings.summary_enabled is True
    assert settings.summary_max_input_messages == 40
    assert settings.summary_max_output_chars == 1000
    monkeypatch.setenv("AGENT_SUMMARY_ENABLED", "false")
    monkeypatch.setenv("AGENT_SUMMARY_MAX_INPUT_MESSAGES", "12")
    monkeypatch.setenv("AGENT_SUMMARY_MAX_OUTPUT_CHARS", "600")
    overridden = Settings(_env_file=None)
    assert overridden.summary_enabled is False
    assert overridden.summary_max_input_messages == 12
    assert overridden.summary_max_output_chars == 600


def test_agent_config_summary_enabled_defaults_on_and_validates_legacy_payloads():
    assert AgentConfig(system_prompt="x").summary_enabled is True
    assert AgentConfig(system_prompt="x", summary_enabled=False).summary_enabled is False
    # Drafts saved before this release have no summary_enabled key at all.
    legacy = AgentConfig.model_validate({"system_prompt": "x", "tool_names": []})
    assert legacy.summary_enabled is True


@asyncio_mark
async def test_p1_it_03_engine_injects_summary_between_prompt_and_history():
    model = SequenceModel(AIMessage(content="回答"))
    history = [
        {"role": "user", "content": "早期问题"},
        {"role": "assistant", "content": "早期回答"},
    ]
    async with httpx.AsyncClient() as client:
        engine = RuntimeEngine(model, ToolGateway(client, []), Settings())
        answer, _, _ = await engine.run("新问题", history, ignore_event, summary="订单号 A123")
    assert answer == "回答"
    seen = model.seen[0]
    assert isinstance(seen[0], SystemMessage) and seen[0].content == Settings().system_prompt
    assert isinstance(seen[1], SystemMessage)
    assert seen[1].content == f"{INJECTION_HEADER}\n订单号 A123"
    assert isinstance(seen[2], HumanMessage) and seen[2].content == "早期问题"
    assert seen[-1].content == "新问题"


@asyncio_mark
async def test_engine_skips_empty_or_disabled_summary():
    model = SequenceModel(AIMessage(content="a"), AIMessage(content="b"))
    async with httpx.AsyncClient() as client:
        engine = RuntimeEngine(model, ToolGateway(client, []), Settings())
        await engine.run("q1", [], ignore_event, summary=None)
        disabled = RuntimeEngine(
            model, ToolGateway(client, []), Settings(summary_enabled=False)
        )
        await disabled.run("q2", [], ignore_event, summary="不应注入")
    for seen in model.seen:
        assert len([m for m in seen if isinstance(m, SystemMessage)]) == 1


@asyncio_mark
async def test_engine_injection_obeys_token_budget():
    model = SequenceModel(AIMessage(content="ok"))
    async with httpx.AsyncClient() as client:
        engine = RuntimeEngine(model, ToolGateway(client, []), Settings())
        await engine.run("q", [], ignore_event, summary="长" * 5000)
    body = model.seen[0][1].content.partition("\n")[2]
    assert len(body) == 1600


@asyncio_mark
async def test_engine_reports_dropped_messages_when_window_overflows():
    model = SequenceModel(AIMessage(content="ok"))
    settings = Settings(history_turns=1)
    history = [
        {"role": "user", "content": "u1"},
        {"role": "assistant", "content": "a1"},
    ]
    async with httpx.AsyncClient() as client:
        engine = RuntimeEngine(model, ToolGateway(client, []), settings)
        _, final, dropped_messages = await engine.run("u2", history, ignore_event)
    # history_turns=1 keeps one turn; the whole previous turn drops out.
    assert dropped_messages == history
    assert [item["content"] for item in final] == ["u2", "ok"]


@asyncio_mark
async def test_engine_reports_no_dropped_messages_inside_window():
    model = SequenceModel(AIMessage(content="ok"))
    async with httpx.AsyncClient() as client:
        engine = RuntimeEngine(model, ToolGateway(client, []), Settings())
        _, final, dropped_messages = await engine.run("q", [], ignore_event)
    assert dropped_messages == [] and len(final) == 2


@asyncio_mark
async def test_demo_model_never_calls_tools_the_caller_did_not_offer():
    # Regression: a summary prompt quoting an earlier demo reply (which mentions
    # "查询演示") must get plain text back when the request carries no tools.
    from agent_platform.modules.model_gateway.service import DemoModel

    reply = await DemoModel([]).ainvoke([HumanMessage(content="此前回复提到查询演示链路")])
    assert not reply.tool_calls and reply.content.strip()
    offered = {"function": {"name": "demo_order_lookup", "parameters": {"type": "object"}}}
    reply = await DemoModel([offered]).ainvoke([HumanMessage(content="查询演示")])
    assert reply.tool_calls and reply.tool_calls[0]["name"] == "demo_order_lookup"
