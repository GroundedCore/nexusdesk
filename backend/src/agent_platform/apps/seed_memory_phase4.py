"""阶段 4（记忆治理）验收演示数据；可重复运行。

服务器启动或手动执行后，控制台即可逐条对照阶段 4 验收场景（05 A1/A5/A6/
A8/A10/A11、UAT 治理操作清单）：

- 刘洋（demo-p4-liuyang）：**客户合并**落库终态——源客户已消失，web/app 双
  身份归并到目标客户，人工维护条目赢得同键冲突（冲突输家物理删除），会话 /
  工单 / 注入日志全部改挂目标客户，审计含 ``memory.customer.merged``
  （source_customer_id / moved / conflicts / tickets_relinked 等，形状与
  ``governance.merge_customers`` 完全一致）。
- 陈晓峰（demo-p4-chenxiaofeng）：**客户删除**落库终态——客户 / 条目 / 身份 /
  注入日志物理删除，会话保留且 customer_id=NULL（历史 run 与事件保留），
  工单字符串引用悬空（控制台显示"客户已删除"），审计
  ``memory.customer.deleted`` 只含计数（``governance.delete_customer``）。
- 孙雅婷（demo-p4-sunyating）：**衰退治理**——两条 preference 跌破阈值转为
  expired（``memory.decay_expired``，actor="memory"，含 effective_confidence），
  一条 profile 转入 pending 再确认队列（``memory.decay_pending``），一条
  expired 被人工恢复并刷新时钟（``memory.item.restored``），一条 protected
  条目 500 天未引用仍正常注入（免疫场景 A10）。
- 周建国（demo-p4-zhoujianguo）：**pending 治理与保护解除**——``:suggested``
  建议合并确认（R4：建议行物理删除、内容落回人工条目，审计带 merged_from）、
  普通 pending 确认 / 拒绝各一例、protected 条目解除保护
  （``memory.item.released``，protected=false 且 source_type 回到 extracted）、
  另留一条 pending 供控制台队列展示。

所有审计 details 只含 id / key / 计数 / 有效分，不含客户内容（C2/C4 口径）。
确定性 uuid5 id；每个客户块以身份绑定（删除客户块以保留会话）作为幂等守卫，
重复运行不会重复写入。
"""

from sqlalchemy import text

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
    ts,
    uid,
)
from agent_platform.modules.agent_config.service import AgentConfig
from agent_platform.platform.persistence.store import one

NS4 = ns("phase4")
AGENT_NAME = "治理演示助手"
AGENT_DESC = "记忆治理演示：客户合并/删除、pending 队列、衰退过期与恢复、保护解除"
PROMPT = "你是一名企业售后客服助手。语气自然简洁，先回应客户处境再给方案，一次推进一件事。"

# 衰退审计里的有效分（decay.daily_migration 迁移时按真实公式计算并 round 6 位）：
# 0.9 * 2^(-200/90) / 0.85 * 2^(-150/90) / 1.0 * 2^(-700/365)
EFF_REPORT = 0.19288
EFF_SETTLE = 0.267733
EFF_ADDR = 0.264656


def uid4(kind: str, name: str) -> str:
    return uid(NS4, kind, name)


async def ensure_ticket_type(conn, tenant) -> str:
    """Ensure the built-in 通用工单 type; return its id (published version 1)."""
    row = await one(
        conn, "SELECT id FROM ticket_types WHERE tenant_id=:t AND code='general'", t=tenant
    )
    if row:
        return str(row["id"])
    tid = uid4("ticket-type", "general")
    await conn.execute(
        text("INSERT INTO ticket_types(tenant_id,id,code,name,published_version) VALUES(:t,:id,'general','通用工单',1) ON CONFLICT (tenant_id,code) DO NOTHING"),
        {"t": tenant, "id": tid},
    )
    await conn.execute(
        text("INSERT INTO ticket_type_versions(tenant_id,type_id,version,name,definition,created_by) VALUES(:t,:id,1,'通用工单','{\"fields\":[]}', 'seed') ON CONFLICT DO NOTHING"),
        {"t": tenant, "id": tid},
    )
    return tid


