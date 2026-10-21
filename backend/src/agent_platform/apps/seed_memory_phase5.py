"""Phase 5 (Agent 记忆配置开关) acceptance demo dataset; safe to run again.

The protagonists here are six demo *agents*, one per memory-config quadrant,
each published with a distinct ``AgentConfig`` memory block so the console's
agent list/config page shows the full Phase 5 configuration surface:

- 记忆全开助手   — all defaults (read+write+all types+search): baseline run
  with the genuine 7-event sequence (memory.injected + memory_extract.queued).
- 只读记忆助手   — ``memory_write=false``: injects but never dispatches
  extraction, so its run has ``memory.injected`` but no
  ``memory_extract.queued`` (6 events, exactly what repository.finish emits).
- 记忆关闭助手   — ``memory_enabled=false`` master switch: the bound customer
  has rich memory, yet the run carries no ``memory.*`` events at all and no
  ``memory_injection_log`` rows (5 events).
- 人工审核记忆助手 — ``memory_write_mode="review"``: extraction still queued,
  but the extracted item lands in ``status='pending'`` on the customer and is
  never injected until confirmed (A4).
- 精简注入助手   — ``memory_inject_max_items=2``: on the same memory-rich
  customer the ``memory.injected`` counts shrink to items=2/events≤2, and an
  ``agent.updated`` audit row shows the config change with before/after
  values (A11/C3).
- 画像限定助手   — ``memory_types=["profile"]``: only profile items appear in
  ``memory.injected``/``memory_injection_log`` even though the customer owns
  preference/event items too (A5).

Two demo customers (孙明轩 / 周雅婷) carry the memory those agents read;
every conversation/run/event row follows the genuine shapes emitted by
``agent_runtime`` (see worker._customer_memory / repository.finish).
"""

from agent_platform.apps.seed_memory_common import (
    REPLY,
    current_audit_actor,
    ensure_agent,
    identity_bound,
    insert_audit,
    insert_conversation,
    insert_customer,
    insert_identity,
    insert_item,
    insert_run,
    item,
    ns,
    pairs,
    run_standalone,
    uid,
)
from agent_platform.modules.agent_config.service import AgentConfig
from agent_platform.platform.persistence.store import one

NS = ns("phase5")

# ---------------------------------------------------------------------------
# Demo agents: one per memory-config quadrant.
# ---------------------------------------------------------------------------

AGENTS = [
    {
        "key": "full",
        "name": "记忆全开助手",
        "description": "记忆配置全开（默认值）：读取+写入+全类型+检索。预期行为：run 事件包含 memory.injected（注入全部可用记忆）与 memory_extract.queued（自动抽取），与阶段 1-4 行为逐位一致（存量等价基线）。",
        "config": {},
    },
    {
        "key": "readonly",
        "name": "只读记忆助手",
        "description": "memory_write=false（只读不写）：既有客户记忆照常注入，会话结束不派发抽取任务、不产生新记忆。预期行为：run 有 memory.injected，无 memory_extract.queued，事件共 6 条。",
        "config": {"memory_write": False},
    },
    {
        "key": "off",
        "name": "记忆关闭助手",
        "description": "memory_enabled=false（总开关关闭）：不注入、不检索、不抽取，子项在配置页置灰。预期行为：即使绑定客户有丰富记忆，run 也不出现任何 memory.* 事件、无注入日志，事件仅 5 条。",
        "config": {"memory_enabled": False},
    },
    {
        "key": "review",
        "name": "人工审核记忆助手",
        "description": "memory_write_mode=review（人工审核写入）：注入正常、抽取任务照常派发，但抽取结果全部进入待确认队列。预期行为：客户记忆出现 status=pending 条目，确认前不参与注入；审计 pending 计数大于 0。",
        "config": {"memory_write_mode": "review"},
    },
    {
        "key": "lean",
        "name": "精简注入助手",
        "description": "memory_inject_max_items=2（精简注入）：画像/偏好每次最多注入 2 条，事件上限同步收紧为 min(2, 平台事件上限)。预期行为：同一客户下 memory.injected 计数收缩为 items=2、events≤2，与记忆全开助手形成对比。",
        "config": {"memory_inject_max_items": 2},
    },
    {
        "key": "profileonly",
        "name": "画像限定助手",
        "description": "memory_types=['profile']（仅客户画像）：仅 profile 参与注入与写入，preference/event 不注入、不写入。预期行为：memory.injected 仅含画像条目（events=0），注入日志只有 profile 段，库中偏好/事件条目保持不变。",
        "config": {"memory_types": ["profile"]},
    },
]

SYSTEM_PROMPT = "你是一名企业客服助手。语气自然简洁，先回应客户处境再给方案，一次推进一件事。"

# ---------------------------------------------------------------------------
# Demo customers and their memory items.
# ---------------------------------------------------------------------------

