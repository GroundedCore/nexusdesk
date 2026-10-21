"""Seed the memory-visualization demo dataset; safe to run again.

Quickstart boots straight into a console where the customer-memory views
(list stats, 8-tab customer detail, injection records, item lifecycle) have
realistic data to show: four demo customers with curated profile/preference/
event/flag items, pending suggestions, service marks, audit history and — the
Phase 6 centerpiece — genuine-shaped ``memory_injection_log`` rows plus the
matching ``memory.injected`` run events, so every explainability view works
out of the box.

Everything is deterministic (uuid5 ids) and each customer block is skipped
once its identity binding exists, so re-runs never duplicate rows. Runs and
logs are seeded as *results* — the runtime worker is not up while bootstrap
seeds, so conversations cannot be produced live at this stage.

Display strings are localized via ``seed_memory_i18n`` (AGENT_SEED_LOCALE);
identity keys stay constant so the locale only affects first-time seeding.
"""

import asyncio
import json
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid5

from sqlalchemy import text

from agent_platform.apps.seed_memory_common import default_reply
from agent_platform.apps.seed_memory_i18n import T, set_locale
from agent_platform.modules.agent_config.service import AgentConfig, AgentDraft
from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.modules.model_gateway.contracts import Connection, Model, Profile
from agent_platform.platform.persistence.store import one
from agent_platform.settings import Settings

NS = UUID("3f8b2a64-1c4a-4e2b-9b7e-7d5c6a1f0001")  # fixed namespace → deterministic demo ids


def uid(kind: str, name: str) -> str:
    return str(uuid5(NS, f"{kind}:{name}"))


NOW = datetime.now(UTC)


def ts(hours: float) -> datetime:
    return NOW - timedelta(hours=hours)


# Item tuple: (type, key, content, confidence, status, source_type, source_ref,
#              protected, reference_count, created_h, updated_h, prev_content, revision)
def I(t, k, content, conf=0.9, status="active", st="extracted", sr=None, prot=False,
      rc=0, ch=24, uh=2, prev=None, rev=1):
    return {"t": t, "k": k, "content": content, "conf": conf, "status": status, "st": st, "sr": sr,
                "prot": prot, "rc": rc, "ch": ch, "uh": uh, "prev": prev, "rev": rev}


