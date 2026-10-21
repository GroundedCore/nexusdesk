"""Phase 2 (客户画像自动抽取) acceptance demo dataset; safe to run again.

Boots the console with the extraction lifecycle visible end to end:

- 陈国栋（恒瑞机械）：首次进线自报姓名/电话/联系偏好 → ``memory.extract.applied``
  审计 added=5 且 pii_masked=1（手机号落库为掩码形态）；二次进线"改口叫陈工"
  → preference.address_name 单条 update（prev_content 保留、revision=2，不堆叠）；
  控制台人工编辑 preference.style → source_type=human / protected / confidence=1.0，
  后续 run 注入编辑后内容（A1/A2/A4/A7）。
- 林晓芸（云朵母婴）：低置信条目进入 pending 队列 → 控制台 confirm（随后注入出现）
  与 reject（随后永不注入）全路径，另留一条仍在 pending 的建议（A3）。
- 黄志强（远大物流）：Agent 读写开关演示——memory_write 关闭的 run 无
  memory_extract.queued 事件，memory_enabled 关闭的 run 有抽取事件但无注入；
  以及一次 dropped=1 的"宁缺毋滥"低置信丢弃（A8 / 抽取防御）。

确定性 uuid5 id；按客户块的 identity 绑定做幂等，重复运行不插重。
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

NSP = ns("phase2")
AGENT_NAME = "画像演示助手"
AGENT_DESC = "阶段 2 客户画像自动抽取演示（抽取落库、改口更新、pending 审核、读写开关）"
SYSTEM_PROMPT = "你是一名企业客服助手。语气自然简洁，先回应客户处境再给方案，一次推进一件事。"

# 受控词表（extraction.CONTROLLED_KEYS）：profile 用 name/phone/company 等，
# preference 用 language/style/address_name/contact_channel；custom_* 为扩展键。


def run_events(run_key, cid, injected, extract=True):
    """Genuine event sequence for one completed run.

    ``memory.injected`` is only emitted when something was actually injected
    (the runtime drops the key otherwise); ``memory_extract.queued`` is absent
    for runs dispatched while the agent's memory_write switch was off.
    """
    injected = injected or []
    counts = {"flags": sum(1 for _, sec, _ in injected if sec == "flag"),
              "items": sum(1 for _, sec, _ in injected if sec in ("profile", "preference")),
              "events": sum(1 for _, sec, _ in injected if sec == "event")}
    events = [
        (1, "run.queued", {}),
        (2, "run.started", {}),
        (3, "model.started", {"round": 1}),
        (4, "model.completed", {"round": 1, "usage": {}, "tool_calls": 0, "duration_ms": 141,
                                "first_token_ms": 38, "gateway_call_id": uid(NSP, "gw", run_key)}),
    ]
    if extract and cid:
        events.append((5, "memory_extract.queued", {"customer_id": str(cid)}))
    if injected:
        events.append((len(events) + 1, "memory.injected",
                       {**counts, "item_ids": [iid for iid, _, _ in injected]}))
    events.append((len(events) + 1, "run.completed", {"output": REPLY, "error_code": None}))
    return events


def R(key, h, msg, injected, extract=True):
    """One run spec: message, anchor hours, injected items, extract-switch state."""
    return {"key": key, "h": h, "msg": msg, "injected": injected, "extract": extract}


def extract_audit(conv_id, input_messages, **counts):
    """``memory.extract.applied`` details — exactly what ExtractionService emits."""
    base = {"added": 0, "updated": 0, "pending": 0, "dropped": 0, "noop": 0, "pii_masked": 0}
    base.update(counts)
    return {"conversation_id": str(conv_id), "input_messages": input_messages,
            **base, "usage": {}}


async def seed(runtime, settings) -> bool:
    p = runtime.platform
    tenant = settings.tenant_id
    agent = await ensure_agent(
        p, tenant, "seed", AGENT_NAME, AGENT_DESC,
        AgentConfig(system_prompt=SYSTEM_PROMPT, tool_names=[], knowledge_base_ids=[]),
    )
    audit_actor = await current_audit_actor(p, tenant)
    seeded = 0
    seeded += await _seed_chen(p, tenant, settings.model_backend, agent, audit_actor)
    seeded += await _seed_lin(p, tenant, settings.model_backend, agent, audit_actor)
    seeded += await _seed_huang(p, tenant, settings.model_backend, agent, audit_actor)
    return seeded > 0


async def _seed_chen(p, tenant, model_backend, agent, audit_actor) -> int:
    """陈国栋：首次建档 + 累积注入 + 改口 update（A1/A2/A7）+ 人工编辑（A4）+ PII 掩码。"""
    ext = "demo-p2-chen-guodong"
    async with p.engine.connect() as conn:
        if await identity_bound(conn, tenant, "web", ext):
            return 0
    cid = uid(NSP, "customer", "chen-guodong")
    conv = uid(NSP, "conversation", ext)
    iid = {k: uid(NSP, "item", f"chen:{k}") for k in (
        "profile:company", "profile:name", "profile:phone",
        "preference:address_name", "preference:contact_channel", "preference:style")}
    prof, pref = "profile", "preference"
    items = [
        (iid["profile:company"], item(prof, "company", "恒瑞机械设备有限公司", .96, sr=str(conv), rc=3, ch=70, uh=70)),
        (iid["profile:name"], item(prof, "name", "陈国栋", .98, sr=str(conv), rc=2, ch=70, uh=70)),
        # 手机号经 PII 掩码后落库（审计 pii_masked=1 对应此条）。
        (iid["profile:phone"], item(prof, "phone", "138****6677", .95, sr=str(conv), rc=2, ch=70, uh=70)),
        # “其实叫我陈工就行”：同键 update，prev_content 保留编辑前值，revision=2，不堆叠。
        (iid["preference:address_name"], item(pref, "address_name", "陈工", .93, sr=str(conv),
                                              prev="陈国栋", rev=2, rc=2, ch=70, uh=48)),
        (iid["preference:contact_channel"], item(pref, "contact_channel", "报价单与合同请发采购部邮箱",
                                                 .9, sr=str(conv), rc=1, ch=70, uh=70)),
        # 控制台人工编辑（A4）：source_type=human、protected、confidence=1.0、revision=2。
        (iid["preference:style"], item(pref, "style", "回复简短直接，结论先行，重要事项分点列出",
                                       1.0, st="human", sr=audit_actor, prot=True,
                                       prev="回复简短直接，结论先行", rev=2, rc=1, ch=48, uh=24)),
    ]
    inj = lambda *keys: [(iid[k], k.split(":")[0], 0.9) for k in keys]
    runs = [
        # 首次进线：尚无可注入记忆（无 memory.injected 事件），抽取落库 5 条。
        R(key="chen:1", h=70, extract=True,
             msg="你好，我是恒瑞机械设备的陈国栋，手机号 13866676677，以后报价单请发采购部邮箱。",
             injected=[]),
        # 二次进线：改口"陈工"+ 要求简短回复；注入首轮累积画像（A2）。
        R(key="chen:2", h=48, extract=True, msg="以后叫我陈工就行，回复尽量简短直接。",
             injected=inj("profile:name", "profile:company", "profile:phone", "preference:address_name")),
        # 三次进线：注入含人工编辑后的 style（A4）。
        R(key="chen:3", h=6, extract=True, msg="在吗？上周说的减速机报价单发我一下。",
             injected=inj("profile:name", "profile:company", "profile:phone",
                          "preference:address_name", "preference:contact_channel", "preference:style")),
    ]
    audits = [
        ("memory", "memory.extract.applied", cid, 70,
         extract_audit(conv, 2, added=5, pii_masked=1)),
        ("memory", "memory.extract.applied", cid, 48,
         extract_audit(conv, 4, added=1, updated=1, noop=1)),
        (audit_actor, "memory.item.edited", iid["preference:style"], 24,
         {"customer_id": str(cid), "type": "preference", "key": "style", "pii_masked": 0}),
        ("memory", "memory.extract.applied", cid, 6,
         extract_audit(conv, 6, noop=4)),
    ]
    async with p.engine.begin() as conn:
        await insert_customer(conn, tenant, cid, created_h=70, updated_h=6)
        await insert_identity(conn, tenant, uid(NSP, "identity", ext), cid, ext, created_h=70)
        for item_id, spec in items:
            await insert_item(conn, tenant, item_id, cid, spec, last_referenced_h=6)
        await insert_conversation(conn, tenant, conv, ext, pairs([r["msg"] for r in runs]),
                                  created_h=70.1, updated_h=6, agent_id=agent["id"], cid=cid)
        for r in runs:
            await insert_run(conn, tenant, NSP, r["key"], conv, agent, model_backend, r["msg"],
                             r["h"], cid=cid, injected=r["injected"],
                             events=run_events(r["key"], cid, r["injected"], extract=r["extract"]))
        for actor, action, resource, hours, details in audits:
            await insert_audit(conn, tenant, actor, action, resource, details, hours)
    return 1


async def _seed_lin(p, tenant, model_backend, agent, audit_actor) -> int:
    """林晓芸：低置信 pending 队列 → 控制台 confirm 后注入 / reject 后永不注入（A3）。"""
    ext = "demo-p2-lin-xiaoyun"
    async with p.engine.connect() as conn:
        if await identity_bound(conn, tenant, "web", ext):
            return 0
    cid = uid(NSP, "customer", "lin-xiaoyun")
    conv = uid(NSP, "conversation", ext)
    iid = {k: uid(NSP, "item", f"lin:{k}") for k in (
        "profile:company", "preference:language", "profile:custom_region", "preference:contact_channel")}
    items = [
        (iid["profile:company"], item("profile", "company", "云朵母婴连锁（佛山 6 家门店）",
                                      .92, sr=str(conv), rc=2, ch=50, uh=50)),
        # 抽取置信 0.62 → pending；控制台 confirm 后转 active，随后轮次开始注入。
        (iid["preference:language"], item("preference", "language", "希望用粤语沟通",
                                          .62, sr=str(conv), rc=1, ch=50, uh=40)),
        # 抽取置信 0.58 → pending；控制台 reject 后 deleted，注入永不再现。
        (iid["profile:custom_region"], item("profile", "custom_region", "门店主要分布在佛山禅城区（推测）",
                                            .58, status="deleted", sr=str(conv), ch=50, uh=40)),
        # 仍留在 pending 队列的低置信建议（控制台待审核列表可见）。
        (iid["preference:contact_channel"], item("preference", "contact_channel", "企业微信沟通",
                                                 .55, status="pending", sr=str(conv), ch=50, uh=50)),
    ]
    inj = lambda *keys: [(iid[k], k.split(":")[0], 0.62) for k in keys]
    runs = [
        R(key="lin:1", h=50, msg="我是云朵母婴的林晓芸，门店在佛山，之后方便的话想用粤语沟通。",
             injected=[]),
        # confirm 之后：language 条目进入注入。
        R(key="lin:2", h=30, msg="上次说的奶粉批次问题，帮我跟进下。",
             injected=inj("profile:company", "preference:language")),
        # reject 之后：custom_region 永不注入（注入列表中缺席即证明）。
        R(key="lin:3", h=8, msg="帮我把上个月的补货清单再发一份。",
             injected=inj("profile:company", "preference:language")),
    ]
    audits = [
        ("memory", "memory.extract.applied", cid, 50,
         extract_audit(conv, 2, added=1, pending=3)),
        (audit_actor, "memory.item.confirmed", iid["preference:language"], 40,
         {"customer_id": str(cid), "type": "preference", "key": "language",
          "old_status": "pending", "new_status": "active"}),
        (audit_actor, "memory.item.rejected", iid["profile:custom_region"], 39.5,
         {"customer_id": str(cid), "type": "profile", "key": "custom_region",
          "old_status": "pending", "new_status": "deleted"}),
        ("memory", "memory.extract.applied", cid, 30, extract_audit(conv, 4, noop=2)),
        ("memory", "memory.extract.applied", cid, 8, extract_audit(conv, 6, noop=2)),
    ]
    async with p.engine.begin() as conn:
        await insert_customer(conn, tenant, cid, created_h=50, updated_h=8)
        await insert_identity(conn, tenant, uid(NSP, "identity", ext), cid, ext, created_h=50)
        for item_id, spec in items:
            await insert_item(conn, tenant, item_id, cid, spec)
        await insert_conversation(conn, tenant, conv, ext, pairs([r["msg"] for r in runs]),
                                  created_h=50.1, updated_h=8, agent_id=agent["id"], cid=cid)
        for r in runs:
            await insert_run(conn, tenant, NSP, r["key"], conv, agent, model_backend, r["msg"],
                             r["h"], cid=cid, injected=r["injected"],
                             events=run_events(r["key"], cid, r["injected"]))
        for actor, action, resource, hours, details in audits:
            await insert_audit(conn, tenant, actor, action, resource, details, hours)
    return 1


async def _seed_huang(p, tenant, model_backend, agent, audit_actor) -> int:
    """黄志强：memory_write 关闭（无抽取事件）/ memory_enabled 关闭（有抽取无注入）（A8）。"""
    ext = "demo-p2-huang-zhiqiang"
    async with p.engine.connect() as conn:
        if await identity_bound(conn, tenant, "web", ext):
            return 0
    cid = uid(NSP, "customer", "huang-zhiqiang")
    conv = uid(NSP, "conversation", ext)
    iid = {k: uid(NSP, "item", f"huang:{k}") for k in (
        "profile:company", "preference:contact_channel", "profile:custom_fleet")}
    items = [
        (iid["profile:company"], item("profile", "company", "远大物流集团",
                                      .9, sr=str(conv), rc=1, ch=60, uh=60)),
        (iid["preference:contact_channel"], item("preference", "contact_channel", "只接受电话沟通，不接收邮件",
                                                 .85, sr=str(conv), rc=1, ch=60, uh=60)),
        # 最新一轮抽取产出的 pending 建议（推断信息，置信 0.6）。
        (iid["profile:custom_fleet"], item("profile", "custom_fleet", "自有车队约 80 辆（推断）",
                                           .6, status="pending", sr=str(conv), ch=10, uh=10)),
    ]
    inj = lambda *keys: [(iid[k], k.split(":")[0], 0.85) for k in keys]
    runs = [
        R(key="huang:1", h=60, extract=True,
             msg="你好，我是远大物流的黄志强，有事直接打电话，别发邮件。", injected=[]),
        # memory_write=false 窗口内的 run：completed 但无 memory_extract.queued 事件。
        R(key="huang:2", h=40, extract=False, msg="上周的干线运输合同续签的事怎么样了？",
             injected=inj("profile:company", "preference:contact_channel")),
        # memory_enabled=false 窗口内的 run：抽取照常投递（读关不影响写），但无注入。
        R(key="huang:3", h=26, extract=True, msg="在吗？帮我查下到杭州的运费。", injected=[]),
        # 恢复双开：注入回归；本轮抽取演示 dropped=1（低置信直接丢弃，宁缺毋滥）。
        R(key="huang:4", h=10, extract=True, msg="我们车队大概 80 辆车，华东线路都能跑。",
             injected=inj("profile:company", "preference:contact_channel")),
    ]
    audits = [
        ("memory", "memory.extract.applied", cid, 60, extract_audit(conv, 2, added=2)),
        # h=40 无抽取任务 → 无审计（写关的证明即审计缺席）。
        ("memory", "memory.extract.applied", cid, 26, extract_audit(conv, 4, noop=1)),
        ("memory", "memory.extract.applied", cid, 10,
         extract_audit(conv, 6, pending=1, dropped=1, noop=1)),
    ]
    async with p.engine.begin() as conn:
        await insert_customer(conn, tenant, cid, created_h=60, updated_h=10)
        await insert_identity(conn, tenant, uid(NSP, "identity", ext), cid, ext, created_h=60)
        for item_id, spec in items:
            await insert_item(conn, tenant, item_id, cid, spec)
        await insert_conversation(conn, tenant, conv, ext, pairs([r["msg"] for r in runs]),
                                  created_h=60.1, updated_h=10, agent_id=agent["id"], cid=cid)
        for r in runs:
            await insert_run(conn, tenant, NSP, r["key"], conv, agent, model_backend, r["msg"],
                             r["h"], cid=cid, injected=r["injected"],
                             events=run_events(r["key"], cid, r["injected"], extract=r["extract"]))
        for actor, action, resource, hours, details in audits:
            await insert_audit(conn, tenant, actor, action, resource, details, hours)
    return 1


if __name__ == "__main__":
    run_standalone(seed, "阶段2客户画像演示数据")