SUN_EXT = "demo-p5-sunmingxuan"
ZHOU_EXT = "demo-p5-zhouyating"

SUN_ITEMS = [
    item("profile", "company", "恒信物流集团有限公司", .97, st="manual", rc=10, ch=20 * 24, uh=48),
    item("profile", "industry", "大宗物流与仓储（华东区域，自营 3 个仓库）", .94, sr=f"{SUN_EXT}:full", rc=6, ch=19 * 24, uh=72),
    item("profile", "role", "供应链总监，负责仓配采购与服务商评估", .93, sr=f"{SUN_EXT}:full", rc=4, ch=18 * 24, uh=96),
    item("profile", "tier", "银牌客户（季度框架合同）", .96, st="manual", rc=8, ch=15 * 24, uh=24),
    item("preference", "contact_channel", "工作日优先企业微信沟通，节假日紧急事项电话", .9, sr=f"{SUN_EXT}:full", rc=5, ch=14 * 24, uh=24),
    item("preference", "report", "每月 5 日前提供上月运输对账月报", .88, st="manual", rc=3, ch=12 * 24, uh=48),
    item("event", "open_ticket:TK-20261020", "华东仓配一体化方案报价已发送，等待客户法务审核（预计 10-25 前反馈）", .92, st="manual", sr=f"{SUN_EXT}:full", rc=2, ch=30, uh=30),
    item("event", "commitment:2026-10-21", "承诺 10-28 前完成上海仓扩容方案测算并同步客户", .9, sr=f"{SUN_EXT}:full", rc=1, ch=28, uh=28),
    item("flag", "vip", "重点客户：合同 2026-12-31 到期，11 月启动续约沟通，报价方案需提前内部评审", 1.0, st="human", prot=True, ch=96, uh=96),
]

ZHOU_ITEMS = [
    item("profile", "company", "悦读教育科技有限公司", .95, st="manual", rc=4, ch=8 * 24, uh=48),
    item("profile", "team_size", "团队约 40 人，以教研与内容团队为主", .9, sr=f"{ZHOU_EXT}:review", rc=2, ch=8 * 24, uh=72),
    item("preference", "contact_channel", "偏好邮件沟通，重要事项抄送其助理", .88, sr=f"{ZHOU_EXT}:review", rc=3, ch=7 * 24, uh=24),
    item("preference", "budget", "年度 SaaS 采购预算约 8 万元", .85, sr=f"{ZHOU_EXT}:review", rc=1, ch=6 * 24, uh=72),
    item("event", "evt-202610-trial", "2026-10 完成企业版 14 天试用，教研团队反馈良好，进入采购评估", .9, sr=f"{ZHOU_EXT}:review", rc=1, ch=5 * 24, uh=5 * 24),
    # review 模式抽取结果：全部进入待确认队列，确认前不参与注入（A4）。
    item("preference", "contact_time", "偏好下午 2 点后电话联系（待确认）", .6, status="pending", sr=f"{ZHOU_EXT}:review", ch=25, uh=25),
]


def run_events(run_key, cid, injected, write_on, reply=REPLY):
    """Genuine event sequence honouring the Phase 5 gates.

    ``repository.finish`` emits ``memory_extract.queued`` only when the write
    gate dispatched an extraction task, and ``memory.injected`` only when
    something was actually injected — a read-off run therefore ends at
    ``run.completed`` right after ``model.completed``.
    """
    run_id = uid(NS, "run", run_key)
    evs = [
        (1, "run.queued", {}),
        (2, "run.started", {}),
        (3, "model.started", {"round": 1}),
        (4, "model.completed", {"round": 1, "usage": {}, "tool_calls": 0, "duration_ms": 141,
                                "first_token_ms": 38, "gateway_call_id": uid(NS, "gw", run_id)}),
    ]
    seq = 5
    if write_on and cid:
        evs.append((seq, "memory_extract.queued", {"customer_id": cid}))
        seq += 1
    if injected:
        counts = {"flags": sum(1 for _, s, _ in injected if s == "flag"),
                  "items": sum(1 for _, s, _ in injected if s in ("profile", "preference")),
                  "events": sum(1 for _, s, _ in injected if s == "event")}
        evs.append((seq, "memory.injected", {**counts, "item_ids": [iid for iid, _, _ in injected]}))
        seq += 1
    evs.append((seq, "run.completed", {"output": reply, "error_code": None}))
    return evs