def _customers():
    """Demo customer blocks; built per call so T() sees the active locale."""
    return [
        {
            "name": "zhangwei", "external_id": "demo-zhangwei", "created_h": 45 * 24,
            "summary": T(
                zh="客户张伟（星辰科技，金牌年框客户）：跟进物流专线升级工单 TK-20261018 与 Q3 对账单承诺；已确认明年 Q1 追加 15% 采购预算；偏好简洁专业、结论先行的沟通。",
                en="Customer Zhang Wei (Starlight Technologies, gold-tier annual-frame account): tracking logistics line upgrade ticket TK-20261018 and the Q3 statement commitment; confirmed a 15% Q1 procurement budget increase for next year; prefers concise, professional, conclusion-first communication.",
                zh_tw="客戶張偉（星辰科技，金牌年框客戶）：跟進物流專線升級工單 TK-20261018 與 Q3 對帳單承諾；已確認明年 Q1 追加 15% 採購預算；偏好簡潔專業、結論先行的溝通。",
                hi="ग्राहक Zhang Wei (Starlight Technologies, गोल्ड-टियर वार्षिक फ्रेम खाता): लॉजिस्टिक्स लाइन अपग्रेड टिकट TK-20261018 और Q3 स्टेटमेंट प्रतिबद्धता की निगरानी जारी; अगले वर्ष की Q1 खरीद बजट में 15% वृद्धि की पुष्टि हो चुकी है; संक्षिप्त, पेशेवर और निष्कर्ष-पहले संचार पसंद करते हैं।"),
            "items": [
                I("profile", "company", T(zh="星辰科技有限公司", en="Starlight Technologies Inc.", zh_tw="星辰科技有限公司", hi="Starlight Technologies Inc."), .98, st="manual", rc=12, ch=40 * 24, uh=20),
                I("profile", "industry", T(zh="跨境电商（主营 3C 配件出海欧美）", en="Cross-border e-commerce (3C accessories exported to EU/US)", zh_tw="跨境電商（主營 3C 配件出海歐美）", hi="क्रॉस-बॉर्डर ई-कॉमर्स (3C एक्सेसरीज़ का EU/US में निर्यात)"), .95, sr="conv-demo-001", rc=8, ch=38 * 24, uh=48),
                I("profile", "role", T(zh="运营总监，对接采购与售后决策", en="Operations Director, owns procurement and after-sales decisions", zh_tw="營運總監，對接採購與售後決策", hi="ऑपरेशंस डायरेक्टर, खरीद और बिक्री-पश्चात निर्णयों के प्रभारी"), .92, sr="conv-demo-001", rc=6, ch=35 * 24, uh=72),
                I("profile", "tier", T(zh="金牌客户（年框合同）", en="Gold-tier customer (annual frame contract)", zh_tw="金牌客戶（年框合同）", hi="गोल्ड-टियर ग्राहक (वार्षिक फ्रेम अनुबंध)"), 1.0, st="manual", prot=True, rc=15, ch=30 * 24, uh=24),
                I("preference", "contact_channel", T(zh="工作时间优先微信沟通，紧急事项电话", en="Prefers WeChat during work hours; phone for urgent matters", zh_tw="工作時間優先微信溝通，緊急事項電話", hi="कार्य समय में WeChat पर संपर्क पसंद; जरूरी मामलों के लिए फोन"), .9, sr="conv-demo-002", rc=9, ch=28 * 24, uh=24),
                I("preference", "tone", T(zh="偏好简洁专业的回复，禁止使用表情符号，数据结论先行", en="Prefers concise professional replies; no emojis; data conclusions first", zh_tw="偏好簡潔專業的回覆，禁止使用表情符號，數據結論先行", hi="संक्षिप्त पेशेवर उत्तर पसंद; इमोजी वर्जित; डेटा निष्कर्ष सबसे पहले"), .9, sr="conv-demo-002",
                  rc=7, ch=25 * 24, uh=26, prev=T(zh="偏好简洁专业的回复，少用表情符号", en="Prefers concise professional replies, minimal emojis", zh_tw="偏好簡潔專業的回覆，少用表情符號", hi="संक्षिप्त पेशेवर उत्तर पसंद, इमोजी का न्यूनतम उपयोग"), rev=2),
                I("preference", "report", T(zh="每周一上午发送上周数据周报", en="Send last week's data report every Monday morning", zh_tw="每週一上午發送上周數據週報", hi="हर सोमवार सुबह पिछले सप्ताह की डेटा रिपोर्ट भेजें"), .88, st="manual", rc=5, ch=20 * 24, uh=26),
                I("preference", "budget_hint", T(zh="提到明年 Q1 可能追加 15% 采购预算（已电话确认）", en="Mentioned a possible 15% Q1 procurement budget increase next year (confirmed by phone)", zh_tw="提到明年 Q1 可能追加 15% 採購預算（已電話確認）", hi="अगले वर्ष Q1 खरीद बजट में 15% संभावित वृद्धि का उल्लेख किया (फोन पर पुष्टि हो चुकी)"), .95, sr="conv-demo-004", ch=3 * 24, uh=24),
                I("profile", "new_contact", T(zh="新采购经理姓陈，年后到岗（待确认）", en="New procurement manager surnamed Chen, starts after New Year (unconfirmed)", zh_tw="新採購經理姓陳，年後到崗（待確認）", hi="नई खरीद प्रबंधक Chen उपनाम की हैं, नववर्ष के बाद पदभार संभालेंगी (पुष्टि बाकी)"), .55, status="pending", sr="conv-demo-004", ch=2 * 24, uh=2 * 24),
                I("event", "evt-202609-logistics", T(zh="2026-09 订单 #SO-20260912 物流延迟投诉，已补偿 5% 货款并升级专线物流", en="2026-09 logistics delay complaint on order #SO-20260912; compensated 5% of goods value and upgraded to dedicated-line shipping", zh_tw="2026-09 訂單 #SO-20260912 物流延遲投訴，已補償 5% 貨款並升級專線物流", hi="2026-09 में ऑर्डर #SO-20260912 पर लॉजिस्टिक्स देरी की शिकायत; माल मूल्य का 5% मुआवज़ा दिया गया और डेडिकेटेड-लाइन शिपिंग में अपग्रेड किया गया"), .95, sr="conv-demo-003", rc=4, ch=32 * 24, uh=32 * 24),
                I("event", "evt-202610-renewal", T(zh="2026-10 完成年框续约 ¥360,000，续约窗口每年 12 月", en="2026-10 annual frame renewal completed at ¥360,000; renewal window every December", zh_tw="2026-10 完成年框續約 ¥360,000，續約窗口每年 12 月", hi="2026-10 में ¥360,000 पर वार्षिक फ्रेम नवीनीकरण पूर्ण; नवीनीकरण विंडो हर दिसंबर"), .97, st="manual", rc=3, ch=12 * 24, uh=12 * 24),
                I("event", "open_ticket:TK-20261018", T(zh="物流专线升级申请已提交，等待承运商确认排期（预计 10-22 前回复）", en="Logistics line upgrade request submitted, awaiting carrier schedule confirmation (reply expected before 10-22)", zh_tw="物流專線升級申請已提交，等待承運商確認排期（預計 10-22 前回覆）", hi="लॉजिस्टिक्स लाइन अपग्रेड अनुरोध सबमिट किया गया, कैरियर से शेड्यूल पुष्टि की प्रतीक्षा (10-22 से पहले उत्तर अपेक्षित)"), .95, st="manual", sr="conv-demo-004", rc=2, ch=26, uh=26),
                I("event", "commitment:2026-10-19", T(zh="承诺客户 10-25 前提供 Q3 专属对账单与返利明细", en="Committed to deliver the Q3 dedicated statement and rebate details before 10-25", zh_tw="承諾客戶 10-25 前提供 Q3 專屬對帳單與返利明細", hi="10-25 से पहले Q3 समर्पित स्टेटमेंट और रिबेट विवरण देने की प्रतिबद्धता"), .9, sr="conv-demo-004", rc=1, ch=26, uh=26),
                I("flag", "vip", T(zh="重点客户：年框 ¥36 万，续约窗口 2026-12，升级需求 2 小时内响应", en="Key account: ¥360k annual frame, renewal window 2026-12, respond to escalations within 2 hours", zh_tw="重點客戶：年框 ¥36 萬，續約窗口 2026-12，升級需求 2 小時內回應", hi="प्रमुख खाता: ¥360k वार्षिक फ्रेम, नवीनीकरण विंडो 2026-12, एस्केलेशन पर 2 घंटे के भीतर प्रतिक्रिया"), 1.0, st="human", prot=True, ch=72, uh=72),
            ],
            "audits": [
                ("memory.extract.applied", None, 600, {"conversation_id": "conv-demo-001", "input_messages": 6, "added": 3, "updated": 0, "pending": 0, "dropped": 0, "pii_masked": 0, "usage": {}}),
                ("memory.flag.upserted", "flag:vip", 72, None),
                ("memory.item.edited", "preference:tone", 26, None),
                ("memory.item.deleted", "preference:report", 27.5, None),
                ("memory.item.restored", "preference:report", 27, None),
                ("memory.item.confirmed", "preference:budget_hint", 24, None),
            ],
            "runs": [
                (34, T(zh="你好，我是星辰科技的张伟，想问下上周说的物流专线进展怎么样了？",
                       en="Hi, this is Zhang Wei from Starlight Technologies. Any progress on the dedicated logistics line we discussed last week?",
                       zh_tw="你好，我是星辰科技的張偉，想問下上周說的物流專線進展怎麼樣了？",
                       hi="नमस्ते, मैं Starlight Technologies से Zhang Wei बोल रहा हूँ। पिछले हफ्ते चर्चा हुई डेडिकेटेड लॉजिस्टिक्स लाइन की क्या प्रगति है?"),
                 ["flag:vip", "profile:company", "profile:industry", "profile:role", "profile:tier",
                  "preference:contact_channel", "preference:tone", "preference:report"]),
                (29, T(zh="另外帮我把下周一的周报提前到周日晚上发可以吗？",
                       en="Also, could you send next Monday's weekly report earlier, on Sunday evening?",
                       zh_tw="另外幫我把下周一的週報提前到週日晚上發可以嗎？",
                       hi="और क्या आप अगले सोमवार की साप्ताहिक रिपोर्ट पहले, रविवार शाम को भेज सकते हैं?"),
                 ["flag:vip", "profile:company", "profile:tier", "preference:report", "preference:contact_channel"]),
                (22, T(zh="我们明年 Q1 的采购计划定了，追加 15% 预算，帮我记录下。",
                       en="Our Q1 procurement plan for next year is set — a 15% budget increase. Please note it down.",
                       zh_tw="我們明年 Q1 的採購計劃定了，追加 15% 預算，幫我記錄下。",
                       hi="अगले वर्ष की Q1 खरीद योजना तय हो गई है — 15% बजट वृद्धि। कृपया इसे दर्ज कर लें।"),
                 ["flag:vip", "profile:company", "profile:tier", "preference:budget_hint"]),
                (20, T(zh="回复的时候直接给我结论就行，别整那些花里胡哨的。",
                       en="Just give me the conclusion in your replies — skip the fluff.",
                       zh_tw="回覆的時候直接給我結論就行，別整那些花裡胡哨的。",
                       hi="उत्तर में सीधे निष्कर्ष बता दें — फालतू बातें न करें।"),
                 ["flag:vip", "preference:tone", "profile:tier"]),
                (6, T(zh="我的工单 TK-20261018 现在什么进度？对账单也别忘了。",
                      en="What's the status of my ticket TK-20261018? And don't forget the statement.",
                      zh_tw="我的工單 TK-20261018 現在什麼進度？對帳單也別忘了。",
                      hi="मेरे टिकट TK-20261018 की अभी क्या स्थिति है? और स्टेटमेंट भी मत भूलिएगा।"),
                 ["flag:vip", "event:open_ticket:TK-20261018", "event:commitment:2026-10-19",
                  "profile:company", "profile:tier"]),
                (2, T(zh="周报按老样子发就行，另外陈经理到岗后跟我同步下。",
                      en="Send the weekly report as usual. Also, sync with me once Manager Chen starts.",
                      zh_tw="週報按老樣子發就行，另外陳經理到崗後跟我同步下。",
                      hi="साप्ताहिक रिपोर्ट हमेशा की तरह भेज दीजिए। और Manager Chen के पदभार संभालते ही मुझे अपडेट कर दीजिएगा।"),
                 ["flag:vip", "profile:company", "preference:contact_channel", "preference:tone"]),
            ],
        },
        {
            "name": "lina", "external_id": "demo-lina", "created_h": 25 * 24,
            "summary": None,
            "items": [
                I("profile", "company", T(zh="蓝海贸易有限公司", en="Blue Ocean Trading Co.", zh_tw="藍海貿易有限公司", hi="Blue Ocean Trading Co."), .96, st="manual", rc=5, ch=25 * 24, uh=48),
                I("profile", "industry", T(zh="零售连锁（华东 12 家门店）", en="Retail chain (12 stores in East China)", zh_tw="零售連鎖（華東 12 家門店）", hi="रिटेल चेन (पूर्वी चीन में 12 स्टोर)"), .9, sr="conv-demo-005", rc=3, ch=24 * 24, uh=72),
                I("preference", "invoice", T(zh="需要增值税专用发票，抬头蓝海贸易有限公司", en="Requires VAT special invoice, titled Blue Ocean Trading Co.", zh_tw="需要增值稅專用發票，抬頭藍海貿易有限公司", hi="VAT विशेष चालान आवश्यक, Blue Ocean Trading Co. के नाम पर"), .93, sr="conv-demo-005", rc=6, ch=22 * 24, uh=25),
                I("preference", "callback", T(zh="希望售后问题 24 小时内电话回访", en="Wants a phone follow-up within 24 hours for after-sales issues", zh_tw="希望售後問題 24 小時內電話回訪", hi="बिक्री-पश्चात समस्याओं पर 24 घंटे के भीतर फोन फॉलो-अप चाहते हैं"), .7, status="deleted", sr="conv-demo-006", ch=4 * 24, uh=23),
                I("event", "evt-20261015-quality", T(zh="2026-10-15 反馈批次 #B2310 产品外观瑕疵，已安排补寄，待回访确认", en="2026-10-15 reported cosmetic defects in batch #B2310; replacement shipped, follow-up confirmation pending", zh_tw="2026-10-15 反饋批次 #B2310 產品外觀瑕疵，已安排補寄，待回訪確認", hi="2026-10-15 को बैच #B2310 में उत्पाद की बाहरी खामियों की सूचना दी; रिप्लेसमेंट भेजा गया, फॉलो-अप पुष्टि लंबित"), .94, sr="conv-demo-006", rc=2, ch=5 * 24, uh=5 * 24),
                I("event", "open_ticket:TK-20261015", T(zh="批次 B2310 瑕疵补寄工单：已发货，顺丰 SF1388 待签收回访", en="Batch B2310 defect replacement ticket: shipped via SF Express SF1388, awaiting delivery confirmation call", zh_tw="批次 B2310 瑕疵補寄工單：已發貨，順豐 SF1388 待簽收回訪", hi="बैच B2310 खामी रिप्लेसमेंट टिकट: SF Express SF1388 से भेजा गया, डिलीवरी पुष्टि कॉल की प्रतीक्षा में"), .94, sr="conv-demo-006", rc=1, ch=26, uh=4),
                I("flag", "complaint_risk", T(zh="近 30 天两次投诉，回复措辞需谨慎，避免承诺交期", en="Two complaints in the last 30 days; phrase replies carefully, avoid promising delivery dates", zh_tw="近 30 天兩次投訴，回覆措辭需謹慎，避免承諾交期", hi="पिछले 30 दिनों में दो शिकायतें; उत्तर सावधानी से तैयार करें, डिलीवरी तिथि का वादा न करें"), 1.0, st="human", prot=True, ch=50, uh=50),
            ],
            "audits": [
                ("memory.extract.applied", None, 550, {"conversation_id": "conv-demo-005", "input_messages": 4, "added": 2, "updated": 0, "pending": 1, "dropped": 0, "pii_masked": 0, "usage": {}}),
                ("memory.flag.upserted", "flag:complaint_risk", 50, None),
                ("memory.item.rejected", "preference:callback", 23, None),
            ],
            "runs": [
                (28, T(zh="我是蓝海贸易的李娜，批次 B2310 的补寄发出了吗？",
                       en="This is Li Na from Blue Ocean Trading. Has the replacement for batch B2310 been shipped?",
                       zh_tw="我是藍海貿易的李娜，批次 B2310 的補寄發出了嗎？",
                       hi="मैं Blue Ocean Trading से Li Na बोल रही हूँ। बैच B2310 का रिप्लेसमेंट भेज दिया गया क्या?"),
                 ["flag:complaint_risk", "profile:company", "profile:industry", "preference:invoice"]),
                (25, T(zh="这次的发票记得开专票，跟上次一样。",
                       en="Remember to issue a VAT special invoice this time, same as before.",
                       zh_tw="這次的發票記得開專票，跟上次一樣。",
                       hi="इस बार भी VAT विशेष चालान बनाना याद रखें, पिछली बार की तरह।"),
                 ["flag:complaint_risk", "preference:invoice", "profile:company"]),
                (21, T(zh="处理完麻烦给我回个电话确认下。",
                       en="Please call me to confirm once it's handled.",
                       zh_tw="處理完麻煩給我回個電話確認下。",
                       hi="काम पूरा होने पर कृपया मुझे फोन करके पुष्टि कर दीजिएगा।"),
                 ["flag:complaint_risk", "profile:company"]),
                (4, T(zh="工单 TK-20261015 的补寄件我已经收到了，回访确认一下。",
                      en="I've received the replacement from ticket TK-20261015 — confirming receipt for the follow-up.",
                      zh_tw="工單 TK-20261015 的補寄件我已經收到了，回訪確認一下。",
                      hi="टिकट TK-20261015 का रिप्लेसमेंट मुझे मिल गया है — फॉलो-अप के लिए प्राप्ति की पुष्टि कर रही हूँ।"),
                 ["flag:complaint_risk", "event:open_ticket:TK-20261015", "profile:company"]),
            ],
        },
        {
            "name": "wangqiang", "external_id": "demo-wangqiang", "created_h": 60 * 24,
            "summary": None,
            "items": [
                I("profile", "company", T(zh="鑫源电子商行", en="Xinyuan Electronics", zh_tw="鑫源電子商行", hi="Xinyuan Electronics"), .8, sr="conv-demo-007", rc=2, ch=60 * 24, uh=10 * 24),
                I("preference", "discount", T(zh="多次索要超额折扣（疑似套利，置信度低）", en="Repeatedly requests excessive discounts (suspected arbitrage, low confidence)", zh_tw="多次索要超額折扣（疑似套利，置信度低）", hi="बार-बार अत्यधिक छूट की मांग (संदिग्ध आर्बिट्राज, कम विश्वास स्तर)"), .35, sr="conv-demo-007", rc=1, ch=55 * 24, uh=20 * 24),
                I("event", "evt-202608-dispute", T(zh="2026-08 一笔退款纠纷进入平台仲裁", en="2026-08 a refund dispute entered platform arbitration", zh_tw="2026-08 一筆退款糾紛進入平台仲裁", hi="2026-08 में एक रिफंड विवाद प्लेटफ़ॉर्म मध्यस्थता में चला गया"), .75, sr="conv-demo-007", rc=2, ch=58 * 24, uh=30 * 24),
                I("flag", "blacklist", T(zh="恶意退款仲裁中：禁止自动承诺赔偿与折扣，一律转人工", en="Malicious refund arbitration in progress: no auto-promised compensation or discounts; always hand off to a human", zh_tw="惡意退款仲裁中：禁止自動承諾賠償與折扣，一律轉人工", hi="दुर्भावनापूर्ण रिफंड मध्यस्थता जारी: स्वतः मुआवज़ा या छूट का वादा न करें; हमेशा मानव एजेंट को सौंपें"), 1.0, st="human", prot=True, ch=48, uh=48),
            ],
            "audits": [
                ("memory.flag.upserted", "flag:blacklist", 48, None),
            ],
            "runs": [
                (18, T(zh="上次那个退款仲裁结果出来没？我要求全额赔偿！",
                       en="Is the refund arbitration result out yet? I demand full compensation!",
                       zh_tw="上次那個退款仲裁結果出來沒？我要求全額賠償！",
                       hi="पिछले रिफंड मध्यस्थता का नतीजा आया क्या? मुझे पूरा मुआवज़ा चाहिए!"),
                 ["flag:blacklist", "profile:company"]),
            ],
        },
        {
            "name": "zhaomin", "external_id": "demo-zhaomin", "created_h": 6 * 24,
            "summary": None,
            "items": [
                I("profile", "company", T(zh="晨曦文化传媒工作室", en="Dawnlight Media Studio", zh_tw="晨曦文化傳媒工作室", hi="Dawnlight Media Studio"), .9, sr="conv-demo-008", rc=1, ch=6 * 24, uh=48),
                I("preference", "contact_channel", T(zh="偏好邮件沟通，附件用 PDF", en="Prefers email communication, attachments as PDF", zh_tw="偏好郵件溝通，附件用 PDF", hi="ईमेल संचार पसंद, अटैचमेंट PDF में"), .85, sr="conv-demo-008", rc=1, ch=6 * 24, uh=48),
                I("profile", "team_size", T(zh="团队约 15 人（待确认）", en="Team of about 15 people (unconfirmed)", zh_tw="團隊約 15 人（待確認）", hi="लगभग 15 लोगों की टीम (पुष्टि बाकी)"), .5, status="pending", sr="conv-demo-008", ch=5 * 24, uh=5 * 24),
            ],
            "audits": [
                ("memory.extract.applied", None, 130, {"conversation_id": "conv-demo-008", "input_messages": 2, "added": 2, "updated": 0, "pending": 1, "dropped": 0, "pii_masked": 0, "usage": {}}),
            ],
            "runs": [
                (16, T(zh="你好，我是晨曦文化的赵敏，想咨询一下企业版报价，麻烦发我邮箱。",
                       en="Hi, this is Zhao Min from Dawnlight Media. I'd like to ask about enterprise pricing — please email me.",
                       zh_tw="你好，我是晨曦文化的趙敏，想諮詢一下企業版報價，麻煩發我郵箱。",
                       hi="नमस्ते, मैं Dawnlight Media से Zhao Min बोल रही हूँ। मुझे एंटरप्राइज़ प्लान के मूल्य के बारे में जानना है — कृपया मुझे ईमेल करें।"),
                 ["profile:company", "preference:contact_channel"]),
                (3, T(zh="我们团队大概 15 个人，后续可能扩容。",
                      en="Our team is about 15 people, with possible expansion later.",
                      zh_tw="我們團隊大概 15 個人，後續可能擴容。",
                      hi="हमारी टीम लगभग 15 लोगों की है, आगे विस्तार की संभावना है।"),
                 ["profile:company", "preference:contact_channel"]),
            ],
        },
    ]