async def insert_ticket(conn, tenant, ticket_key, no, conv_id, title, desc, status,
                        customer_ref, created_h, type_id, due_h=None, resolved_h=None):
    """One ticket + detail + created process log (real 0008 schema shape)."""
    tid = uid4("ticket", ticket_key)
    await conn.execute(
        text("""INSERT INTO tickets(id,tenant_id,conversation_id,title,status,revision,created_at,
                updated_at,ticket_no,type_id,type_version,customer_id,created_by,due_at,resolved_at)
                VALUES(:id,:t,:cid,:title,:st,1,:ca,:ua,:no,:ty,1,:cust,'seed',:due,:res)
                ON CONFLICT DO NOTHING"""),
        {"id": tid, "t": tenant, "cid": conv_id, "title": title, "st": status,
         "ca": ts(created_h), "ua": ts(resolved_h or created_h), "no": no, "ty": type_id,
         "cust": customer_ref, "due": ts(due_h) if due_h is not None else None,
         "res": ts(resolved_h) if resolved_h is not None else None},
    )
    await conn.execute(
        text("INSERT INTO tickets_detail(ticket_id,tenant_id,description) VALUES(:id,:t,:d) ON CONFLICT DO NOTHING"),
        {"id": tid, "t": tenant, "d": desc},
    )
    await conn.execute(
        text("""INSERT INTO ticket_process_log(tenant_id,ticket_id,actor_type,actor_id,action_type,content,created_at)
                VALUES(:t,:id,'staff','seed','created',:c,:ca)"""),
        {"t": tenant, "id": tid, "c": f"工单创建：{title}", "ca": ts(created_h)},
    )
    return tid


