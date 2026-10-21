"""Phase 3 acceptance dataset: event memory and search (事件记忆与搜索).

Seeds the scenarios the Phase 3 acceptance report (A1~A7) exercises so the
console can show them on server startup:

- A1/AC-1  ticket lifecycle events written as ``ticket:{no}:created/resolved/
  closed`` items (``source_type='ticket'``, ``confidence=1.0``, TTL
  ``expires_at``), searchable via the console memory search (content_tsv is a
  stored generated column, so plain INSERTs are immediately searchable);
- A2/AC-2  ``commitment:{log_id}`` items with ``expires_at=NULL`` and
  ``source_ref='{ticket_id}:{log_id}'``;
- A3/AC-3  in-flight ``open_ticket:{no}`` items injected into runs — genuine
  ``memory_injection_log`` rows with ``section='event'`` and ``score=NULL``
  plus the matching ``memory.injected`` event counts;
- A4b      two equally relevant events (3 days vs ~150 days old) so the decay
  ranking puts the recent one first;
- A5/AC-5  an event past TTL soft-``expired`` (searchable nowhere);
- A6/AC-6  resolved/closed tickets whose ``open_ticket:`` row is soft-expired
  and disappears from the 【待跟进事项】 injection;
- R5       one ``memory.event.written`` audit per ticket event with the exact
  details shape emitted by ``memory/events.py``
  (``{event, items_written, pii_masked}``, actor ``memory``, resource = the
  ticket id).

Deterministic uuid5 ids under ``ns("phase3")``; each customer block is
skipped once its identity binding exists, so re-runs never duplicate rows.

Display strings are localized via ``seed_memory_i18n`` (AGENT_SEED_LOCALE);
identity keys stay constant so the locale only affects first-time seeding.
"""

from datetime import timedelta

from sqlalchemy import text

