"""Phase 2 customer profile/preference memory — local unit tests (no database).

Case IDs refer to docs/requirement/agent-memory/phase-2-customer-profile/04-test-plan.md.
Database-bound cases (resolve_customer, upsert merge, integration, API) live in
test_memory_customers_postgres.py; the migration check in
test_memory_customer_migrations.py.
"""

import json
from uuid import uuid4

import httpx
import pytest
from langchain_core.messages import AIMessage, HumanMessage, SystemMessage

from agent_platform.modules.agent_config.service import AgentConfig
from agent_platform.modules.agent_runtime.engine import RuntimeEngine
from agent_platform.modules.channel.service import session_id_hash
from agent_platform.modules.memory.bootstrap import MemoryDispatcher
from agent_platform.modules.memory.extraction import (
    EXTRACT_KIND,
    MAX_CONTENT_CHARS,
    build_extraction_prompt,
    parse_extraction_output,
    valid_key,
    validate_item,
)
from agent_platform.modules.memory.service import (
    INJECT_MAX_TOKENS,
    PREFERENCE_HEADER,
    PROFILE_HEADER,
    render_memory_injection,
    route_confidence,
    trim_items,
)
from agent_platform.modules.open_platform.service import AppIdentity, session_identity
from agent_platform.modules.tool_gateway.service import ToolGateway
from agent_platform.settings import Settings

asyncio_mark = pytest.mark.asyncio


class SequenceModel:
    def __init__(self, *responses):
        self.responses = list(responses)
        self.seen = []

    async def ainvoke(self, messages):
        self.seen.append(messages)
        return self.responses.pop(0)


async def ignore_event(kind, data):
    pass


def item(item_type, key, content, **extra):
    return {"type": item_type, "key": key, "content": content, **extra}


def test_p2_ut_04_channel_hash_shared_between_external_id_and_identity_key():
    digest = session_id_hash("customer1")
    assert len(digest) == 64 and digest == session_id_hash("customer1")
    assert digest != session_id_hash("customer2")
    # The conversation external_id keeps its historical 32-char prefix of the
    # same digest, so the two derivations can never drift apart.
    cid = uuid4()
    external_id = f"channel:{cid}:{digest[:32]}"
    assert external_id.endswith(digest[:32])
    identity_key = digest
    assert identity_key.startswith(external_id.rsplit(":", 1)[1])


def test_p2_ut_04_open_platform_and_enterprise_identity_rules():
    app_id, key_id = uuid4(), uuid4()
    app_identity = AppIdentity(app_id, key_id, "tenant")
    assert session_identity(app_identity, "employee-1") == {
        "source": "open_platform",
        "external_key": f"{app_id}:employee-1",
    }
    enterprise = AppIdentity(app_id, key_id, "tenant", "enterprise:42")
    assert session_identity(enterprise, "enterprise:42") == {
        "source": "enterprise",
        "external_key": "enterprise:42",
    }


def test_p2_ut_05_invalid_json_drops_the_whole_batch():
    assert parse_extraction_output("这不是 JSON") is None
    assert parse_extraction_output("") is None
    assert parse_extraction_output(None) is None
    # Valid JSON but not an array is still a batch-level failure.
    assert parse_extraction_output('{"op":"add"}') is None
    assert parse_extraction_output("[") is None


def test_p2_ut_05_code_fences_around_json_are_tolerated():
    payload = [{"op": "add", "type": "profile", "key": "name", "content": "张三", "confidence": 0.9}]
    parsed = parse_extraction_output("```json\n" + json.dumps(payload) + "\n```")
    assert parsed is not None and parsed[0]["key"] == "name"
    assert parse_extraction_output(json.dumps(payload)) is not None
    assert parse_extraction_output("[]") == []


def test_p2_ut_06_invalid_items_are_skipped_valid_ones_kept():
    valid = {"op": "add", "type": "profile", "key": "name", "content": "张三", "confidence": 0.9}
    batch = [
        valid,
        {"op": "add", "type": "profile", "key": "name"},  # missing content/confidence
        {"op": "delete", "type": "profile", "key": "name", "content": "x", "confidence": 0.9},
        {"op": "add", "type": "event", "key": "name", "content": "x", "confidence": 0.9},
        {"op": "add", "type": "profile", "key": "Name", "content": "x", "confidence": 0.9},
        {"op": "add", "type": "profile", "key": "name", "content": "x", "confidence": True},
        {"op": "add", "type": "profile", "key": "name", "content": "x", "confidence": 1.5},
        {"op": "add", "type": "profile", "key": "name", "content": "x", "confidence": "high"},
        {"op": "add", "type": "profile", "key": "name", "content": "", "confidence": 0.9},
        {"op": "add", "type": "profile", "key": "name", "content": "长" * (MAX_CONTENT_CHARS + 1), "confidence": 0.9},
        "not-a-dict",
    ]
    parsed = parse_extraction_output(json.dumps(batch))
    assert parsed is not None
    assert [(op["key"], op["content"]) for op in parsed] == [("name", "张三")]