async def seed(runtime, settings) -> bool:
    tenant = settings.tenant_id
    p = runtime.platform
    audit_actor = await current_audit_actor(p, tenant)
    inserted = False

    # --- demo agents (idempotent by name via ensure_agent) ------------------
    agents = {}
    existing = {a["name"] for a in await p.agents.list(tenant)}
    for spec in AGENTS:
        if spec["name"] not in existing:
            inserted = True
        config = AgentConfig(system_prompt=SYSTEM_PROMPT, tool_names=[], knowledge_base_ids=[], **spec["config"])
        agents[spec["key"]] = await ensure_agent(p, tenant, "seed", spec["name"], spec["description"], config)

    async with p.engine.begin() as conn:
        # A11/C3：配置变更审计（操作人 + 变更前后值），按审计行自身存在性判重，
        # 与 Agent 创建解耦（两者不在同一事务，避免部分失败后审计永久缺失）。
        lean_audit = await one(
            conn,
            "SELECT id FROM audit_records WHERE tenant_id=:t AND action='agent.updated'"
            " AND resource=:r AND details->'changes' ? 'memory_inject_max_items'",
            t=tenant, r=str(agents["lean"]["id"]),
        )
        if not lean_audit:
            inserted = True
            await insert_audit(
                conn, tenant, audit_actor, "agent.updated", agents["lean"]["id"],
                {"revision": 2, "changes": {"memory_inject_max_items": {"from": None, "to": 2}}},
                20,
            )

        # --- 孙明轩：记忆丰富，供 全开/只读/关闭/精简 四个 Agent 对比 ----------
        if not await identity_bound(conn, tenant, "web", SUN_EXT):
            inserted = True
            cid = uid(NS, "customer", "sunmingxuan")
            iids = {f'{i["t"]}:{i["k"]}': uid(NS, "item", f"sun:{i['t']}:{i['k']}") for i in SUN_ITEMS}
            specs = {f'{i["t"]}:{i["k"]}': i for i in SUN_ITEMS}

            def sinj(keys):
                return [(iids[k], specs[k]["t"], specs[k]["conf"] if specs[k]["t"] in ("profile", "preference") else None)
                        for k in keys]

            await insert_customer(conn, tenant, cid, 20 * 24, 5)
            await insert_identity(conn, tenant, uid(NS, "identity", SUN_EXT), cid, SUN_EXT, 20 * 24)
            for i in SUN_ITEMS:
                await insert_item(conn, tenant, iids[f'{i["t"]}:{i["k"]}'], cid, i,
                                  last_referenced_h=5 if i["status"] == "active" else None)

            full_inj = sinj(["flag:vip", "profile:company", "profile:industry",
                             "profile:role", "profile:tier", "preference:contact_channel",
                             "preference:report", "event:open_ticket:TK-20261020",
                             "event:commitment:2026-10-21"])
            full_inj2 = sinj(["flag:vip", "profile:company", "profile:tier",
                              "event:open_ticket:TK-20261020"])
            readonly_inj = sinj(["flag:vip", "profile:company", "profile:industry",
                                 "preference:contact_channel"])
            lean_inj = sinj(["flag:vip", "profile:company", "profile:tier",
                             "event:open_ticket:TK-20261020", "event:commitment:2026-10-21"])

            conv_full = uid(NS, "conversation", f"{SUN_EXT}:full")
            await insert_conversation(
                conn, tenant, conv_full, f"{SUN_EXT}:full",
                pairs(["你好，我是恒信物流的孙明轩，华东仓配一体化的报价方案进展怎么样了？",
                       "上海仓扩容的测算别忘了，月底前我们要内部评审。"]),
                30.1, 5, agents["full"]["id"],
                summary="客户孙明轩（恒信物流，银牌客户）：跟进华东仓配一体化报价与上海仓扩容测算；合同 2026-12-31 到期，11 月启动续约。",
                cid=cid)
            await insert_run(conn, tenant, NS, "sun:full:0", conv_full, agents["full"],
                             settings.model_backend,
                             "你好，我是恒信物流的孙明轩，华东仓配一体化的报价方案进展怎么样了？",
                             30, cid=cid, injected=full_inj)
            await insert_run(conn, tenant, NS, "sun:full:1", conv_full, agents["full"],
                             settings.model_backend,
                             "上海仓扩容的测算别忘了，月底前我们要内部评审。",
                             5, cid=cid, injected=full_inj2)

            conv_ro = uid(NS, "conversation", f"{SUN_EXT}:readonly")
            await insert_conversation(
                conn, tenant, conv_ro, f"{SUN_EXT}:readonly",
                pairs(["运输对账月报还是按老时间发就行。"]), 12.1, 12, agents["readonly"]["id"], cid=cid)
            await insert_run(conn, tenant, NS, "sun:readonly:0", conv_ro, agents["readonly"],
                             settings.model_backend, "运输对账月报还是按老时间发就行。",
                             12, cid=cid, injected=readonly_inj,
                             events=run_events("sun:readonly:0", cid, readonly_inj, write_on=False))

            conv_off = uid(NS, "conversation", f"{SUN_EXT}:off")
            await insert_conversation(
                conn, tenant, conv_off, f"{SUN_EXT}:off",
                pairs(["在吗？想问下报价的事。"]), 8.1, 8, agents["off"]["id"], cid=cid)
            await insert_run(conn, tenant, NS, "sun:off:0", conv_off, agents["off"],
                             settings.model_backend, "在吗？想问下报价的事。",
                             8, cid=cid,
                             events=run_events("sun:off:0", cid, [], write_on=False))

            conv_lean = uid(NS, "conversation", f"{SUN_EXT}:lean")
            await insert_conversation(
                conn, tenant, conv_lean, f"{SUN_EXT}:lean",
                pairs(["报价方案和扩容测算一起催一下，谢谢。"]), 6.1, 6, agents["lean"]["id"], cid=cid)
            await insert_run(conn, tenant, NS, "sun:lean:0", conv_lean, agents["lean"],
                             settings.model_backend, "报价方案和扩容测算一起催一下，谢谢。",
                             6, cid=cid, injected=lean_inj)

            await insert_audit(
                conn, tenant, "memory", "memory.extract.applied", cid,
                {"conversation_id": f"{SUN_EXT}:full", "input_messages": 4, "added": 2,
                 "updated": 1, "pending": 0, "dropped": 0, "pii_masked": 0, "usage": {}},
                29)

        # --- 周雅婷：供 画像限定/人工审核 两个 Agent 演示 ----------------------
        if not await identity_bound(conn, tenant, "web", ZHOU_EXT):
            inserted = True
            cid = uid(NS, "customer", "zhouyating")
            iids = {f'{i["t"]}:{i["k"]}': uid(NS, "item", f"zhou:{i['t']}:{i['k']}") for i in ZHOU_ITEMS}
            specs = {f'{i["t"]}:{i["k"]}': i for i in ZHOU_ITEMS}

            def zinj(keys):
                return [(iids[k], specs[k]["t"], specs[k]["conf"] if specs[k]["t"] in ("profile", "preference") else None)
                        for k in keys]

            await insert_customer(conn, tenant, cid, 8 * 24, 3)
            await insert_identity(conn, tenant, uid(NS, "identity", ZHOU_EXT), cid, ZHOU_EXT, 8 * 24)
            for i in ZHOU_ITEMS:
                await insert_item(conn, tenant, iids[f'{i["t"]}:{i["k"]}'], cid, i,
                                  last_referenced_h=3 if i["status"] == "active" else None)

            conv_po = uid(NS, "conversation", f"{ZHOU_EXT}:profileonly")
            await insert_conversation(
                conn, tenant, conv_po, f"{ZHOU_EXT}:profileonly",
                pairs(["你好，我是悦读教育的周雅婷，想确认下企业版报价。"]), 10.1, 10, agents["profileonly"]["id"], cid=cid)
            po_inj = zinj(["profile:company", "profile:team_size"])
            await insert_run(conn, tenant, NS, "zhou:profileonly:0", conv_po, agents["profileonly"],
                             settings.model_backend, "你好，我是悦读教育的周雅婷，想确认下企业版报价。",
                             10, cid=cid, injected=po_inj)

            conv_rv = uid(NS, "conversation", f"{ZHOU_EXT}:review")
            await insert_conversation(
                conn, tenant, conv_rv, f"{ZHOU_EXT}:review",
                pairs(["我们试用期结束了，整体很满意，想聊聊正式采购。",
                       "后续联系我的话，尽量下午两点以后打电话。"]),
                26.1, 3, agents["review"]["id"], cid=cid)
            rv_inj = zinj(["profile:company", "profile:team_size", "preference:contact_channel",
                           "preference:budget", "event:evt-202610-trial"])
            await insert_run(conn, tenant, NS, "zhou:review:0", conv_rv, agents["review"],
                             settings.model_backend, "我们试用期结束了，整体很满意，想聊聊正式采购。",
                             26, cid=cid, injected=rv_inj)
            rv_inj2 = zinj(["profile:company", "preference:contact_channel"])
            await insert_run(conn, tenant, NS, "zhou:review:1", conv_rv, agents["review"],
                             settings.model_backend, "后续联系我的话，尽量下午两点以后打电话。",
                             3, cid=cid, injected=rv_inj2)

            # review 模式：抽取结果全部进待确认（added=0, pending=1）。
            await insert_audit(
                conn, tenant, "memory", "memory.extract.applied", cid,
                {"conversation_id": f"{ZHOU_EXT}:review", "input_messages": 3, "added": 0,
                 "updated": 0, "pending": 1, "dropped": 0, "pii_masked": 0, "usage": {}},
                25)

    return inserted


if __name__ == "__main__":
    run_standalone(seed, "阶段5Agent记忆配置演示数据")
