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

Display strings are localized via ``seed_memory_i18n`` (AGENT_SEED_LOCALE);
identity keys stay constant so the locale only affects first-time seeding.
"""

from sqlalchemy import text

from agent_platform.apps.seed_memory_common import (
    current_audit_actor,
    default_reply,
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
from agent_platform.apps.seed_memory_i18n import T
from agent_platform.modules.agent_config.service import AgentConfig
from agent_platform.platform.persistence.store import one

NS4 = ns("phase4")

# 衰退审计里的有效分（decay.daily_migration 迁移时按真实公式计算并 round 6 位）：
# 0.9 * 2^(-200/90) / 0.85 * 2^(-150/90) / 1.0 * 2^(-700/365)
EFF_REPORT = 0.19288
EFF_SETTLE = 0.267733
EFF_ADDR = 0.264656


def uid4(kind: str, name: str) -> str:
    return uid(NS4, kind, name)


def _agent_display() -> tuple[str, str, str]:
    """Agent name/description/system prompt; built per call so T() sees the active locale."""
    return (
        T(zh="治理演示助手", en="Governance Demo Assistant",
          zh_tw="治理演示助手", hi="गवर्नेंस डेमो असिस्टेंट"),
        T(zh="记忆治理演示：客户合并/删除、pending 队列、衰退过期与恢复、保护解除",
          en="Memory governance demo: customer merge/delete, pending queue, decay expiry and restore, protection release",
          zh_tw="記憶治理演示：客戶合併/刪除、pending 佇列、衰退過期與恢復、保護解除",
          hi="मेमोरी गवर्नेंस डेमो: ग्राहक मर्ज/डिलीट, पेंडिंग क्यू, डिके एक्सपायरी और रीस्टोर, प्रोटेक्शन रिलीज़"),
        T(zh="你是一名企业售后客服助手。语气自然简洁，先回应客户处境再给方案，一次推进一件事。",
          en="You are an enterprise after-sales support assistant. Keep a natural, concise tone; "
             "acknowledge the customer's situation before offering solutions; advance one thing at a time.",
          zh_tw="你是一名企業售後客服助手。語氣自然簡潔，先回應客戶處境再給方案，一次推進一件事。",
          hi="आप एक एंटरप्राइज़ आफ्टर-सेल्स सपोर्ट असिस्टेंट हैं। स्वाभाविक और संक्षिप्त शैली रखें; "
             "समाधान देने से पहले ग्राहक की स्थिति को स्वीकार करें; एक समय में एक ही बात आगे बढ़ाएँ।"),
    )


def _merged_items() -> dict:
    """刘洋 memory items; built per call so T() sees the active locale."""
    return {
        # 冲突条目：源侧人工维护、protected 胜出（kept=source），target 的抽取行已物理删除
        "company": item("profile", "company",
                        T(zh="华腾国际物流集团有限公司", en="Huateng International Logistics Group Co., Ltd.",
                          zh_tw="華騰國際物流集團有限公司",
                          hi="Huateng International Logistics Group Co., Ltd."),
                        1.0, st="manual", prot=True, rc=14, ch=32 * 24, uh=48),
        "contact": item("profile", "contact_name",
                        T(zh="刘洋（集团大客户部总经理，采购决策人）",
                          en="Liu Yang (General Manager, Group Key Accounts Dept.; procurement decision-maker)",
                          zh_tw="劉洋（集團大客戶部總經理，採購決策人）",
                          hi="Liu Yang (जनरल मैनेजर, ग्रुप की अकाउंट्स विभाग; खरीद निर्णयकर्ता)"),
                        1.0, st="manual", prot=True, rc=9, ch=32 * 24, uh=48),
        "vip": item("flag", "vip",
                    T(zh="重点客户：年合同 ¥28 万，专属客服经理对接，诉求 2 小时内响应",
                      en="Key account: ¥280k annual contract, dedicated account manager, respond to requests within 2 hours",
                      zh_tw="重點客戶：年合約 ¥28 萬，專屬客服經理對接，訴求 2 小時內回應",
                      hi="प्रमुख खाता: ¥2.8 लाख वार्षिक अनुबंध, समर्पित खाता प्रबंधक, अनुरोधों का 2 घंटे के भीतर जवाब"),
                    1.0, st="human", prot=True, ch=30 * 24, uh=30 * 24),
        "delivery": item("preference", "delivery_window",
                         T(zh="工作日上午 9:00-12:00 派送，提前一天电话预约",
                           en="Deliver weekday mornings 9:00-12:00; book by phone one day in advance",
                           zh_tw="工作日上午 9:00-12:00 派送，提前一天電話預約",
                           hi="सप्ताह के दिनों में सुबह 9:00-12:00 डिलीवरी; एक दिन पहले फोन से बुकिंग करें"),
                         .92, sr=uid4("conversation", "demo-p4-liuyang"), rc=6, ch=38 * 24, uh=5 * 24),
        "contract": item("event", "evt-202610-contract",
                         T(zh="2026-10 签订年度仓储配送合同 ¥280,000，含 3 个区域仓",
                           en="2026-10 signed annual warehousing & distribution contract ¥280,000, covering 3 regional warehouses",
                           zh_tw="2026-10 簽訂年度倉儲配送合約 ¥280,000，含 3 個區域倉",
                           hi="2026-10 में ¥280,000 का वार्षिक वेयरहाउसिंग व डिस्ट्रीब्यूशन अनुबंध साइन हुआ, जिसमें 3 क्षेत्रीय गोदाम शामिल हैं"),
                         .98, st="manual", rc=3, ch=15 * 24, uh=15 * 24),
    }


def _decay_items() -> dict:
    """孙雅婷 memory items; built per call so T() sees the active locale."""
    return {
        # 200 天未引用的 preference，跌破阈值 → expired（A8）
        "report": item("preference", "report_format",
                       T(zh="月度对账报表需含批次号与序列号明细",
                         en="Monthly reconciliation report must include batch numbers and serial-number details",
                         zh_tw="月度對帳報表需含批次號與序列號明細",
                         hi="मासिक रिकन्सिलिएशन रिपोर्ट में बैच नंबर और सीरियल नंबर का विवरण अनिवार्य है"),
                       .9, status="expired", sr=uid4("conversation", "demo-p4-sunyating"),
                       ch=200 * 24, uh=49),
        # 150 天未引用，同样 expired
        "settle": item("preference", "settlement_cycle",
                       T(zh="按自然季度结算，账期 45 天",
                         en="Settle by calendar quarter, 45-day payment terms",
                         zh_tw="按自然季度結算，帳期 45 天",
                         hi="कैलेंडर तिमाही के अनुसार निपटान, 45 दिन की भुगतान अवधि"),
                       .85, status="expired", sr=uid4("conversation", "demo-p4-sunyating"),
                       ch=150 * 24, uh=49),
        # 700 天未引用的 profile → 转 pending 再确认队列（非 expired，A11/R8）
        "addr": item("profile", "warehouse_addr",
                     T(zh="旧仓地址：苏州市相城区黄埭镇潘阳工业园 3 号库",
                       en="Old warehouse address: Warehouse 3, Panyang Industrial Park, Huangdai Town, Xiangcheng District, Suzhou",
                       zh_tw="舊倉地址：蘇州市相城區黃埭鎮潘陽工業園 3 號庫",
                       hi="पुराना गोदाम पता: Warehouse 3, Panyang Industrial Park, Huangdai Town, Xiangcheng District, Suzhou"),
                     1.0, status="pending", sr=uid4("conversation", "demo-p4-sunyating"),
                     ch=700 * 24, uh=49),
        # 曾 expired，20 小时前人工恢复并刷新时钟（R19），恢复后正常注入
        "review": item("preference", "monthly_review",
                       T(zh="每月 5 日前召开服务复盘会，需提前准备工单汇总",
                         en="Hold the service review meeting before the 5th of each month; prepare the ticket summary in advance",
                         zh_tw="每月 5 日前召開服務復盤會，需提前準備工單匯總",
                         hi="हर महीने की 5 तारीख से पहले सेवा समीक्षा बैठक करें; टिकट सारांश पहले से तैयार रखें"),
                       .88, sr=uid4("conversation", "demo-p4-sunyating"), rc=3, ch=160 * 24, uh=20),
        # protected 500 天不衰减、不淘汰、正常注入（A10）
        "tier": item("profile", "tier",
                     T(zh="战略客户（省级集采入围单位，年度框架协议）",
                       en="Strategic customer (shortlisted for provincial centralized procurement, annual framework agreement)",
                       zh_tw="戰略客戶（省級集採入圍單位，年度框架協議）",
                       hi="रणनीतिक ग्राहक (प्रादेशिक केंद्रीकृत खरीद में शॉर्टलिस्टेड, वार्षिक फ्रेमवर्क एग्रीमेंट)"),
                     1.0, st="manual", prot=True, rc=18, ch=500 * 24, uh=26),
        "company": item("profile", "company",
                        T(zh="瑞康医疗器械股份有限公司", en="Ruikang Medical Devices Co., Ltd.",
                          zh_tw="瑞康醫療器械股份有限公司",
                          hi="Ruikang Medical Devices Co., Ltd."),
                        .97, st="manual", rc=11, ch=220 * 24, uh=26),
    }


def _pending_items() -> dict:
    """周建国 memory items; built per call so T() sees the active locale."""
    return {
        # 人工维护条目：:suggested 建议确认后内容已合并回来（prev 留存、rev+1、所有权不变）
        "address": item("profile", "address",
                        T(zh="总部地址：成都市高新区天府大道 888 号（新址）",
                          en="Headquarters address: 888 Tianfu Avenue, High-Tech Zone, Chengdu (new site)",
                          zh_tw="總部地址：成都市高新區天府大道 888 號（新址）",
                          hi="मुख्यालय पता: 888 Tianfu Avenue, High-Tech Zone, Chengdu (नया पता)"),
                        1.0, st="manual", prot=True, rc=4, ch=20 * 24, uh=30,
                        prev=T(zh="总部地址：成都市武侯区人民南路 66 号",
                               en="Headquarters address: 66 Renmin South Road, Wuhou District, Chengdu",
                               zh_tw="總部地址：成都市武侯區人民南路 66 號",
                               hi="मुख्यालय पता: 66 Renmin South Road, Wuhou District, Chengdu"),
                        rev=2),
        # 普通 pending 被确认 → active，时钟刷新
        "meeting": item("preference", "meeting_time",
                        T(zh="项目例会固定在每周二下午 2 点，线上进行",
                          en="Project regular meeting fixed at 2 PM every Tuesday, held online",
                          zh_tw="專案例會固定在每週二下午 2 點，線上進行",
                          hi="प्रोजेक्ट की नियमित बैठक हर मंगलवार दोपहर 2 बजे, ऑनलाइन"),
                        .8, sr=uid4("conversation", "demo-p4-zhoujianguo"), rc=2, ch=6 * 24, uh=36),
        # pending 被拒绝 → deleted（控制台可恢复）
        "fax": item("profile", "fax_number",
                    T(zh="传真号码 028-****1234（低置信抽取）",
                      en="Fax number 028-****1234 (low-confidence extraction)",
                      zh_tw="傳真號碼 028-****1234（低置信抽取）",
                      hi="फ़ैक्स नंबर 028-****1234 (कम-विश्वास निष्कर्षण)"),
                    .6, status="deleted", sr=uid4("conversation", "demo-p4-zhoujianguo"),
                    ch=6 * 24, uh=35),
        # 仍在 pending 队列等待处理
        "boss": item("profile", "decision_maker",
                     T(zh="技术把关人可能是总工程师姓何（待确认）",
                       en="Technical gatekeeper may be the chief engineer surnamed He (unconfirmed)",
                       zh_tw="技術把關人可能是總工程師姓何（待確認）",
                       hi="तकनीकी गेटकीपर संभवतः मुख्य अभियंता हैं, उपनाम He (पुष्टि बाकी)"),
                     .55, status="pending", sr=uid4("conversation", "demo-p4-zhoujianguo"),
                     ch=2 * 24, uh=2 * 24),
        # 曾被人工保护，28 小时前解除保护回到自动管理（protected=false / source_type=extracted）
        "industry": item("profile", "industry",
                         T(zh="建筑工程总承包（市政 + 房建双一级资质）",
                           en="General construction contracting (grade-1 qualifications in both municipal works and building construction)",
                           zh_tw="建築工程總承包（市政 + 房建雙一級資質）",
                           hi="जनरल कंस्ट्रक्शन कॉन्ट्रैक्टिंग (नगरपालिका कार्यों और भवन निर्माण दोनों में ग्रेड-1 योग्यता)"),
                         1.0, st="extracted", rc=5, ch=25 * 24, uh=28, rev=2),
        "bid": item("event", "evt-202610-bid",
                    T(zh="2026-10 中标高新区市政道路改造项目（合同额 ¥1,200 万）",
                      en="2026-10 won the bid for the High-Tech Zone municipal road renovation project (contract value ¥12M)",
                      zh_tw="2026-10 中標高新區市政道路改造專案（合約額 ¥1,200 萬）",
                      hi="2026-10 में हाई-टेक ज़ोन नगरपालिका सड़क नवीनीकरण परियोजना की बोली जीती (अनुबंध मूल्य ¥1.2 करोड़)"),
                    .97, st="manual", rc=2, ch=8 * 24, uh=8 * 24),
    }


async def ensure_ticket_type(conn, tenant) -> str:
    """Ensure the built-in 通用工单 type; return its id (published version 1)."""
    row = await one(
        conn, "SELECT id FROM ticket_types WHERE tenant_id=:t AND code='general'", t=tenant
    )
    if row:
        return str(row["id"])
    tid = uid4("ticket-type", "general")
    name = T(zh="通用工单", en="General Ticket", zh_tw="通用工單", hi="सामान्य टिकट")
    await conn.execute(
        text("INSERT INTO ticket_types(tenant_id,id,code,name,published_version) VALUES(:t,:id,'general',:name,1) ON CONFLICT (tenant_id,code) DO NOTHING"),
        {"t": tenant, "id": tid, "name": name},
    )
    await conn.execute(
        text("INSERT INTO ticket_type_versions(tenant_id,type_id,version,name,definition,created_by) VALUES(:t,:id,1,:name,'{\"fields\":[]}', 'seed') ON CONFLICT DO NOTHING"),
        {"t": tenant, "id": tid, "name": name},
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
        {"t": tenant, "id": tid,
         "c": T(zh=f"工单创建：{title}", en=f"Ticket created: {title}",
                zh_tw=f"工單建立：{title}", hi=f"टिकट बनाया गया: {title}"),
         "ca": ts(created_h)},
    )
    return tid


async def seed_merged(p, tenant, agent, actor, backend) -> bool:
    """刘洋：source 客户（web 身份）合并进 target（app 身份）后的落库终态（A6）。"""
    async with p.engine.connect() as conn:
        if await identity_bound(conn, tenant, "web", "demo-p4-liuyang"):
            return False
    cid = uid4("customer", "liuyang")  # 合并后存续的 target 客户
    source_cid = uid4("customer", "liuyang-source")  # 源客户已 DELETE，仅存于审计
    items = _merged_items()
    iids = {k: uid4("item", f"liuyang:{v['t']}:{v['k']}") for k, v in items.items()}
    conv_app = uid4("conversation", "demo-p4-liuyang")
    conv_web = uid4("conversation", "demo-p4-liuyang-web")
    msg_contract = T(zh="我是华腾物流的刘洋，仓储合同续签的事想确认下条款。",
                     en="This is Liu Yang from Huateng Logistics. I'd like to confirm the terms of the warehousing contract renewal.",
                     zh_tw="我是華騰物流的劉洋，倉儲合約續簽的事想確認下條款。",
                     hi="मैं Huateng Logistics से Liu Yang बोल रहा हूँ। वेयरहाउसिंग अनुबंध नवीनीकरण की शर्तें कन्फर्म करनी थीं।")
    msg_delivery = T(zh="派送还是按老规矩，工作日上午，提前一天电话。",
                     en="Keep deliveries as usual — weekday mornings, with a phone call one day ahead.",
                     zh_tw="派送還是按老規矩，工作日上午，提前一天電話。",
                     hi="डिलीवरी वैसे ही रखें — सप्ताह के दिनों में सुबह, एक दिन पहले फोन करके।")
    msg_quote = T(zh="你好，我姓刘，华腾国际物流，想问下区域仓的报价。",
                  en="Hello, my surname is Liu, from Huateng International Logistics. I'd like to ask about regional warehouse pricing.",
                  zh_tw="你好，我姓劉，華騰國際物流，想問下區域倉的報價。",
                  hi="नमस्ते, मेरा उपनाम Liu है, Huateng International Logistics से। क्षेत्रीय गोदाम के रेट के बारे में पूछना था।")
    msg_manager = T(zh="上次说的专属客服经理安排好了吗？",
                    en="Has the dedicated account manager we discussed been arranged?",
                    zh_tw="上次說的專屬客服經理安排好了嗎？",
                    hi="पिछली बार चर्चा हुआ समर्पित खाता प्रबंधक तय हो गया क्या?")
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
                                  pairs([msg_contract, msg_delivery]),
                                  10 * 24, 5 * 24, agent["id"],
                                  summary=T(zh="客户刘洋（华腾国际物流集团，VIP）：年度仓储配送合同 ¥28 万已签，偏好工作日上午派送；源 web 档案已合并入本客户。",
                                            en="Customer Liu Yang (Huateng International Logistics Group, VIP): annual warehousing & distribution contract of ¥280k signed; prefers weekday-morning delivery; the source web profile has been merged into this customer.",
                                            zh_tw="客戶劉洋（華騰國際物流集團，VIP）：年度倉儲配送合約 ¥28 萬已簽，偏好工作日上午派送；源 web 檔案已合併入本客戶。",
                                            hi="ग्राहक Liu Yang (Huateng International Logistics Group, VIP): ¥2.8 लाख का वार्षिक वेयरहाउसिंग व डिस्ट्रीब्यूशन अनुबंध साइन हो चुका है; सप्ताह के दिनों में सुबह डिलीवरी पसंद करते हैं; स्रोत web प्रोफ़ाइल इस ग्राहक में मर्ज हो चुकी है।"),
                                  cid=cid)
        await insert_conversation(conn, tenant, conv_web, "demo-p4-liuyang-web",
                                  pairs([msg_quote, msg_manager]),
                                  30 * 24, 28 * 24, agent["id"], cid=cid)
        # 合并前移入的 run：注入日志 customer_id 已改挂 target（injection_logs_moved=2）
        await insert_run(conn, tenant, NS4, "liuyang-web-0", conv_web, agent, backend,
                         msg_quote, 30 * 24,
                         cid=cid, injected=[(iids["company"], "profile", 1.0),
                                            (iids["contact"], "profile", 1.0)])
        await insert_run(conn, tenant, NS4, "liuyang-web-1", conv_web, agent, backend,
                         msg_manager, 28 * 24,
                         cid=cid, injected=[(iids["vip"], "flag", None),
                                            (iids["company"], "profile", 1.0)])
        # 合并后的 run：双身份记忆合一，注入含冲突胜出的 company
        await insert_run(conn, tenant, NS4, "liuyang-app-0", conv_app, agent, backend,
                         msg_contract, 5 * 24,
                         cid=cid, injected=[(iids["vip"], "flag", None),
                                            (iids["company"], "profile", 1.0),
                                            (iids["contact"], "profile", 1.0),
                                            (iids["contract"], "event", None),
                                            (iids["delivery"], "preference", .92)])
        await insert_run(conn, tenant, NS4, "liuyang-app-1", conv_app, agent, backend,
                         msg_delivery, 4,
                         cid=cid, injected=[(iids["vip"], "flag", None),
                                            (iids["delivery"], "preference", .92),
                                            (iids["company"], "profile", 1.0)])
        # 合并时源侧在途工单改挂 target（tickets_relinked=1，R22）
        await insert_ticket(conn, tenant, "liuyang-merge", "TK-P4-MRG001", conv_web,
                            T(zh="区域仓报价方案确认", en="Regional warehouse quote confirmation",
                              zh_tw="區域倉報價方案確認", hi="क्षेत्रीय गोदाम कोटेशन की पुष्टि"),
                            T(zh="客户要求提供 3 个区域仓的分项报价与 SLA 承诺。",
                              en="Customer requested itemized quotes for 3 regional warehouses plus SLA commitments.",
                              zh_tw="客戶要求提供 3 個區域倉的分項報價與 SLA 承諾。",
                              hi="ग्राहक ने 3 क्षेत्रीय गोदामों के लिए मदवार कोटेशन और SLA वादे माँगे हैं।"),
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
    msg_refund = T(zh="我是陈晓峰，上次那笔退款什么时候到账？",
                   en="This is Chen Xiaofeng. When will my refund from last time arrive?",
                   zh_tw="我是陳曉峰，上次那筆退款什麼時候到帳？",
                   hi="मैं Chen Xiaofeng बोल रहा हूँ। पिछली बार का मेरा रिफंड कब आएगा?")
    msg_hurry = T(zh="好的，那麻烦尽快。",
                  en="OK, please get it done as soon as possible.",
                  zh_tw="好的，那麻煩盡快。",
                  hi="ठीक है, कृपया जल्द से जल्द करवा दीजिए।")
    async with p.engine.begin() as conn:
        type_id = await ensure_ticket_type(conn, tenant)
        # 会话保留可继续，customer_id=NULL（conversations_unlinked=1）
        await insert_conversation(conn, tenant, conv, "demo-p4-chenxiaofeng",
                                  pairs([msg_refund, msg_hurry]),
                                  12 * 24, 10 * 24, agent["id"],
                                  summary=T(zh="（客户数据已于删除操作后清除，仅保留会话历史）",
                                            en="(Customer data was purged by the delete operation; only conversation history is retained)",
                                            zh_tw="（客戶資料已於刪除操作後清除，僅保留會話歷史）",
                                            hi="(डिलीट ऑपरेशन के बाद ग्राहक डेटा हटा दिया गया; केवल वार्तालाप इतिहास सुरक्षित है)"),
                                  cid=None)
        # 历史 run 与 7 事件序列保留：memory.injected 引用已删除条目 id，注入日志已级联删除
        for n, (h, msg) in enumerate([(12 * 24, msg_refund), (10 * 24, msg_hurry)]):
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
                (7, "run.completed", {"output": default_reply(), "error_code": None}),
            ]
            await insert_run(conn, tenant, NS4, f"chenxiaofeng-{n}", conv, agent, backend,
                             msg, h, events=events)
        # 工单字符串引用悬空 → 控制台按 customer_active 降级显示"客户已删除"（R16）
        await insert_ticket(conn, tenant, "chenxiaofeng-del", "TK-P4-DEL001", conv,
                            T(zh="退款进度查询", en="Refund progress inquiry",
                              zh_tw="退款進度查詢", hi="रिफंड प्रगति पूछताछ"),
                            T(zh="客户咨询 2026-10 退货批次退款进度，已答复 3 个工作日内到账。",
                              en="Customer asked about the refund progress for the 2026-10 return batch; advised it would arrive within 3 business days.",
                              zh_tw="客戶諮詢 2026-10 退貨批次退款進度，已答覆 3 個工作日內到帳。",
                              hi="ग्राहक ने 2026-10 रिटर्न बैच के रिफंड की प्रगति पूछी; बताया गया कि 3 कार्यदिवसों में राशि आ जाएगी।"),
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
    items = _decay_items()
    iids = {k: uid4("item", f"sunyating:{v['t']}:{v['k']}") for k, v in items.items()}
    conv = uid4("conversation", "demo-p4-sunyating")
    msg_schedule = T(zh="我是瑞康医疗的孙雅婷，本月复盘会安排在 3 号可以吗？",
                     en="This is Sun Yating from Ruikang Medical. Can we schedule this month's review meeting on the 3rd?",
                     zh_tw="我是瑞康醫療的孫雅婷，本月復盤會安排在 3 號可以嗎？",
                     hi="मैं Ruikang Medical से Sun Yating बोल रही हूँ। क्या इस महीने की समीक्षा बैठक 3 तारीख को रख सकते हैं?")
    msg_materials = T(zh="复盘材料按老样子准备就行。",
                      en="Just prepare the review materials the usual way.",
                      zh_tw="復盤材料按老樣子準備就行。",
                      hi="समीक्षा सामग्री हमेशा की तरह तैयार कर दीजिए।")
    async with p.engine.begin() as conn:
        await insert_customer(conn, tenant, cid, 220 * 24, 4)
        await insert_identity(conn, tenant, uid4("identity", "demo-p4-sunyating"), cid,
                              "demo-p4-sunyating", 220 * 24)
        for k, spec in items.items():
            # expired/pending 条目保持老龄时钟；restore 的 review 时钟已刷新
            lra = 20 if k == "review" else (26 if k == "tier" else None)
            await insert_item(conn, tenant, iids[k], cid, spec, last_referenced_h=lra)
        await insert_conversation(conn, tenant, conv, "demo-p4-sunyating",
                                  pairs([msg_schedule, msg_materials]),
                                  22, 4, agent["id"], cid=cid)
        # 恢复后的 run：review 重新进入注入；expired/pending 条目不出现
        await insert_run(conn, tenant, NS4, "sunyating-0", conv, agent, backend,
                         msg_schedule, 20,
                         cid=cid, injected=[(iids["tier"], "profile", 1.0),
                                            (iids["company"], "profile", .97),
                                            (iids["review"], "preference", .88)])
        await insert_run(conn, tenant, NS4, "sunyating-1", conv, agent, backend,
                         msg_materials, 4,
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
    items = _pending_items()
    iids = {k: uid4("item", f"zhoujianguo:{v['t']}:{v['k']}") for k, v in items.items()}
    suggested_id = uid4("item", "zhoujianguo:profile:address:suggested")  # 已物理删除（R4 合并）
    conv = uid4("conversation", "demo-p4-zhoujianguo")
    msg_kickoff = T(zh="我是恒信建筑的周建国，中标项目的开工协调会定在周二下午吧。",
                    en="This is Zhou Jianguo from Hengxin Construction. Let's schedule the kickoff coordination meeting for the won project on Tuesday afternoon.",
                    zh_tw="我是恆信建築的周建國，中標專案的開工協調會定在週二下午吧。",
                    hi="मैं Hengxin Construction से Zhou Jianguo बोल रहा हूँ। जीती गई परियोजना की किकऑफ़ समन्वय बैठक मंगलवार दोपहर रखते हैं।")
    msg_moved = T(zh="我们总部搬到高新区天府大道 888 号了，资料里的地址更新一下。",
                  en="Our headquarters has moved to 888 Tianfu Avenue, High-Tech Zone — please update the address in your records.",
                  zh_tw="我們總部搬到高新區天府大道 888 號了，資料裡的地址更新一下。",
                  hi="हमारा मुख्यालय 888 Tianfu Avenue, High-Tech Zone शिफ्ट हो गया है — कृपया अपने रिकॉर्ड में पता अपडेट कर दें।")
    async with p.engine.begin() as conn:
        await insert_customer(conn, tenant, cid, 30 * 24, 3)
        await insert_identity(conn, tenant, uid4("identity", "demo-p4-zhoujianguo"), cid,
                              "demo-p4-zhoujianguo", 30 * 24)
        for k, spec in items.items():
            lra = 36 if k == "meeting" else None
            await insert_item(conn, tenant, iids[k], cid, spec, last_referenced_h=lra)
        await insert_conversation(conn, tenant, conv, "demo-p4-zhoujianguo",
                                  pairs([msg_kickoff, msg_moved]),
                                  3 * 24, 3, agent["id"], cid=cid)
        await insert_run(conn, tenant, NS4, "zhoujianguo-0", conv, agent, backend,
                         msg_kickoff, 40,
                         cid=cid, injected=[(iids["address"], "profile", 1.0),
                                            (iids["industry"], "profile", 1.0),
                                            (iids["bid"], "event", None),
                                            (iids["meeting"], "preference", .8)])
        await insert_run(conn, tenant, NS4, "zhoujianguo-1", conv, agent, backend,
                         msg_moved, 3,
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
    name, desc, prompt = _agent_display()
    agent = await ensure_agent(
        p, tenant, "seed", name, desc,
        AgentConfig(system_prompt=prompt, tool_names=[], knowledge_base_ids=[]),
    )
    actor = await current_audit_actor(p, tenant)
    backend = settings.model_backend
    inserted = False
    for block in (seed_merged, seed_deleted, seed_decay, seed_pending):
        inserted = await block(p, tenant, agent, actor, backend) or inserted
    return inserted


if __name__ == "__main__":
    run_standalone(seed, lambda: T(zh="阶段4记忆治理演示数据", en="Phase 4 memory-governance demo data",
                                   zh_tw="階段4記憶治理演示資料", hi="चरण 4 मेमोरी-गवर्नेंस डेमो डेटा"))