async def ensure_agent(p, tenant, actor):
    """Demo agent with the full memory stack on; reuses the sample demo model."""
    catalog = p.gateway.catalog

    async def ensure(kind, body):
        rows = await catalog.list(tenant, kind)
        existing = next((r for r in rows if r["name"] == body.name), None)
        return existing or await catalog.save(tenant, actor, kind, body)

    connection = await ensure("connections", Connection(
        name=T(zh="示例模拟连接", en="Sample Demo Connection", zh_tw="示例模擬連接", hi="सैंपल डेमो कनेक्शन"), protocol="demo"))
    model = await ensure(
        "models",
        Model(name=T(zh="示例模拟 Chat", en="Sample Demo Chat", zh_tw="示例模擬 Chat", hi="सैंपल डेमो चैट"), connection_id=connection["id"],
              model_name="demo", operations=["chat"], tool_calling=True),
    )
    profile = await ensure("profiles", Profile(
        name=T(zh="示例客服模型方案", en="Sample Support Model Profile", zh_tw="示例客服模型方案", hi="सैंपल सपोर्ट मॉडल प्रोफ़ाइल"),
        operation="chat", model_id=model["id"]))
    version = profile["published_version"]
    if not version:
        published = await catalog.publish(tenant, actor, profile["id"], profile["revision"])
        version = published["version"]
    agent_name = T(zh="记忆演示助手", en="Memory Demo Assistant", zh_tw="記憶演示助手", hi="मेमोरी डेमो असिस्टेंट")
    agents = await p.agents.list(tenant)
    agent = next((a for a in agents if a["name"] == agent_name), None)
    if not agent:
        agent = await p.agents.create(
            tenant,
            actor,
            AgentDraft(
                name=agent_name,
                description=T(zh="客户记忆可视化与注入可解释性演示（记忆读写全开）",
                              en="Demo for customer memory visualization and injection explainability (memory read/write fully on)",
                              zh_tw="客戶記憶可視化與注入可解釋性演示（記憶讀寫全開）",
                              hi="ग्राहक मेमोरी विज़ुअलाइज़ेशन और इंजेक्शन व्याख्यात्मकता का डेमो (मेमोरी रीड/राइट पूरी तरह चालू)"),
                config=AgentConfig(
                    model_profile_id=profile["id"],
                    model_profile_version=version,
                    system_prompt=T(zh="你是一名企业售后客服助手。语气自然简洁，先回应客户处境再给方案，一次推进一件事。",
                                    en="You are an enterprise after-sales support assistant. Keep a natural, concise tone; acknowledge the customer's situation before offering solutions; advance one thing at a time.",
                                    zh_tw="你是一名企業售後客服助手。語氣自然簡潔，先回應客戶處境再給方案，一次推進一件事。",
                                    hi="आप एक एंटरप्राइज़ बिक्री-पश्चात सपोर्ट असिस्टेंट हैं। स्वाभाविक और संक्षिप्त लहजा रखें; समाधान देने से पहले ग्राहक की स्थिति को समझें; एक बार में एक ही बात आगे बढ़ाएं।"),
                    tool_names=[],
                    knowledge_base_ids=[],
                ),
            ),
        )
        await p.agents.publish(tenant, actor, agent["id"], agent["draft_revision"])
    return agent