async def seed_merged(p, tenant, agent, actor, backend) -> bool:
    """刘洋：source 客户（web 身份）合并进 target（app 身份）后的落库终态（A6）。"""
    async with p.engine.connect() as conn:
        if await identity_bound(conn, tenant, "web", "demo-p4-liuyang"):
            return False
    cid = uid4("customer", "liuyang")  # 合并后存续的 target 客户
    source_cid = uid4("customer", "liuyang-source")  # 源客户已 DELETE，仅存于审计
    items = {
        # 冲突条目：源侧人工维护、protected 胜出（kept=source），target 的抽取行已物理删除
        "company": item("profile", "company", "华腾国际物流集团有限公司", 1.0,
                        st="manual", prot=True, rc=14, ch=32 * 24, uh=48),
        "contact": item("profile", "contact_name", "刘洋（集团大客户部总经理，采购决策人）", 1.0,
                        st="manual", prot=True, rc=9, ch=32 * 24, uh=48),
        "vip": item("flag", "vip", "重点客户：年合同 ¥28 万，专属客服经理对接，诉求 2 小时内响应",
                    1.0, st="human", prot=True, ch=30 * 24, uh=30 * 24),
        "delivery": item("preference", "delivery_window", "工作日上午 9:00-12:00 派送，提前一天电话预约",
                         .92, sr=uid4("conversation", "demo-p4-liuyang"), rc=6, ch=38 * 24, uh=5 * 24),
        "contract": item("event", "evt-202610-contract", "2026-10 签订年度仓储配送合同 ¥280,000，含 3 个区域仓",
                         .98, st="manual", rc=3, ch=15 * 24, uh=15 * 24),
    }
    iids = {k: uid4("item", f"liuyang:{v['t']}:{v['k']}") for k, v in items.items()}
    conv_app = uid4("conversation", "demo-p4-liuyang")
    conv_web = uid4("conversation", "demo-p4-liuyang-web")
    async with p.engine.begin() as conn:
        await insert_customer(conn, tenant, cid, 40 * 24, 4)
        # 双身份：web 身份由源客户改挂而来（identities_moved=1），app 为原有身份
        await insert_identity(conn, tenant, uid4("identity", "demo-p4-liuyang"), cid,
                              "demo-p4-liuyang", 32 * 24, source="web")
        await insert_identity(conn, tenant, uid4("identity", "demo-p4-liuyang-app"), cid,
                              "demo-p4-liuyang-app", 40 * 24, source="app")
        for key, spec in items.items():
            await insert_item(conn, tenant, iids[key], cid, spec, last_referenced_h=4)
        type_id = await ensure_ticket_type(conn, tenant)
        # app 会话（原有）与 web 会话（合并改挂，conversations_moved=1）
        await insert_conversation(conn, tenant, conv_app, "demo-p4-liuyang",
                                  pairs(["我是华腾物流的刘洋，仓储合同续签的事想确认下条款。",
                                         "派送还是按老规矩，工作日上午，提前一天电话。"]),
                                  10 * 24, 5 * 24, agent["id"],
                                  summary="客户刘洋（华腾国际物流集团，VIP）：年度仓储配送合同 ¥28 万已签，偏好工作日上午派送；源 web 档案已合并入本客户。",
                                  cid=cid)
        await insert_conversation(conn, tenant, conv_web, "demo-p4-liuyang-web",
                                  pairs(["你好，我姓刘，华腾国际物流，想问下区域仓的报价。",
                                         "上次说的专属客服经理安排好了吗？"]),
                                  30 * 24, 28 * 24, agent["id"], cid=cid)
        # 合并前移入的 run：注入日志 customer_id 已改挂 target（injection_logs_moved=2）
        await insert_run(conn, tenant, NS4, "liuyang-web-0", conv_web, agent, backend,
                         "你好，我姓刘，华腾国际物流，想问下区域仓的报价。", 30 * 24,
                         cid=cid, injected=[(iids["company"], "profile", 1.0),
                                            (iids["contact"], "profile", 1.0)])
        await insert_run(conn, tenant, NS4, "liuyang-web-1", conv_web, agent, backend,
                         "上次说的专属客服经理安排好了吗？", 28 * 24,
                         cid=cid, injected=[(iids["vip"], "flag", None),
                                            (iids["company"], "profile", 1.0)])
        # 合并后的 run：双身份记忆合一，注入含冲突胜出的 company
        await insert_run(conn, tenant, NS4, "liuyang-app-0", conv_app, agent, backend,
                         "我是华腾物流的刘洋，仓储合同续签的事想确认下条款。", 5 * 24,
                         cid=cid, injected=[(iids["vip"], "flag", None),
                                            (iids["company"], "profile", 1.0),
                                            (iids["contact"], "profile", 1.0),
                                            (iids["contract"], "event", None),
                                            (iids["delivery"], "preference", .92)])
        await insert_run(conn, tenant, NS4, "liuyang-app-1", conv_app, agent, backend,
                         "派送还是按老规矩，工作日上午，提前一天电话。", 4,
                         cid=cid, injected=[(iids["vip"], "flag", None),
                                            (iids["delivery"], "preference", .92),
                                            (iids["company"], "profile", 1.0)])
        # 合并时源侧在途工单改挂 target（tickets_relinked=1，R22）
        await insert_ticket(conn, tenant, "liuyang-merge", "TK-P4-MRG001", conv_web,
                            "区域仓报价方案确认", "客户要求提供 3 个区域仓的分项报价与 SLA 承诺。",
                            "in_progress", str(cid), 6 * 24, type_id, due_h=-48)
        # 合并审计：details 形状与 governance.merge_customers 完全一致
        await insert_audit(conn, tenant, actor, "memory.customer.merged", cid,
                           {"source_customer_id": source_cid, "moved": 3,
                            "conflicts": [{"type": "profile", "key": "company", "kept": "source"}],
                            "identities_moved": 1, "conversations_moved": 1,
                            "tickets_relinked": 1, "injection_logs_moved": 2}, 48)
        await insert_audit(conn, tenant, actor, "memory.flag.upserted", iids["vip"],
                           {"customer_id": cid, "key": "vip", "source_type": "human",
                            "source_ref": actor, "pii_masked": 0}, 30 * 24)
    return True