from agent_platform.apps.seed_memory_common import (
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
from agent_platform.apps.seed_memory_i18n import T
from agent_platform.modules.agent_config.service import AgentConfig

NS = ns("phase3")
TTL_DAYS = 180  # memory_event_ttl_days default


def ticket_uid(ticket_no: str) -> str:
    """Stand-in ticket id (the real writer stores tickets.id as source_ref)."""
    return uid(NS, "ticket", ticket_no)


async def insert_event_item(conn, tenant, iid, cid, key, content, *,
                            source_ref, created_h, updated_h=None, last_ref_h=None,
                            status="active", source_type="ticket", conf=1.0,
                            expires_at=None, rc=0):
    """One event item with the exact write shape of ``add_event_item`` plus the
    TTL column the common ``insert_item`` helper does not cover."""
    await conn.execute(
        text("""INSERT INTO memory_items(id,tenant_id,customer_id,type,key,content,confidence,status,
                source_type,source_ref,revision,last_referenced_at,reference_count,
                created_at,updated_at,protected,expires_at)
                VALUES(:id,:t,:c,'event',:k,:co,:cf,:st,:sty,:sr,1,:lra,:rc,:ca,:ua,false,:exp)
                ON CONFLICT DO NOTHING"""),
        {"id": iid, "t": tenant, "c": cid, "k": key, "co": content, "cf": conf, "st": status,
         "sty": source_type, "sr": source_ref,
         "lra": ts(last_ref_h if last_ref_h is not None else created_h),
         "rc": rc, "ca": ts(created_h),
         "ua": ts(updated_h if updated_h is not None else created_h),
         "exp": expires_at},
    )


def ttl(created_h: float):
    """``expires_at`` for TTL-managed event rows (commitments pass None)."""
    return ts(created_h) + timedelta(days=TTL_DAYS)


def ev(key, content, created_h, **kw):
    """Event-item spec dict (avoids the C408-flagged ``dict()`` call form)."""
    return {"key": key, "content": content, "created_h": created_h, **kw}


def _customers():
    """Phase-3 customer blocks; built per call so T() sees the active locale."""
    return [
        {
            # A1/A2/A4b/A5/A6: full lifecycle ticket + pending commitment + decay pair.
            "name": "chenjing", "external_id": "demo-p3-chenjing", "created_h": 40 * 24,
            "summary": T(
                zh="客户陈静（华宇家电连锁）：工单 TK-20260928 批次 B2310 空调冷媒不足已解决，补偿滤网套装承诺待兑现；历史批次问题可检索。",
                en="Customer Chen Jing (Huayu Home Appliance Chain): ticket TK-20260928 (batch B2310 air conditioner low on refrigerant) resolved; the promised complimentary filter set is still pending; historical batch issues are searchable.",
                zh_tw="客戶陳靜（華宇家電連鎖）：工單 TK-20260928 批次 B2310 空調冷媒不足已解決，補償濾網套裝承諾待兌現；歷史批次問題可檢索。",
                hi="ग्राहक Chen Jing (Huayu Home Appliance Chain): टिकट TK-20260928 में बैच B2310 के एयर कंडीशनर में रेफ्रिजरेंट की कमी का समाधान हो गया है; मुआवज़े के तौर पर वादा किया गया फ़िल्टर सेट अभी भेजा जाना बाकी है; पुराने बैच की समस्याएं खोजी जा सकती हैं।"),
            "profile": item("profile", "company",
                            T(zh="华宇家电连锁（华北 8 家门店）", en="Huayu Home Appliance Chain (8 stores in North China)",
                              zh_tw="華宇家電連鎖（華北 8 家門店）", hi="Huayu Home Appliance Chain (उत्तर चीन में 8 स्टोर)"), .97,
                            st="manual", rc=6, ch=40 * 24, uh=3 * 24),
            "events": [
                # Ticket lifecycle items — the exact keys/content of memory/events.py.
                ev(key="ticket:TK-20260928:created",
                     content=T(zh="工单 TK-20260928 已创建：客户反馈空调 KFR-35GW（批次 B2310）制冷效果差，要求安排上门检测",
                               en="Ticket TK-20260928 created: customer reported the KFR-35GW air conditioner (batch B2310) cools poorly and requested an on-site inspection",
                               zh_tw="工單 TK-20260928 已創建：客戶反饋空調 KFR-35GW（批次 B2310）製冷效果差，要求安排上門檢測",
                               hi="टिकट TK-20260928 बनाया गया: ग्राहक ने बताया कि KFR-35GW एयर कंडीशनर (बैच B2310) ठीक से ठंडा नहीं कर रहा है और ऑन-साइट जांच का अनुरोध किया"),
                     created_h=22 * 24, rc=3, expires_at=ttl(22 * 24)),
                ev(key="ticket:TK-20260928:resolved",
                     content=T(zh="工单 TK-20260928 已解决：上门检测确认批次 B2310 冷媒不足，已现场加注冷媒并补偿延保 6 个月",
                               en="Ticket TK-20260928 resolved: on-site inspection confirmed batch B2310 was low on refrigerant; refrigerant refilled on site and a 6-month warranty extension granted as compensation",
                               zh_tw="工單 TK-20260928 已解決：上門檢測確認批次 B2310 冷媒不足，已現場加注冷媒並補償延長保固 6 個月",
                               hi="टिकट TK-20260928 हल हुआ: ऑन-साइट जांच में बैच B2310 में रेफ्रिजरेंट की कमी की पुष्टि हुई; मौके पर ही रेफ्रिजरेंट भरा गया और मुआवज़े के रूप में 6 महीने की वारंटी बढ़ाई गई"),
                     created_h=3 * 24, rc=4, expires_at=ttl(3 * 24)),
                # AC-6: resolved ticket → in-flight row soft-expired, kept for audit.
                ev(key="open_ticket:TK-20260928",
                     content=T(zh="空调 KFR-35GW（批次 B2310）制冷效果差，要求安排上门检测",
                               en="KFR-35GW air conditioner (batch B2310) cools poorly; customer requested an on-site inspection",
                               zh_tw="空調 KFR-35GW（批次 B2310）製冷效果差，要求安排上門檢測",
                               hi="KFR-35GW एयर कंडीशनर (बैच B2310) ठीक से ठंडा नहीं कर रहा है; ग्राहक ने ऑन-साइट जांच का अनुरोध किया है"),
                     created_h=22 * 24, updated_h=3 * 24, status="expired", rc=2,
                     expires_at=ttl(22 * 24)),
                # AC-2: pending commitment — never expires, source_ref carries log id.
                ev(key="commitment:10001",
                     content=T(zh="48 小时内寄出补偿的滤网套装，物流单号将以短信同步客户",
                               en="Ship the complimentary filter set within 48 hours; the tracking number will be sent to the customer by SMS",
                               zh_tw="48 小時內寄出補償的濾網套裝，物流單號將以簡訊同步客戶",
                               hi="48 घंटों के भीतर मुआवज़े का फ़िल्टर सेट भेजा जाएगा; ट्रैकिंग नंबर ग्राहक को SMS से भेजा जाएगा"),
                     created_h=3 * 24 - 1, rc=3, expires_at=None,
                     source_ref=f"{ticket_uid('TK-20260928')}:10001"),
                # A4b (old half): equally relevant to “批次 B2310”, referenced ~150d ago.
                ev(key="evt-202606-batch",
                     content=T(zh="2026-06 历史反馈：批次 B2310 空调遥控器按键失灵，已登记补寄并回访确认",
                               en="2026-06 historical feedback: batch B2310 air conditioner remote control buttons failed; replacement registered and confirmed via follow-up call",
                               zh_tw="2026-06 歷史反饋：批次 B2310 空調遙控器按鍵失靈，已登記補寄並回訪確認",
                               hi="2026-06 की पुरानी शिकायत: बैच B2310 के एयर कंडीशनर के रिमोट कंट्रोल के बटन काम नहीं कर रहे थे; प्रतिस्थापन दर्ज किया गया और फ़ॉलो-अप कॉल में पुष्टि की गई"),
                     created_h=150 * 24, last_ref_h=150 * 24, rc=1,
                     source_type="extracted", conf=.9, expires_at=None,
                     source_ref="conv-p3-chenjing"),
                # AC-5: past TTL → soft-expired, invisible to search and injection.
                ev(key="ticket:TK-20260310:closed",
                     content=T(zh="工单 TK-20260310 已关闭：客户确认补寄配件已签收，无其他诉求",
                               en="Ticket TK-20260310 closed: customer confirmed receipt of the replacement parts, no further requests",
                               zh_tw="工單 TK-20260310 已關閉：客戶確認補寄配件已簽收，無其他訴求",
                               hi="टिकट TK-20260310 बंद हुआ: ग्राहक ने प्रतिस्थापन पार्ट्स मिलने की पुष्टि की, कोई और अनुरोध नहीं"),
                     created_h=200 * 24, updated_h=10 * 24, status="expired",
                     expires_at=ts(10 * 24)),
            ],
            "runs": [
                # AC-2/AC-3: pending commitment shows up in the 【待跟进事项】 section.
                (26, T(zh="你好，我是华宇家电的陈静，工单 TK-20260928 的空调修好了，但说好的滤网套装什么时候寄？",
                       en="Hi, this is Chen Jing from Huayu Home Appliances. The air conditioner from ticket TK-20260928 is fixed, but when will the promised filter set be shipped?",
                       zh_tw="你好，我是華宇家電的陳靜，工單 TK-20260928 的空調修好了，但說好的濾網套裝什麼時候寄？",
                       hi="नमस्ते, मैं Huayu Home Appliances की Chen Jing बोल रही हूँ। टिकट TK-20260928 वाला एयर कंडीशनर ठीक हो गया है, लेकिन वादा किया गया फ़िल्टर सेट कब भेजा जाएगा?"),
                 ["profile:company", "event:commitment:10001"]),
                # Console search demo: “批次 B2310” hits both the 3-day and 150-day events.
                (2, T(zh="另外帮我查下批次 B2310 之前还有没有其他问题记录。",
                      en="Also, please check whether batch B2310 had any other problem records before.",
                      zh_tw="另外幫我查一下批次 B2310 之前還有沒有其他問題記錄。",
                      hi="इसके अलावा, कृपया देखें कि बैच B2310 से जुड़ी पहले कोई और समस्या दर्ज है या नहीं।"),
                 ["profile:company", "event:commitment:10001"]),
            ],
            "audits": [
                # One memory.event.written per event task (R5): counts only, actor "memory".
                ("TK-20260928", 22 * 24, {"event": "created", "items_written": 2, "pii_masked": 0}),
                ("TK-20260928", 3 * 24, {"event": "resolved", "items_written": 1, "pii_masked": 0}),
                ("TK-20260928", 3 * 24 - 1, {"event": "commitment", "items_written": 1, "pii_masked": 0}),
            ],
        },
        {
            # A3/AC-3: in-flight ticket + fresh commitment → 待跟进事项 injection.
            "name": "liuyang", "external_id": "demo-p3-liuyang", "created_h": 15 * 24,
            "summary": T(
                zh="客户刘洋（宏达汽配）：在途工单 TK-20261019 订单 SO-8812 刹车片物流滞留，承运商加急中；承诺明早 10 点前电话回复物流轨迹。",
                en="Customer Liu Yang (Hongda Auto Parts): in-flight ticket TK-20261019 — order SO-8812 brake pads stuck in transit, carrier expediting; committed to call back with the latest tracking before 10 AM tomorrow.",
                zh_tw="客戶劉洋（宏達汽配）：在途工單 TK-20261019 訂單 SO-8812 剎車片物流滯留，承運商加急中；承諾明早 10 點前電話回覆物流軌跡。",
                hi="ग्राहक Liu Yang (Hongda Auto Parts): लंबित टिकट TK-20261019 — ऑर्डर SO-8812 के ब्रेक पैड ट्रांज़िट में अटके हैं, कैरियर प्राथमिकता से निपटा रहा है; कल सुबह 10 बजे से पहले फ़ोन पर लेटेस्ट ट्रैकिंग बताने का वादा किया गया है।"),
            "profile": item("profile", "company",
                            T(zh="宏达汽车配件有限公司", en="Hongda Auto Parts Co., Ltd.",
                              zh_tw="宏達汽車配件有限公司", hi="Hongda Auto Parts Co., Ltd."), .95,
                            st="manual", rc=4, ch=15 * 24, uh=5),
            "events": [
                ev(key="ticket:TK-20261019:created",
                     content=T(zh="工单 TK-20261019 已创建：客户反馈订单 SO-8812 一批刹车片物流滞留中转仓 3 天，要求加急并同步轨迹",
                               en="Ticket TK-20261019 created: customer reported order SO-8812 brake pads stuck at a transit warehouse for 3 days; requested expediting and tracking updates",
                               zh_tw="工單 TK-20261019 已創建：客戶反饋訂單 SO-8812 一批剎車片物流滯留中轉倉 3 天，要求加急並同步軌跡",
                               hi="टिकट TK-20261019 बनाया गया: ग्राहक ने बताया कि ऑर्डर SO-8812 के ब्रेक पैड 3 दिनों से ट्रांज़िट वेयरहाउस में अटके हैं; जल्दी डिलीवरी और ट्रैकिंग अपडेट का अनुरोध किया"),
                     created_h=2 * 24, rc=2, expires_at=ttl(2 * 24)),
                # AC-3: active in-flight row — the one the injection section renders.
                ev(key="open_ticket:TK-20261019",
                     content=T(zh="订单 SO-8812 刹车片物流滞留中转仓：承运商已加急处理，预计 48 小时内恢复运输",
                               en="Order SO-8812 brake pads stuck at transit warehouse: carrier is expediting, transport expected to resume within 48 hours",
                               zh_tw="訂單 SO-8812 剎車片物流滯留中轉倉：承運商已加急處理，預計 48 小時內恢復運輸",
                               hi="ऑर्डर SO-8812 के ब्रेक पैड ट्रांज़िट वेयरहाउस में अटके हैं: कैरियर इसे प्राथमिकता से निपटा रहा है, 48 घंटों के भीतर परिवहन फिर से शुरू होने की उम्मीद है"),
                     created_h=2 * 24, updated_h=5, rc=5, expires_at=ttl(2 * 24)),
                ev(key="commitment:10007",
                     content=T(zh="明天上午 10 点前电话回复加急件最新物流轨迹",
                               en="Call back with the latest tracking on the expedited shipment before 10 AM tomorrow",
                               zh_tw="明天上午 10 點前電話回覆加急件最新物流軌跡",
                               hi="कल सुबह 10 बजे से पहले फ़ोन पर एक्सपेडिटेड शिपमेंट की लेटेस्ट ट्रैकिंग बताई जाएगी"),
                     created_h=5, rc=2, expires_at=None,
                     source_ref=f"{ticket_uid('TK-20261019')}:10007"),
                ev(key="evt-202609-breakage",
                     content=T(zh="2026-09 一批火花塞到货破损，已完成退换货并升级防震包装",
                               en="2026-09 a batch of spark plugs arrived damaged; return/exchange completed and shock-proof packaging upgraded",
                               zh_tw="2026-09 一批火星塞到貨破損，已完成退換貨並升級防震包裝",
                               hi="2026-09 में स्पार्क प्लग का एक बैच टूटा हुआ पहुंचा; रिटर्न/एक्सचेंज पूरा हो गया और शॉक-प्रूफ़ पैकेजिंग अपग्रेड कर दी गई"),
                     created_h=35 * 24, rc=1, source_type="extracted", conf=.9,
                     expires_at=None, source_ref="conv-p3-liuyang"),
            ],
            "runs": [
                (5, T(zh="我是宏达汽配的刘洋，SO-8812 那批刹车片到底什么时候能到？产线停产待料了！",
                      en="This is Liu Yang from Hongda Auto Parts. When will the SO-8812 brake pads actually arrive? Our production line is halted waiting for them!",
                      zh_tw="我是宏達汽配的劉洋，SO-8812 那批剎車片到底什麼時候能到？產線停產待料了！",
                      hi="मैं Hongda Auto Parts का Liu Yang बोल रहा हूँ। SO-8812 के ब्रेक पैड आख़िर कब पहुंचेंगे? हमारी प्रोडक्शन लाइन माल के इंतज़ार में ठप है!"),
                 ["profile:company", "event:open_ticket:TK-20261019", "event:commitment:10007"]),
                (1, T(zh="物流有新进展麻烦第一时间通知我，电话或者微信都行。",
                      en="Please notify me as soon as there's any logistics update — phone or WeChat both work.",
                      zh_tw="物流有新進展麻煩第一時間通知我，電話或者微信都行。",
                      hi="लॉजिस्टिक्स में कोई नई प्रगति हो तो कृपया मुझे सबसे पहले सूचित करें — फ़ोन या WeChat, दोनों चलेंगे।"),
                 ["profile:company", "event:open_ticket:TK-20261019", "event:commitment:10007"]),
            ],
            "audits": [
                ("TK-20261019", 2 * 24, {"event": "created", "items_written": 2, "pii_masked": 0}),
                ("TK-20261019", 5, {"event": "commitment", "items_written": 1, "pii_masked": 0}),
            ],
        },
        {
            # A6/UAT: ticket closed → open_ticket and commitment both expired, the
            # 待跟进事项 section disappears; conclusions stay searchable (AC-1).
            "name": "sunqian", "external_id": "demo-p3-sunqian", "created_h": 20 * 24,
            "summary": None,
            "profile": item("profile", "company",
                            T(zh="悦读时光书店", en="Reading Time Bookstore",
                              zh_tw="悅讀時光書店", hi="Reading Time Bookstore"), .9,
                            sr="conv-p3-sunqian", rc=2, ch=20 * 24, uh=7 * 24),
            "events": [
                ev(key="ticket:TK-20261010:created",
                     content=T(zh="工单 TK-20261010 已创建：企业团购 200 册图书到货缺 12 册，客户要求补发",
                               en="Ticket TK-20261010 created: corporate group order of 200 books arrived 12 short; customer requested re-shipment",
                               zh_tw="工單 TK-20261010 已創建：企業團購 200 冊圖書到貨缺 12 冊，客戶要求補發",
                               hi="टिकट TK-20261010 बनाया गया: कॉर्पोरेट ग्रुप ऑर्डर की 200 किताबों में से 12 कम पहुंचीं; ग्राहक ने दोबारा भेजने का अनुरोध किया"),
                     created_h=10 * 24, rc=1, expires_at=ttl(10 * 24)),
                ev(key="ticket:TK-20261010:resolved",
                     content=T(zh="工单 TK-20261010 已解决：缺册 12 册已补发，顺丰 SF6612 单号已短信同步客户",
                               en="Ticket TK-20261010 resolved: the 12 missing books re-shipped; SF Express SF6612 tracking number sent to the customer by SMS",
                               zh_tw="工單 TK-20261010 已解決：缺冊 12 冊已補發，順豐 SF6612 單號已簡訊同步客戶",
                               hi="टिकट TK-20261010 हल हुआ: 12 कम किताबें दोबारा भेज दी गईं; SF Express का SF6612 ट्रैकिंग नंबर ग्राहक को SMS से भेजा गया"),
                     created_h=8 * 24, rc=2, expires_at=ttl(8 * 24)),
                ev(key="ticket:TK-20261010:closed",
                     content=T(zh="工单 TK-20261010 已关闭：客户确认补发图书已全部签收，无其他诉求",
                               en="Ticket TK-20261010 closed: customer confirmed all re-shipped books received, no further requests",
                               zh_tw="工單 TK-20261010 已關閉：客戶確認補發圖書已全部簽收，無其他訴求",
                               hi="टिकट TK-20261010 बंद हुआ: ग्राहक ने दोबारा भेजी गई सभी किताबें मिलने की पुष्टि की, कोई और अनुरोध नहीं"),
                     created_h=7 * 24, rc=1, expires_at=ttl(7 * 24)),
                ev(key="open_ticket:TK-20261010",
                     content=T(zh="企业团购 200 册图书到货缺 12 册，客户要求补发",
                               en="Corporate group order of 200 books arrived 12 short; customer requested re-shipment",
                               zh_tw="企業團購 200 冊圖書到貨缺 12 冊，客戶要求補發",
                               hi="कॉर्पोरेट ग्रुप ऑर्डर की 200 किताबों में से 12 कम पहुंचीं; ग्राहक ने दोबारा भेजने का अनुरोध किया"),
                     created_h=10 * 24, updated_h=8 * 24, status="expired",
                     expires_at=ttl(10 * 24)),
                # 承诺已兑现：工单关闭联动置 expired（UAT 核对项）。
                ev(key="commitment:10012",
                     content=T(zh="补发图书签收后电话回访确认无缺册",
                               en="After the re-shipped books are signed for, call to confirm no copies are missing",
                               zh_tw="補發圖書簽收後電話回訪確認無缺冊",
                               hi="दोबारा भेजी गई किताबें मिलने के बाद फ़ोन पर पुष्टि करें कि कोई किताब कम तो नहीं है"),
                     created_h=8 * 24, updated_h=7 * 24, status="expired", expires_at=None,
                     source_ref=f"{ticket_uid('TK-20261010')}:10012"),
            ],
            "runs": [
                # AC-6: no open ticket/commitment left → events count is 0, only the
                # profile section injects; the resolved conclusion stays searchable.
                (7 * 24, T(zh="补发的图书都收到了，没问题了，谢谢。",
                           en="All the re-shipped books arrived, everything's fine now, thanks.",
                           zh_tw="補發的圖書都收到了，沒問題了，謝謝。",
                           hi="दोबारा भेजी गई सभी किताबें मिल गईं, अब सब ठीक है, धन्यवाद।"),
                 ["profile:company"]),
            ],
            "audits": [
                ("TK-20261010", 10 * 24, {"event": "created", "items_written": 2, "pii_masked": 0}),
                ("TK-20261010", 8 * 24, {"event": "resolved", "items_written": 1, "pii_masked": 0}),
                ("TK-20261010", 7 * 24, {"event": "closed", "items_written": 1, "pii_masked": 0}),
            ],
        },
    ]


async def seed_customer(c, engine, model_backend, tenant, agent) -> bool:
    """Insert one customer block; skip entirely once its identity exists."""
    ext = c["external_id"]
    async with engine.connect() as conn:
        if await identity_bound(conn, tenant, "web", ext):
            return False
    cid = uid(NS, "customer", c["name"])
    conv_id = uid(NS, "conversation", ext)
    profile_id = uid(NS, "item", f'{c["name"]}:profile:company')
    event_ids = {e["key"]: uid(NS, "item", f'{c["name"]}:event:{e["key"]}') for e in c["events"]}
    id_by_label = {"profile:company": profile_id,
                   **{f"event:{k}": v for k, v in event_ids.items()}}

    async with engine.begin() as conn:
        last_run_h = min(h for h, _, _ in c["runs"])
        await insert_customer(conn, tenant, cid, c["created_h"], last_run_h)
        await insert_identity(conn, tenant, uid(NS, "identity", ext), cid, ext, c["created_h"])
        await insert_item(conn, tenant, profile_id, cid, c["profile"], last_referenced_h=last_run_h)
        for e in c["events"]:
            await insert_event_item(
                conn, tenant, event_ids[e["key"]], cid, e["key"], e["content"],
                source_ref=e.get("source_ref") or ticket_uid(
                    e["key"].split(":")[1] if ":" in e["key"] else e["key"]),
                created_h=e["created_h"], updated_h=e.get("updated_h"),
                last_ref_h=e.get("last_ref_h"), status=e.get("status", "active"),
                source_type=e.get("source_type", "ticket"), conf=e.get("conf", 1.0),
                expires_at=e.get("expires_at"), rc=e.get("rc", 0),
            )
        await insert_conversation(
            conn, tenant, conv_id, ext, pairs([msg for _, msg, _ in c["runs"]]),
            c["runs"][0][0] + 0.1, last_run_h, agent["id"], summary=c["summary"], cid=cid,
        )
        for n, (h, msg, injected) in enumerate(c["runs"]):
            await insert_run(
                conn, tenant, NS, f"{ext}:{n}", conv_id, agent, model_backend, msg, h,
                cid=cid,
                injected=[
                    (id_by_label[label],
                     label.split(":")[0],
                     # Event rows carry no ranking score (recency-ordered), so
                     # section='event' logs NULL exactly like the runtime.
                     None if label.startswith("event:") else c["profile"]["conf"])
                    for label in injected
                ],
            )
        for ticket_no, hours_ago, details in c["audits"]:
            await insert_audit(conn, tenant, "memory", "memory.event.written",
                               ticket_uid(ticket_no), details, hours_ago)
    return True


async def seed(runtime, settings) -> bool:
    """Idempotent entrypoint: True only when at least one customer was inserted."""
    p = runtime.platform
    tenant = settings.tenant_id
    actor = await current_audit_actor(p, tenant)
    agent = await ensure_agent(
        p, tenant, actor,
        T(zh="事件检索演示助手", en="Event Retrieval Demo Assistant",
          zh_tw="事件檢索演示助手", hi="इवेंट रिट्रीवल डेमो असिस्टेंट"),
        T(zh="阶段3事件记忆与搜索验收演示（工单事件写入、待跟进事项注入、事件检索）",
          en="Phase 3 event memory & search acceptance demo (ticket event writes, follow-up item injection, event retrieval)",
          zh_tw="階段3事件記憶與搜尋驗收演示（工單事件寫入、待跟進事項注入、事件檢索）",
          hi="चरण 3 इवेंट मेमोरी और खोज स्वीकृति डेमो (टिकट इवेंट लेखन, फ़ॉलो-अप आइटम इंजेक्शन, इवेंट खोज)"),
        AgentConfig(
            system_prompt=T(zh="你是一名企业售后客服助手。优先关注客户的在途工单与未兑现承诺，回答时引用历史工单的处理结论。",
                            en="You are an enterprise after-sales support assistant. Prioritize the customer's in-flight tickets and unfulfilled commitments, and cite the resolutions of historical tickets when answering.",
                            zh_tw="你是一名企業售後客服助手。優先關注客戶的在途工單與未兌現承諾，回答時引用歷史工單的處理結論。",
                            hi="आप एक एंटरप्राइज़ आफ़्टर-सेल्स सपोर्ट असिस्टेंट हैं। ग्राहक के लंबित टिकटों और अधूरे वादों को प्राथमिकता दें, और जवाब देते समय पुराने टिकटों के समाधान का हवाला दें।"),
            tool_names=[],
            knowledge_base_ids=[],
        ),
    )
    inserted = False
    for cust in _customers():
        if await seed_customer(cust, p.engine, settings.model_backend, tenant, agent):
            inserted = True
    return inserted


if __name__ == "__main__":
    run_standalone(seed, lambda: T(zh="阶段3事件记忆与搜索演示数据", en="Phase 3 event-memory & search demo data",
                                   zh_tw="階段3事件記憶與搜尋演示數據", hi="चरण 3 इवेंट-मेमोरी और खोज डेमो डेटा"))