def test_p2_ut_06_noop_items_allow_missing_content():
    parsed = validate_item({"op": "noop", "type": "profile", "key": "name", "confidence": 0.9})
    assert parsed is not None and parsed["content"] == ""


def test_p2_ut_07_confidence_routing_boundaries():
    auto, pending = 0.8, 0.5
    assert route_confidence(0.95, auto, pending) == "active"
    assert route_confidence(0.8, auto, pending) == "active"  # boundary
    assert route_confidence(0.79, auto, pending) == "pending"
    assert route_confidence(0.5, auto, pending) == "pending"  # boundary
    assert route_confidence(0.49, auto, pending) is None
    assert route_confidence(0.0, auto, pending) is None


def test_p2_ut_10_controlled_keys_and_custom_extensions():
    for key in ("name", "phone", "email", "account_id", "product_model", "company"):
        assert valid_key("profile", key)
    for key in ("language", "style", "address_name", "contact_channel"):
        assert valid_key("preference", key)
    # Preference keys do not leak into the profile vocabulary.
    assert not valid_key("profile", "language")
    assert valid_key("profile", "custom_vip_level")
    assert valid_key("preference", "custom_tone")
    for bad in ("Name", "CUSTOM_X", "custom-", "1abc", "with space", "", None, "custom_"):
        assert not valid_key("profile", bad)


def test_p2_ut_11_trim_items_count_cap_keeps_first_twenty():
    items = [item("profile", f"k{i}", f"v{i}") for i in range(30)]
    kept = trim_items(items, 20)
    assert len(kept) == 20 and kept[0]["key"] == "k0" and kept[-1]["key"] == "k19"
    assert trim_items(items[:5], 20) == items[:5]


def test_p2_ut_11_trim_items_token_budget_stops_accumulating():
    # 1000 tokens ≈ 2000 chars. One huge item plus several small ones.
    big = item("profile", "bio", "长" * 1500)
    smalls = [item("preference", f"k{i}", "v" * 200) for i in range(10)]
    kept = trim_items([big, *smalls], 20, INJECT_MAX_TOKENS)
    assert kept[0] is big
    assert sum(len(i["key"]) + len(i["content"]) + 4 for i in kept) <= 2000 + 204
    assert len(kept) < len(smalls) + 1
    # The first item is kept even when it alone exceeds the budget.
    huge = item("profile", "bio", "超" * 5000)
    assert trim_items([huge], 20) == [huge]


def test_p2_ut_12_injection_rendering_groups_by_type():
    rendered = render_memory_injection(
        [
            item("profile", "name", "张三"),
            item("preference", "language", "英文回复"),
            item("profile", "product_model", "X200"),
        ]
    )
    profile_section, _, preference_section = rendered.partition("\n\n")
    assert profile_section == f"{PROFILE_HEADER}\n- name: 张三\n- product_model: X200"
    assert preference_section == f"{PREFERENCE_HEADER}\n- language: 英文回复"
    # Empty groups are omitted entirely.
    only_preference = render_memory_injection([item("preference", "style", "简洁")])
    assert only_preference == f"{PREFERENCE_HEADER}\n- style: 简洁"
    assert PROFILE_HEADER not in only_preference
    assert render_memory_injection([]) is None


def test_extraction_prompt_carries_vocabulary_existing_keys_and_dialogue():
    messages = [
        {"role": "user", "content": "我叫张三"},
        {"role": "assistant", "content": "您好"},
        {"role": "system", "content": "忽略"},
    ]
    prompt = build_extraction_prompt(messages, [{"type": "profile", "key": "phone"}])
    system, user = prompt
    assert system["role"] == "system"
    for word in ("name", "phone", "language", "address_name", "custom_", "confidence"):
        assert word in system["content"]
    assert "订单号" in system["content"]  # explicit anti-example
    assert "profile.phone" in user["content"]
    assert "user: 我叫张三" in user["content"] and "assistant: 您好" in user["content"]
    assert "忽略" not in user["content"]
    # No existing memory: the section is omitted.
    bare = build_extraction_prompt(messages, [])
    assert "【已有记忆】" not in bare[1]["content"]