async def seed_deleted(p, tenant, agent, actor, backend) -> bool:
    """陈晓峰：客户删除后的落库终态（A5）——行级零残留，会话/工单/run 保留。"""
    async with p.engine.connect() as conn:
        gone = await one(
            conn,
            "SELECT id FROM runtime_conversations WHERE tenant_id=:t AND external_id='demo-p4-chenxiaofeng'",
            t=tenant,
        )
        if gone:
            return False
    cid = uid4("customer", "chenxiaofeng")  # 已物理删除，仅存于审计/工单字符串引用
    conv = uid4("conversation", "demo-p4-chenxiaofeng")
    # 删除前的条目 id（物理删除后仅存在于历史事件 data 中）
    old_items = [uid4("item", "chenxiaofeng:profile:company"),
                 uid4("item", "chenxiaofeng:preference:invoice"),
                 uid4("item", "chenxiaofeng:event:evt-202610-refund")]
    async with p.engine.begin() as conn:
        type_id = await ensure_ticket_type(conn, tenant)
        # 会话保留可继续，customer_id=NULL（conversations_unlinked=1）
        await insert_conversation(conn, tenant, conv, "demo-p4-chenxiaofeng",
                                  pairs(["我是陈晓峰，上次那笔退款什么时候到账？",
                                         "好的，那麻烦尽快。"]),
                                  12 * 24, 10 * 24, agent["id"],
                                  summary="（客户数据已于删除操作后清除，仅保留会话历史）",
                                  cid=None)
        # 历史 run 与 7 事件序列保留：memory.injected 引用已删除条目 id，注入日志已级联删除
        for n, (h, msg) in enumerate([(12 * 24, "我是陈晓峰，上次那笔退款什么时候到账？"),
                                      (10 * 24, "好的，那麻烦尽快。")]):
            events = [
                (1, "run.queued", {}),
                (2, "run.started", {}),
                (3, "model.started", {"round": 1}),
                (4, "model.completed", {"round": 1, "usage": {}, "tool_calls": 0,
                                        "duration_ms": 141, "first_token_ms": 38,
                                        "gateway_call_id": uid4("gw", f"chenxiaofeng:{n}")}),
                (5, "memory_extract.queued", {"customer_id": cid}),
                (6, "memory.injected", {"flags": 0, "items": 2, "events": 1,
                                        "item_ids": old_items}),
                (7, "run.completed", {"output": REPLY, "error_code": None}),
            ]
            await insert_run(conn, tenant, NS4, f"chenxiaofeng-{n}", conv, agent, backend,
                             msg, h, events=events)
        # 工单字符串引用悬空 → 控制台按 customer_active 降级显示"客户已删除"（R16）
        await insert_ticket(conn, tenant, "chenxiaofeng-del", "TK-P4-DEL001", conv,
                            "退款进度查询", "客户咨询 2026-10 退货批次退款进度，已答复 3 个工作日内到账。",
                            "resolved", str(cid), 12 * 24, type_id, resolved_h=10 * 24)
        # 删除审计：仅操作人/计数/时间，不含客户内容（C2/C4）
        await insert_audit(conn, tenant, actor, "memory.customer.deleted", cid,
                           {"items_deleted": 3, "identities_deleted": 1,
                            "conversations_unlinked": 1, "injection_logs_deleted": 2}, 24)
    return True