async def seed_customer(c, engine, model_backend, tenant, agent, audit_actor):
    """Insert one customer block; skip entirely once its identity exists."""
    ext = c["external_id"]
    async with engine.connect() as conn:
        bound = await one(
            conn,
            "SELECT id FROM memory_identities WHERE tenant_id=:t AND source='web' AND external_key=:k",
            t=tenant, k=ext,
        )
    if bound:
        return False
    cid = uid("customer", c["name"])
    conv_id = uid("conversation", ext)
    item_ids = {f'{i["t"]}:{i["k"]}': uid("item", f'{c["name"]}:{i["t"]}:{i["k"]}') for i in c["items"]}
    items_by_key = {f'{i["t"]}:{i["k"]}': i for i in c["items"]}
    last_run_h = min(h for h, _, _ in c["runs"])

    async with engine.begin() as conn:
        await conn.execute(
            text("INSERT INTO memory_customers(id,tenant_id,created_at,updated_at) VALUES(:id,:t,:ca,:ua) ON CONFLICT DO NOTHING"),
            {"id": cid, "t": tenant, "ca": ts(c["created_h"]), "ua": ts(last_run_h)},
        )
        await conn.execute(
            text("INSERT INTO memory_identities(id,tenant_id,customer_id,source,external_key,created_at) VALUES(:id,:t,:c,'web',:k,:ca) ON CONFLICT DO NOTHING"),
            {"id": uid("identity", ext), "t": tenant, "c": cid, "k": ext, "ca": ts(c["created_h"])},
        )
        for i in c["items"]:
            iid = item_ids[f'{i["t"]}:{i["k"]}']
            referenced = i["status"] == "active" and any(
                f'{i["t"]}:{i["k"]}' in inj for _, _, inj in c["runs"])
            await conn.execute(
                text("""INSERT INTO memory_items(id,tenant_id,customer_id,type,key,content,confidence,status,
                        source_type,source_ref,prev_content,revision,last_referenced_at,reference_count,
                        created_at,updated_at,protected)
                        VALUES(:id,:t,:c,:ty,:k,:co,:cf,:st,:sty,:sr,:prev,:rev,:lra,:rc,:ca,:ua,:prot)
                        ON CONFLICT DO NOTHING"""),
                {"id": iid, "t": tenant, "c": cid, "ty": i["t"], "k": i["k"], "co": i["content"], "cf": i["conf"],
                     "st": i["status"], "sty": i["st"], "sr": i["sr"], "prev": i["prev"], "rev": i["rev"],
                     "lra": ts(last_run_h) if referenced else ts(i["ch"]), "rc": i["rc"],
                     "ca": ts(i["ch"]), "ua": ts(i["uh"]), "prot": i["prot"]},
            )
        # Conversation with full history and one completed run per message pair.
        history = []
        for _, msg, _ in c["runs"]:
            history.append({"role": "user", "content": msg})
            history.append({"role": "assistant", "content": default_reply()})
        await conn.execute(
            text("""INSERT INTO runtime_conversations(id,tenant_id,external_id,history,created_at,mode,agent_id,
                    updated_at,revision,source,summary,customer_id)
                    VALUES(:id,:t,:ext,CAST(:hist AS jsonb),:ca,'bot',:aid,:ua,1,'business',:sum,:cid)
                    ON CONFLICT (tenant_id,external_id) DO NOTHING"""),
            {"id": conv_id, "t": tenant, "ext": ext, "hist": json.dumps(history, ensure_ascii=False),
                 "ca": ts(c["runs"][0][0] + 0.1), "aid": agent["id"], "ua": ts(last_run_h),
                 "sum": c["summary"], "cid": cid},
        )
        for n, (h, msg, injected) in enumerate(c["runs"]):
            run_id = uid("run", f"{ext}:{n}")
            created, started, finished = ts(h), ts(h) + timedelta(milliseconds=30), ts(h) + timedelta(milliseconds=235)
            run_config = {"agent": {"id": str(agent["id"]), "tools": [],
                                    "config": agent["draft"], "version": 1, "is_example": False},
                          "model_name": "", "fingerprint": "0" * 64, "model_backend": model_backend}
            await conn.execute(
                text("""INSERT INTO runtime_runs(id,conversation_id,tenant_id,input,config,status,output,
                        event_seq,queue_expires_at,created_at,started_at,finished_at)
                        VALUES(:id,:cid,:t,:inp,CAST(:cfg AS jsonb),'completed',:out,7,:qexp,:ca,:sa,:fa)
                        ON CONFLICT DO NOTHING"""),
                {"id": run_id, "cid": conv_id, "t": tenant, "inp": msg,
                     "cfg": json.dumps(run_config, ensure_ascii=False, default=str), "out": default_reply(),
                     "qexp": created + timedelta(minutes=2), "ca": created, "sa": started, "fa": finished},
            )
            for role, when in (("user", created), ("assistant", finished)):
                await conn.execute(
                    text("INSERT INTO conversation_messages(id,conversation_id,role,content,run_id,created_at) VALUES(:id,:cid,:r,:co,:rid,:ca)"),
                    {"id": uid("message", f"{run_id}:{role}"), "cid": conv_id, "r": role,
                         "co": msg if role == "user" else default_reply(), "rid": run_id, "ca": when},
                )
            injected_ids = [item_ids[k] for k in injected if k in item_ids]
            sections = [items_by_key[k]["t"] for k in injected if k in items_by_key]
            counts = {"flags": sections.count("flag"),
                      "items": sections.count("profile") + sections.count("preference"),
                      "events": sections.count("event")}
            events = [
                (1, "run.queued", {}),
                (2, "run.started", {}),
                (3, "model.started", {"round": 1}),
                (4, "model.completed", {"round": 1, "usage": {}, "tool_calls": 0, "duration_ms": 141,
                                        "first_token_ms": 38, "gateway_call_id": uid("gw", run_id)}),
                (5, "memory_extract.queued", {"customer_id": cid}),
                (6, "memory.injected", {**counts, "item_ids": injected_ids}),
                (7, "run.completed", {"output": default_reply(), "error_code": None}),
            ]
            for seq, etype, data in events:
                await conn.execute(
                    text("INSERT INTO runtime_events(run_id,seq,type,data,created_at) VALUES(:rid,:seq,:ty,CAST(:d AS jsonb),:ca) ON CONFLICT DO NOTHING"),
                    {"rid": run_id, "seq": seq, "ty": etype, "d": json.dumps(data, ensure_ascii=False), "ca": finished},
                )
            for k in injected:
                if k not in item_ids:
                    continue
                section = items_by_key[k]["t"]
                score = items_by_key[k]["conf"] if section in ("profile", "preference") else None
                await conn.execute(
                    text("INSERT INTO memory_injection_log(tenant_id,run_id,customer_id,item_id,section,score,injected_at) VALUES(:t,:rid,:cid,:iid,:sec,:score,:at)"),
                    {"t": tenant, "rid": run_id, "cid": cid, "iid": item_ids[k], "sec": section, "score": score, "at": finished},
                )
        for action, item_key, hours_ago, details in c["audits"]:
            if details is None:
                i = items_by_key[item_key]
                details = {"key": i["k"], "type": i["t"], "customer_id": cid}
                if action == "memory.flag.upserted":
                    details.update(pii_masked=0, source_ref=audit_actor, source_type="human")
                elif action == "memory.item.edited":
                    details["pii_masked"] = 0
                else:
                    transitions = {"memory.item.deleted": ("active", "deleted"),
                                   "memory.item.restored": ("deleted", "active"),
                                   "memory.item.confirmed": ("pending", "active"),
                                   "memory.item.rejected": ("pending", "deleted")}
                    old, new = transitions[action]
                    details.update(old_status=old, new_status=new)
            resource = item_ids.get(item_key, cid) if item_key else cid
            await conn.execute(
                text("INSERT INTO audit_records(tenant_id,actor,action,resource,details,created_at) VALUES(:t,:actor,:action,:res,CAST(:d AS jsonb),:ca)"),
                {"t": tenant, "actor": audit_actor, "action": action, "res": str(resource),
                     "d": json.dumps(details, ensure_ascii=False), "ca": ts(hours_ago)},
            )
    return True