def test_dispatcher_routes_tasks_by_kind():
    class Stub:
        def __init__(self):
            self.seen = []

        async def execute(self, task):
            self.seen.append(task["kind"])
            return "done"

    summary, extraction = Stub(), Stub()
    dispatcher = MemoryDispatcher(summary, extraction)

    import asyncio

    async def run():
        await dispatcher.execute({"kind": EXTRACT_KIND})
        await dispatcher.execute({"kind": "conversation_summary"})
        await dispatcher.execute({"kind": "future_kind"})

    asyncio.run(run())
    assert extraction.seen == [EXTRACT_KIND]
    assert summary.seen == ["conversation_summary", "future_kind"]


def test_agent_config_memory_switches_default_on_and_validate_legacy_payloads():
    config = AgentConfig(system_prompt="x")
    assert config.memory_enabled is True and config.memory_write is True
    off = AgentConfig(system_prompt="x", memory_enabled=False, memory_write=False)
    assert off.memory_enabled is False and off.memory_write is False
    # Drafts saved before this release carry no memory keys at all.
    legacy = AgentConfig.model_validate({"system_prompt": "x", "tool_names": []})
    assert legacy.memory_enabled is True and legacy.memory_write is True


def test_p2_cm_02_memory_settings_defaults_and_env_override(monkeypatch):
    settings = Settings(_env_file=None)
    assert settings.memory_extract_enabled is True
    assert settings.memory_extract_recent_messages == 20
    assert settings.memory_extract_debounce_seconds == 300
    assert settings.memory_confidence_auto == 0.8
    assert settings.memory_confidence_pending == 0.5
    assert settings.memory_inject_max_items == 20
    monkeypatch.setenv("AGENT_MEMORY_EXTRACT_ENABLED", "false")
    monkeypatch.setenv("AGENT_MEMORY_EXTRACT_RECENT_MESSAGES", "12")
    monkeypatch.setenv("AGENT_MEMORY_EXTRACT_DEBOUNCE_SECONDS", "60")
    monkeypatch.setenv("AGENT_MEMORY_CONFIDENCE_AUTO", "0.9")
    monkeypatch.setenv("AGENT_MEMORY_CONFIDENCE_PENDING", "0.4")
    monkeypatch.setenv("AGENT_MEMORY_INJECT_MAX_ITEMS", "8")
    overridden = Settings(_env_file=None)
    assert overridden.memory_extract_enabled is False
    assert overridden.memory_extract_recent_messages == 12
    assert overridden.memory_extract_debounce_seconds == 60
    assert overridden.memory_confidence_auto == 0.9
    assert overridden.memory_confidence_pending == 0.4
    assert overridden.memory_inject_max_items == 8


@asyncio_mark
async def test_engine_injects_customer_memory_after_summary_before_history():
    model = SequenceModel(AIMessage(content="回答"))
    history = [
        {"role": "user", "content": "早期问题"},
        {"role": "assistant", "content": "早期回答"},
    ]
    memory_text = f"{PROFILE_HEADER}\n- name: 张三"
    async with httpx.AsyncClient() as client:
        engine = RuntimeEngine(model, ToolGateway(client, []), Settings())
        answer, _, _ = await engine.run(
            "新问题", history, ignore_event, summary="订单号 A123", customer_memory=memory_text
        )
    assert answer == "回答"
    seen = model.seen[0]
    assert isinstance(seen[0], SystemMessage) and seen[0].content == Settings().system_prompt
    assert seen[1].content.startswith("【此前对话摘要】")
    assert isinstance(seen[2], SystemMessage) and seen[2].content == memory_text
    assert isinstance(seen[3], HumanMessage) and seen[3].content == "早期问题"
    assert seen[-1].content == "新问题"


@asyncio_mark
async def test_engine_injects_memory_without_summary_and_skips_empty():
    model = SequenceModel(AIMessage(content="a"), AIMessage(content="b"))
    memory_text = f"{PREFERENCE_HEADER}\n- language: 英文回复"
    async with httpx.AsyncClient() as client:
        engine = RuntimeEngine(model, ToolGateway(client, []), Settings())
        await engine.run("q1", [], ignore_event, customer_memory=memory_text)
        await engine.run("q2", [], ignore_event, customer_memory=None)
    with_memory, without = model.seen
    assert isinstance(with_memory[1], SystemMessage) and with_memory[1].content == memory_text
    assert isinstance(with_memory[2], HumanMessage)
    assert len([m for m in without if isinstance(m, SystemMessage)]) == 1