async def seed_decay(p, tenant, agent, actor, backend) -> bool:
    """孙雅婷：衰退迁移 / 恢复 / protected 免疫（A8/A10/A11）。"""
    async with p.engine.connect() as conn:
        if await identity_bound(conn, tenant, "web", "demo-p4-sunyating"):
            return False
    cid = uid4("customer", "sunyating")
    items = {
        # 200 天未引用的 preference，跌破阈值 → expired（A8）
        "report": item("preference", "report_format", "月度对账报表需含批次号与序列号明细", .9,
                       status="expired", sr=uid4("conversation", "demo-p4-sunyating"),
                       ch=200 * 24, uh=49),
        # 150 天未引用，同样 expired
        "settle": item("preference", "settlement_cycle", "按自然季度结算，账期 45 天", .85,
                       status="expired", sr=uid4("conversation", "demo-p4-sunyating"),
                       ch=150 * 24, uh=49),
        # 700 天未引用的 profile → 转 pending 再确认队列（非 expired，A11/R8）
        "addr": item("profile", "warehouse_addr", "旧仓地址：苏州市相城区黄埭镇潘阳工业园 3 号库", 1.0,
                     status="pending", sr=uid4("conversation", "demo-p4-sunyating"),
                     ch=700 * 24, uh=49),
        # 曾 expired，20 小时前人工恢复并刷新时钟（R19），恢复后正常注入
        "review": item("preference", "monthly_review", "每月 5 日前召开服务复盘会，需提前准备工单汇总", .88,
                       sr=uid4("conversation", "demo-p4-sunyating"), rc=3, ch=160 * 24, uh=20),
        # protected 500 天不衰减、不淘汰、正常注入（A10）
        "tier": item("profile", "tier", "战略客户（省级集采入围单位，年度框架协议）", 1.0,
                     st="manual", prot=True, rc=18, ch=500 * 24, uh=26),
        "company": item("profile", "company", "瑞康医疗器械股份有限公司", .97,
                        st="manual", rc=11, ch=220 * 24, uh=26),
    }
    iids = {k: uid4("item", f"sunyating:{v['t']}:{v['k']}") for k, v in items.items()}
    conv = uid4("conversation", "demo-p4-sunyating")
    async with p.engine.begin() as conn:
        await insert_customer(conn, tenant, cid, 220 * 24, 4)
        await insert_identity(conn, tenant, uid4("identity", "demo-p4-sunyating"), cid,
                              "demo-p4-sunyating", 220 * 24)
        for k, spec in items.items():
            # expired/pending 条目保持老龄时钟；restore 的 review 时钟已刷新
            lra = 20 if k == "review" else (26 if k == "tier" else None)
            await insert_item(conn, tenant, iids[k], cid, spec, last_referenced_h=lra)
        await insert_conversation(conn, tenant, conv, "demo-p4-sunyating",
                                  pairs(["我是瑞康医疗的孙雅婷，本月复盘会安排在 3 号可以吗？",
                                         "复盘材料按老样子准备就行。"]),
                                  22, 4, agent["id"], cid=cid)
        # 恢复后的 run：review 重新进入注入；expired/pending 条目不出现
        await insert_run(conn, tenant, NS4, "sunyating-0", conv, agent, backend,
                         "我是瑞康医疗的孙雅婷，本月复盘会安排在 3 号可以吗？", 20,
                         cid=cid, injected=[(iids["tier"], "profile", 1.0),
                                            (iids["company"], "profile", .97),
                                            (iids["review"], "preference", .88)])
        await insert_run(conn, tenant, NS4, "sunyating-1", conv, agent, backend,
                         "复盘材料按老样子准备就行。", 4,
                         cid=cid, injected=[(iids["tier"], "profile", 1.0),
                                            (iids["review"], "preference", .88)])
        # 衰退迁移审计（actor="memory"，daily_migration 真实形状，含迁移时有效分）
        await insert_audit(conn, tenant, "memory", "memory.decay_expired", iids["report"],
                           {"customer_id": cid, "type": "preference", "key": "report_format",
                            "effective_confidence": EFF_REPORT}, 49)
        await insert_audit(conn, tenant, "memory", "memory.decay_expired", iids["settle"],
                           {"customer_id": cid, "type": "preference", "key": "settlement_cycle",
                            "effective_confidence": EFF_SETTLE}, 49)
        await insert_audit(conn, tenant, "memory", "memory.decay_pending", iids["addr"],
                           {"customer_id": cid, "type": "profile", "key": "warehouse_addr",
                            "effective_confidence": EFF_ADDR}, 49)
        # expired → active 恢复（R19 刷新时钟）
        await insert_audit(conn, tenant, actor, "memory.item.restored", iids["review"],
                           {"customer_id": cid, "type": "preference", "key": "monthly_review",
                            "old_status": "expired", "new_status": "active"}, 20)
    return True