async def main():
    settings = Settings()
    tenant, actor = settings.tenant_id, "seed"
    locale = set_locale(settings)
    async with runtime_services(settings) as runtime:
        p = runtime.platform
        async with p.engine.connect() as lock:
            await lock.execute(text("SELECT pg_advisory_lock(71432025)"))
            try:
                agent = await ensure_agent(p, tenant, actor)
                async with p.engine.connect() as conn:
                    admin = await one(conn, "SELECT user_id FROM local_admin_credentials WHERE tenant_id=:t LIMIT 1", t=tenant)
                audit_actor = f"enterprise:{admin['user_id']}" if admin else actor
                seeded = 0
                for cust in _customers():
                    if await seed_customer(cust, p.engine, settings.model_backend, tenant, agent, audit_actor):
                        seeded += 1
                print(T(zh=f"记忆演示数据就绪（新入库 {seeded}/4 个客户，租户：{tenant}，语言：{locale}）。",
                        en=f"Memory demo data ready ({seeded}/4 customers newly seeded, tenant: {tenant}, locale: {locale}).",
                        zh_tw=f"記憶演示數據就緒（新入庫 {seeded}/4 個客戶，租戶：{tenant}，語言：{locale}）。",
                        hi=f"मेमोरी डेमो डेटा तैयार ({seeded}/4 ग्राहक नए सीड किए गए, टेनेंट: {tenant}, लोकेल: {locale})।"))
            finally:
                await lock.execute(text("SELECT pg_advisory_unlock(71432025)"))


if __name__ == "__main__":
    asyncio.run(main())