async def seed_pending(p, tenant, agent, actor, backend) -> bool:
    """周建国：pending 确认（含 :suggested 合并）/ 拒绝 / protected 解除（A1、UAT）。"""
    async with p.engine.connect() as conn:
        if await identity_bound(conn, tenant, "web", "demo-p4-zhoujianguo"):
            return False
    cid = uid4("customer", "zhoujianguo")
    items = {
        # 人工维护条目：:suggested 建议确认后内容已合并回来（prev 留存、rev+1、所有权不变）
        "address": item("profile", "address", "总部地址：成都市高新区天府大道 888 号（新址）", 1.0,
                        st="manual", prot=True, rc=4, ch=20 * 24, uh=30,
                        prev="总部地址：成都市武侯区人民南路 66 号", rev=2),
        # 普通 pending 被确认 → active，时钟刷新
        "meeting": item("preference", "meeting_time", "项目例会固定在每周二下午 2 点，线上进行", .8,
                        sr=uid4("conversation", "demo-p4-zhoujianguo"), rc=2, ch=6 * 24, uh=36),
        # pending 被拒绝 → deleted（控制台可恢复）
        "fax": item("profile", "fax_number", "传真号码 028-****1234（低置信抽取）", .6,
                    status="deleted", sr=uid4("conversation", "demo-p4-zhoujianguo"),
                    ch=6 * 24, uh=35),
        # 仍在 pending 队列等待处理
        "boss": item("profile", "decision_maker", "技术把关人可能是总工程师姓何（待确认）", .55,
                     status="pending", sr=uid4("conversation", "demo-p4-zhoujianguo"),
                     ch=2 * 24, uh=2 * 24),
        # 曾被人工保护，28 小时前解除保护回到自动管理（protected=false / source_type=extracted）
        "industry": item("profile", "industry", "建筑工程总承包（市政 + 房建双一级资质）", 1.0,
                         st="extracted", rc=5, ch=25 * 24, uh=28, rev=2),
        "bid": item("event", "evt-202610-bid", "2026-10 中标高新区市政道路改造项目（合同额 ¥1,200 万）",
                    .97, st="manual", rc=2, ch=8 * 24, uh=8 * 24),
    }
    iids = {k: uid4("item", f"zhoujianguo:{v['t']}:{v['k']}") for k, v in items.items()}
    suggested_id = uid4("item", "zhoujianguo:profile:address:suggested")  # 已物理删除（R4 合并）
    conv = uid4("conversation", "demo-p4-zhoujianguo")
    async with p.engine.begin() as conn:
        await insert_customer(conn, tenant, cid, 30 * 24, 3)
        await insert_identity(conn, tenant, uid4("identity", "demo-p4-zhoujianguo"), cid,
                              "demo-p4-zhoujianguo", 30 * 24)
        for k, spec in items.items():
            lra = 36 if k == "meeting" else None
            await insert_item(conn, tenant, iids[k], cid, spec, last_referenced_h=lra)
        await insert_conversation(conn, tenant, conv, "demo-p4-zhoujianguo",
                                  pairs(["我是恒信建筑的周建国，中标项目的开工协调会定在周二下午吧。",
                                         "我们总部搬到高新区天府大道 888 号了，资料里的地址更新一下。"]),
                                  3 * 24, 3, agent["id"], cid=cid)
        await insert_run(conn, tenant, NS4, "zhoujianguo-0", conv, agent, backend,
                         "我是恒信建筑的周建国，中标项目的开工协调会定在周二下午吧。", 40,
                         cid=cid, injected=[(iids["address"], "profile", 1.0),
                                            (iids["industry"], "profile", 1.0),
                                            (iids["bid"], "event", None),
                                            (iids["meeting"], "preference", .8)])
        await insert_run(conn, tenant, NS4, "zhoujianguo-1", conv, agent, backend,
                         "我们总部搬到高新区天府大道 888 号了，资料里的地址更新一下。", 3,
                         cid=cid, injected=[(iids["address"], "profile", 1.0),
                                            (iids["industry"], "profile", 1.0),
                                            (iids["meeting"], "preference", .8)])
        # :suggested 合并确认（R4）：resource 为人工条目，merged_from 为已删除的建议行
        await insert_audit(conn, tenant, actor, "memory.item.confirmed", iids["address"],
                           {"customer_id": cid, "type": "profile", "key": "address",
                            "old_status": "pending", "new_status": "active",
                            "merged_from": suggested_id}, 30)
        # 普通 pending 确认 / 拒绝（service.confirm_item / reject_item 形状）
        await insert_audit(conn, tenant, actor, "memory.item.confirmed", iids["meeting"],
                           {"customer_id": cid, "type": "preference", "key": "meeting_time",
                            "old_status": "pending", "new_status": "active"}, 36)
        await insert_audit(conn, tenant, actor, "memory.item.rejected", iids["fax"],
                           {"customer_id": cid, "type": "profile", "key": "fax_number",
                            "old_status": "pending", "new_status": "deleted"}, 35)
        # protected 解除（service.release_item：protected=false、source_type 回 extracted）
        await insert_audit(conn, tenant, actor, "memory.item.released", iids["industry"],
                           {"customer_id": cid, "type": "profile", "key": "industry"}, 28)
    return True


async def seed(runtime, settings) -> bool:
    """Idempotent entrypoint: True only when at least one block inserted."""
    p = runtime.platform
    tenant = settings.tenant_id
    agent = await ensure_agent(
        p, tenant, "seed", AGENT_NAME, AGENT_DESC,
        AgentConfig(system_prompt=PROMPT, tool_names=[], knowledge_base_ids=[]),
    )
    actor = await current_audit_actor(p, tenant)
    backend = settings.model_backend
    inserted = False
    for block in (seed_merged, seed_deleted, seed_decay, seed_pending):
        inserted = await block(p, tenant, agent, actor, backend) or inserted
    return inserted


if __name__ == "__main__":
    run_standalone(seed, "阶段4记忆治理演示数据")
