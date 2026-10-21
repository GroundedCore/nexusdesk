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

Display strings are localized via ``seed_memory_i18n`` (AGENT_SEED_LOCALE);
identity keys stay constant so the locale only affects first-time seeding.
"""

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
    uid,
)
from agent_platform.apps.seed_memory_i18n import T
from agent_platform.modules.agent_config.service import AgentConfig
from agent_platform.platform.persistence.store import one

NS = ns("phase5")

# ---------------------------------------------------------------------------
# Demo agents: one per memory-config quadrant.
# ---------------------------------------------------------------------------


def _agents():
    """Demo agent specs; built per call so T() sees the active locale."""
    return [
        {
            "key": "full",
            "name": T(zh="记忆全开助手", en="Full Memory Assistant",
                      zh_tw="記憶全開助手", hi="फुल मेमोरी असिस्टेंट"),
            "description": T(
                zh="记忆配置全开（默认值）：读取+写入+全类型+检索。预期行为：run 事件包含 memory.injected（注入全部可用记忆）与 memory_extract.queued（自动抽取），与阶段 1-4 行为逐位一致（存量等价基线）。",
                en="All memory config on (defaults): read + write + all types + search. Expected behavior: the run events include memory.injected (all available memory injected) and memory_extract.queued (auto extraction), bit-for-bit identical to phases 1-4 (legacy-equivalence baseline).",
                zh_tw="記憶配置全開（預設值）：讀取+寫入+全類型+檢索。預期行為：run 事件包含 memory.injected（注入全部可用記憶）與 memory_extract.queued（自動抽取），與階段 1-4 行為逐位一致（存量等價基線）。",
                hi="मेमोरी कॉन्फ़िग पूरी तरह चालू (डिफ़ॉल्ट): रीड + राइट + सभी प्रकार + सर्च। अपेक्षित व्यवहार: रन इवेंट्स में memory.injected (सभी उपलब्ध मेमोरी इंजेक्ट) और memory_extract.queued (स्वचालित एक्सट्रैक्शन) शामिल होते हैं, जो चरण 1-4 के व्यवहार के बिल्कुल समान है (लेगसी-समकक्ष बेसलाइन)।"),
            "config": {},
        },
        {
            "key": "readonly",
            "name": T(zh="只读记忆助手", en="Read-only Memory Assistant",
                      zh_tw="唯讀記憶助手", hi="रीड-ओनली मेमोरी असिस्टेंट"),
            "description": T(
                zh="memory_write=false（只读不写）：既有客户记忆照常注入，会话结束不派发抽取任务、不产生新记忆。预期行为：run 有 memory.injected，无 memory_extract.queued，事件共 6 条。",
                en="memory_write=false (read-only): existing customer memory is injected as usual, but no extraction task is dispatched and no new memory is produced after the conversation. Expected behavior: the run has memory.injected but no memory_extract.queued — 6 events in total.",
                zh_tw="memory_write=false（唯讀不寫）：既有客戶記憶照常注入，會話結束不派發抽取任務、不產生新記憶。預期行為：run 有 memory.injected，無 memory_extract.queued，事件共 6 條。",
                hi="memory_write=false (केवल-पठन): मौजूदा ग्राहक मेमोरी सामान्य रूप से इंजेक्ट होती है, लेकिन वार्तालाप समाप्त होने पर कोई एक्सट्रैक्शन टास्क डिस्पैच नहीं होता और कोई नई मेमोरी नहीं बनती। अपेक्षित व्यवहार: रन में memory.injected है, पर memory_extract.queued नहीं — कुल 6 इवेंट।"),
            "config": {"memory_write": False},
        },
        {
            "key": "off",
            "name": T(zh="记忆关闭助手", en="Memory Off Assistant",
                      zh_tw="記憶關閉助手", hi="मेमोरी ऑफ असिस्टेंट"),
            "description": T(
                zh="memory_enabled=false（总开关关闭）：不注入、不检索、不抽取，子项在配置页置灰。预期行为：即使绑定客户有丰富记忆，run 也不出现任何 memory.* 事件、无注入日志，事件仅 5 条。",
                en="memory_enabled=false (master switch off): no injection, no search, no extraction; sub-options are greyed out on the config page. Expected behavior: even with a memory-rich bound customer, the run carries no memory.* events and no injection log rows — only 5 events.",
                zh_tw="memory_enabled=false（總開關關閉）：不注入、不檢索、不抽取，子項在配置頁置灰。預期行為：即使綁定客戶有豐富記憶，run 也不出現任何 memory.* 事件、無注入日誌，事件僅 5 條。",
                hi="memory_enabled=false (मास्टर स्विच बंद): न इंजेक्शन, न सर्च, न एक्सट्रैक्शन; कॉन्फ़िग पेज पर सब-ऑप्शन ग्रे हो जाते हैं। अपेक्षित व्यवहार: भले ही बंधे ग्राहक के पास समृद्ध मेमोरी हो, रन में कोई memory.* इवेंट नहीं होता और कोई इंजेक्शन लॉग नहीं बनता — केवल 5 इवेंट।"),
            "config": {"memory_enabled": False},
        },
        {
            "key": "review",
            "name": T(zh="人工审核记忆助手", en="Human Review Memory Assistant",
                      zh_tw="人工審核記憶助手", hi="ह्यूमन रिव्यू मेमोरी असिस्टेंट"),
            "description": T(
                zh="memory_write_mode=review（人工审核写入）：注入正常、抽取任务照常派发，但抽取结果全部进入待确认队列。预期行为：客户记忆出现 status=pending 条目，确认前不参与注入；审计 pending 计数大于 0。",
                en="memory_write_mode=review (human-reviewed writes): injection works normally and extraction tasks are still dispatched, but all extraction results enter the pending-confirmation queue. Expected behavior: the customer's memory shows status=pending items that are not injected until confirmed; audit pending count is greater than 0.",
                zh_tw="memory_write_mode=review（人工審核寫入）：注入正常、抽取任務照常派發，但抽取結果全部進入待確認佇列。預期行為：客戶記憶出現 status=pending 條目，確認前不參與注入；審計 pending 計數大於 0。",
                hi="memory_write_mode=review (मानव-समीक्षित राइट): इंजेक्शन सामान्य रूप से काम करता है और एक्सट्रैक्शन टास्क भी डिस्पैच होते हैं, लेकिन सभी एक्सट्रैक्शन परिणाम पुष्टि-लंबित कतार में चले जाते हैं। अपेक्षित व्यवहार: ग्राहक की मेमोरी में status=pending आइटम दिखते हैं जो पुष्टि तक इंजेक्ट नहीं होते; ऑडिट में pending संख्या 0 से अधिक होती है।"),
            "config": {"memory_write_mode": "review"},
        },
        {
            "key": "lean",
            "name": T(zh="精简注入助手", en="Compact Injection Assistant",
                      zh_tw="精簡注入助手", hi="कॉम्पैक्ट इंजेक्शन असिस्टेंट"),
            "description": T(
                zh="memory_inject_max_items=2（精简注入）：画像/偏好每次最多注入 2 条，事件上限同步收紧为 min(2, 平台事件上限)。预期行为：同一客户下 memory.injected 计数收缩为 items=2、events≤2，与记忆全开助手形成对比。",
                en="memory_inject_max_items=2 (compact injection): at most 2 profile/preference items injected per run, and the event cap tightens to min(2, platform event cap). Expected behavior: for the same customer the memory.injected counts shrink to items=2 and events<=2, in contrast to the Full Memory Assistant.",
                zh_tw="memory_inject_max_items=2（精簡注入）：畫像/偏好每次最多注入 2 條，事件上限同步收緊為 min(2, 平台事件上限)。預期行為：同一客戶下 memory.injected 計數收縮為 items=2、events≤2，與記憶全開助手形成對比。",
                hi="memory_inject_max_items=2 (कॉम्पैक्ट इंजेक्शन): प्रति रन अधिकतम 2 प्रोफ़ाइल/प्रेफरेंस आइटम इंजेक्ट होते हैं, और इवेंट सीमा भी min(2, प्लेटफ़ॉर्म इवेंट सीमा) तक सीमित हो जाती है। अपेक्षित व्यवहार: उसी ग्राहक के लिए memory.injected की गिनती items=2 और events<=2 तक सिकुड़ जाती है, जो फुल मेमोरी असिस्टेंट के विपरीत है।"),
            "config": {"memory_inject_max_items": 2},
        },
        {
            "key": "profileonly",
            "name": T(zh="画像限定助手", en="Profile-only Assistant",
                      zh_tw="畫像限定助手", hi="प्रोफ़ाइल-ओनली असिस्टेंट"),
            "description": T(
                zh="memory_types=['profile']（仅客户画像）：仅 profile 参与注入与写入，preference/event 不注入、不写入。预期行为：memory.injected 仅含画像条目（events=0），注入日志只有 profile 段，库中偏好/事件条目保持不变。",
                en="memory_types=['profile'] (customer profile only): only profile items participate in injection and writes; preference/event items are neither injected nor written. Expected behavior: memory.injected contains only profile items (events=0), the injection log has only the profile section, and preference/event items in the store remain unchanged.",
                zh_tw="memory_types=['profile']（僅客戶畫像）：僅 profile 參與注入與寫入，preference/event 不注入、不寫入。預期行為：memory.injected 僅含畫像條目（events=0），注入日誌只有 profile 段，庫中偏好/事件條目保持不變。",
                hi="memory_types=['profile'] (केवल ग्राहक प्रोफ़ाइल): केवल profile आइटम इंजेक्शन और राइट में भाग लेते हैं; preference/event आइटम न तो इंजेक्ट होते हैं, न लिखे जाते हैं। अपेक्षित व्यवहार: memory.injected में केवल प्रोफ़ाइल आइटम होते हैं (events=0), इंजेक्शन लॉग में केवल profile सेक्शन होता है, और स्टोर में preference/event आइटम अपरिवर्तित रहते हैं।"),
            "config": {"memory_types": ["profile"]},
        },
    ]


def _system_prompt() -> str:
    return T(zh="你是一名企业客服助手。语气自然简洁，先回应客户处境再给方案，一次推进一件事。",
             en="You are an enterprise customer-support assistant. Keep a natural, concise tone; acknowledge the customer's situation before offering solutions; advance one thing at a time.",
             zh_tw="你是一名企業客服助手。語氣自然簡潔，先回應客戶處境再給方案，一次推進一件事。",
             hi="आप एक एंटरप्राइज़ कस्टमर-सपोर्ट असिस्टेंट हैं। स्वाभाविक और संक्षिप्त लहजा रखें; समाधान देने से पहले ग्राहक की स्थिति को स्वीकार करें; एक बार में एक ही कार्य आगे बढ़ाएं।")


# ---------------------------------------------------------------------------
# Demo customers and their memory items.
# ---------------------------------------------------------------------------

SUN_EXT = "demo-p5-sunmingxuan"
ZHOU_EXT = "demo-p5-zhouyating"


def _sun_items():
    """Sun Mingxuan's memory items; built per call so T() sees the locale."""
    return [
        item("profile", "company", T(zh="恒信物流集团有限公司", en="Hengxin Logistics Group Co., Ltd.",
                                      zh_tw="恆信物流集團有限公司", hi="Hengxin Logistics Group Co., Ltd."), .97, st="manual", rc=10, ch=20 * 24, uh=48),
        item("profile", "industry", T(zh="大宗物流与仓储（华东区域，自营 3 个仓库）", en="Bulk logistics and warehousing (East China region, 3 self-operated warehouses)",
                                       zh_tw="大宗物流與倉儲（華東區域，自營 3 個倉庫）", hi="बल्क लॉजिस्टिक्स और वेयरहाउसिंग (पूर्वी चीन क्षेत्र, 3 स्व-संचालित गोदाम)"), .94, sr=f"{SUN_EXT}:full", rc=6, ch=19 * 24, uh=72),
        item("profile", "role", T(zh="供应链总监，负责仓配采购与服务商评估", en="Supply Chain Director, responsible for warehouse/distribution procurement and vendor evaluation",
                                   zh_tw="供應鏈總監，負責倉配採購與服務商評估", hi="सप्लाई चेन डायरेक्टर, गोदाम/वितरण खरीद और विक्रेता मूल्यांकन के लिए जिम्मेदार"), .93, sr=f"{SUN_EXT}:full", rc=4, ch=18 * 24, uh=96),
        item("profile", "tier", T(zh="银牌客户（季度框架合同）", en="Silver-tier customer (quarterly frame contract)",
                                   zh_tw="銀牌客戶（季度框架合同）", hi="सिल्वर-टियर ग्राहक (त्रैमासिक फ्रेम कॉन्ट्रैक्ट)"), .96, st="manual", rc=8, ch=15 * 24, uh=24),
        item("preference", "contact_channel", T(zh="工作日优先企业微信沟通，节假日紧急事项电话", en="Prefers WeCom on workdays; phone for urgent matters on holidays",
                                                 zh_tw="工作日優先企業微信溝通，節假日緊急事項電話", hi="कार्यदिवसों पर WeCom को प्राथमिकता; अवकाश के दिनों में ज़रूरी मामलों के लिए फ़ोन"), .9, sr=f"{SUN_EXT}:full", rc=5, ch=14 * 24, uh=24),
        item("preference", "report", T(zh="每月 5 日前提供上月运输对账月报", en="Provide last month's shipping reconciliation report before the 5th of each month",
                                       zh_tw="每月 5 日前提供上月運輸對賬月報", hi="हर महीने की 5 तारीख से पहले पिछले महीने की शिपिंग रीकंसिलिएशन रिपोर्ट दें"), .88, st="manual", rc=3, ch=12 * 24, uh=48),
        item("event", "open_ticket:TK-20261020", T(zh="华东仓配一体化方案报价已发送，等待客户法务审核（预计 10-25 前反馈）", en="Quote for the East China integrated warehouse/distribution plan sent, awaiting the customer's legal review (feedback expected before 10-25)",
                                                    zh_tw="華東倉配一體化方案報價已發送，等待客戶法務審核（預計 10-25 前反饋）", hi="पूर्वी चीन एकीकृत गोदाम/वितरण योजना का कोटेशन भेजा गया, ग्राहक की कानूनी समीक्षा की प्रतीक्षा में (10-25 से पहले प्रतिक्रिया अपेक्षित)"), .92, st="manual", sr=f"{SUN_EXT}:full", rc=2, ch=30, uh=30),
        item("event", "commitment:2026-10-21", T(zh="承诺 10-28 前完成上海仓扩容方案测算并同步客户", en="Committed to finish the Shanghai warehouse expansion estimate before 10-28 and sync with the customer",
                                                  zh_tw="承諾 10-28 前完成上海倉擴容方案測算並同步客戶", hi="10-28 से पहले शंघाई गोदाम विस्तार का अनुमान पूरा कर ग्राहक को सूचित करने का वादा"), .9, sr=f"{SUN_EXT}:full", rc=1, ch=28, uh=28),
        item("flag", "vip", T(zh="重点客户：合同 2026-12-31 到期，11 月启动续约沟通，报价方案需提前内部评审", en="Key account: contract expires 2026-12-31; renewal talks start in November; quotes require prior internal review",
                               zh_tw="重點客戶：合同 2026-12-31 到期，11 月啟動續約溝通，報價方案需提前內部評審", hi="मुख्य खाता: कॉन्ट्रैक्ट 2026-12-31 को समाप्त होता है; नवंबर में नवीनीकरण वार्ता शुरू होगी; कोटेशन के लिए पहले आंतरिक समीक्षा आवश्यक है"), 1.0, st="human", prot=True, ch=96, uh=96),
    ]


def _zhou_items():
    """Zhou Yating's memory items; built per call so T() sees the locale."""
    return [
        item("profile", "company", T(zh="悦读教育科技有限公司", en="Yuedu Education Technology Co., Ltd.",
                                      zh_tw="悅讀教育科技有限公司", hi="Yuedu Education Technology Co., Ltd."), .95, st="manual", rc=4, ch=8 * 24, uh=48),
        item("profile", "team_size", T(zh="团队约 40 人，以教研与内容团队为主", en="Team of about 40 people, mostly curriculum research and content staff",
                                        zh_tw="團隊約 40 人，以教研與內容團隊為主", hi="लगभग 40 लोगों की टीम, मुख्य रूप से पाठ्यक्रम अनुसंधान और कंटेंट स्टाफ़"), .9, sr=f"{ZHOU_EXT}:review", rc=2, ch=8 * 24, uh=72),
        item("preference", "contact_channel", T(zh="偏好邮件沟通，重要事项抄送其助理", en="Prefers email; CC her assistant on important matters",
                                                 zh_tw="偏好郵件溝通，重要事項抄送其助理", hi="ईमेल को प्राथमिकता; महत्वपूर्ण मामलों पर उनके असिस्टेंट को CC करें"), .88, sr=f"{ZHOU_EXT}:review", rc=3, ch=7 * 24, uh=24),
        item("preference", "budget", T(zh="年度 SaaS 采购预算约 8 万元", en="Annual SaaS procurement budget of about CNY 80,000",
                                      zh_tw="年度 SaaS 採購預算約 8 萬元", hi="वार्षिक SaaS खरीद बजट लगभग CNY 80,000"), .85, sr=f"{ZHOU_EXT}:review", rc=1, ch=6 * 24, uh=72),
        item("event", "evt-202610-trial", T(zh="2026-10 完成企业版 14 天试用，教研团队反馈良好，进入采购评估", en="2026-10 completed the 14-day enterprise trial; positive feedback from the curriculum team; entered procurement evaluation",
                                             zh_tw="2026-10 完成企業版 14 天試用，教研團隊反饋良好，進入採購評估", hi="2026-10 में 14-दिवसीय एंटरप्राइज़ ट्रायल पूरा हुआ; पाठ्यक्रम टीम से सकारात्मक प्रतिक्रिया; खरीद मूल्यांकन में प्रवेश"), .9, sr=f"{ZHOU_EXT}:review", rc=1, ch=5 * 24, uh=5 * 24),
        # review 模式抽取结果：全部进入待确认队列，确认前不参与注入（A4）。
        item("preference", "contact_time", T(zh="偏好下午 2 点后电话联系（待确认）", en="Prefers phone contact after 2 PM (unconfirmed)",
                                             zh_tw="偏好下午 2 點後電話聯繫（待確認）", hi="दोपहर 2 बजे के बाद फ़ोन संपर्क पसंद (पुष्टि लंबित)"), .6, status="pending", sr=f"{ZHOU_EXT}:review", ch=25, uh=25),
    ]


def run_events(run_key, cid, injected, write_on, reply=None):
    """Genuine event sequence honouring the Phase 5 gates.

    ``repository.finish`` emits ``memory_extract.queued`` only when the write
    gate dispatched an extraction task, and ``memory.injected`` only when
    something was actually injected — a read-off run therefore ends at
    ``run.completed`` right after ``model.completed``.
    """
    reply = reply if reply is not None else default_reply()
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
    for spec in _agents():
        if spec["name"] not in existing:
            inserted = True
        config = AgentConfig(system_prompt=_system_prompt(), tool_names=[], knowledge_base_ids=[], **spec["config"])
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
            sun_items = _sun_items()
            cid = uid(NS, "customer", "sunmingxuan")
            iids = {f'{i["t"]}:{i["k"]}': uid(NS, "item", f"sun:{i['t']}:{i['k']}") for i in sun_items}
            specs = {f'{i["t"]}:{i["k"]}': i for i in sun_items}

            def sinj(keys):
                return [(iids[k], specs[k]["t"], specs[k]["conf"] if specs[k]["t"] in ("profile", "preference") else None)
                        for k in keys]

            await insert_customer(conn, tenant, cid, 20 * 24, 5)
            await insert_identity(conn, tenant, uid(NS, "identity", SUN_EXT), cid, SUN_EXT, 20 * 24)
            for i in sun_items:
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

            msg_full_0 = T(zh="你好，我是恒信物流的孙明轩，华东仓配一体化的报价方案进展怎么样了？",
                           en="Hi, this is Sun Mingxuan from Hengxin Logistics. How is the quote for the East China integrated warehouse/distribution plan coming along?",
                           zh_tw="你好，我是恆信物流的孫明軒，華東倉配一體化的報價方案進展怎麼樣了？",
                           hi="नमस्ते, मैं Hengxin Logistics से Sun Mingxuan बोल रहा हूँ। पूर्वी चीन एकीकृत गोदाम/वितरण योजना के कोटेशन की क्या प्रगति है?")
            msg_full_1 = T(zh="上海仓扩容的测算别忘了，月底前我们要内部评审。",
                           en="Don't forget the Shanghai warehouse expansion estimate — we have an internal review before month-end.",
                           zh_tw="上海倉擴容的測算別忘了，月底前我們要內部評審。",
                           hi="शंघाई गोदाम विस्तार का अनुमान मत भूलिए — महीने के अंत से पहले हमारी आंतरिक समीक्षा है।")
            conv_full = uid(NS, "conversation", f"{SUN_EXT}:full")
            await insert_conversation(
                conn, tenant, conv_full, f"{SUN_EXT}:full",
                pairs([msg_full_0, msg_full_1]),
                30.1, 5, agents["full"]["id"],
                summary=T(zh="客户孙明轩（恒信物流，银牌客户）：跟进华东仓配一体化报价与上海仓扩容测算；合同 2026-12-31 到期，11 月启动续约。",
                          en="Customer Sun Mingxuan (Hengxin Logistics, silver tier): following up on the East China integrated warehouse/distribution quote and the Shanghai warehouse expansion estimate; contract expires 2026-12-31, renewal talks start in November.",
                          zh_tw="客戶孫明軒（恆信物流，銀牌客戶）：跟進華東倉配一體化報價與上海倉擴容測算；合同 2026-12-31 到期，11 月啟動續約。",
                          hi="ग्राहक Sun Mingxuan (Hengxin Logistics, सिल्वर टियर): पूर्वी चीन एकीकृत गोदाम/वितरण कोटेशन और शंघाई गोदाम विस्तार अनुमान का फ़ॉलो-अप; कॉन्ट्रैक्ट 2026-12-31 को समाप्त, नवंबर में नवीनीकरण वार्ता शुरू।"),
                cid=cid)
            await insert_run(conn, tenant, NS, "sun:full:0", conv_full, agents["full"],
                             settings.model_backend,
                             msg_full_0,
                             30, cid=cid, injected=full_inj)
            await insert_run(conn, tenant, NS, "sun:full:1", conv_full, agents["full"],
                             settings.model_backend,
                             msg_full_1,
                             5, cid=cid, injected=full_inj2)

            msg_ro_0 = T(zh="运输对账月报还是按老时间发就行。",
                         en="Just send the shipping reconciliation monthly report at the usual time.",
                         zh_tw="運輸對賬月報還是按老時間發就行。",
                         hi="शिपिंग रीकंसिलिएशन मासिक रिपोर्ट हमेशा की तरह उसी समय भेज दीजिए।")
            conv_ro = uid(NS, "conversation", f"{SUN_EXT}:readonly")
            await insert_conversation(
                conn, tenant, conv_ro, f"{SUN_EXT}:readonly",
                pairs([msg_ro_0]), 12.1, 12, agents["readonly"]["id"], cid=cid)
            await insert_run(conn, tenant, NS, "sun:readonly:0", conv_ro, agents["readonly"],
                             settings.model_backend, msg_ro_0,
                             12, cid=cid, injected=readonly_inj,
                             events=run_events("sun:readonly:0", cid, readonly_inj, write_on=False))

            msg_off_0 = T(zh="在吗？想问下报价的事。",
                          en="Are you there? I'd like to ask about the quote.",
                          zh_tw="在嗎？想問下報價的事。",
                          hi="क्या आप यहाँ हैं? मुझे कोटेशन के बारे में पूछना था।")
            conv_off = uid(NS, "conversation", f"{SUN_EXT}:off")
            await insert_conversation(
                conn, tenant, conv_off, f"{SUN_EXT}:off",
                pairs([msg_off_0]), 8.1, 8, agents["off"]["id"], cid=cid)
            await insert_run(conn, tenant, NS, "sun:off:0", conv_off, agents["off"],
                             settings.model_backend, msg_off_0,
                             8, cid=cid,
                             events=run_events("sun:off:0", cid, [], write_on=False))

            msg_lean_0 = T(zh="报价方案和扩容测算一起催一下，谢谢。",
                           en="Please chase both the quote and the expansion estimate, thanks.",
                           zh_tw="報價方案和擴容測算一起催一下，謝謝。",
                           hi="कृपया कोटेशन और विस्तार अनुमान दोनों को आगे बढ़वाइए, धन्यवाद।")
            conv_lean = uid(NS, "conversation", f"{SUN_EXT}:lean")
            await insert_conversation(
                conn, tenant, conv_lean, f"{SUN_EXT}:lean",
                pairs([msg_lean_0]), 6.1, 6, agents["lean"]["id"], cid=cid)
            await insert_run(conn, tenant, NS, "sun:lean:0", conv_lean, agents["lean"],
                             settings.model_backend, msg_lean_0,
                             6, cid=cid, injected=lean_inj)

            await insert_audit(
                conn, tenant, "memory", "memory.extract.applied", cid,
                {"conversation_id": f"{SUN_EXT}:full", "input_messages": 4, "added": 2,
                 "updated": 1, "pending": 0, "dropped": 0, "pii_masked": 0, "usage": {}},
                29)

        # --- 周雅婷：供 画像限定/人工审核 两个 Agent 演示 ----------------------
        if not await identity_bound(conn, tenant, "web", ZHOU_EXT):
            inserted = True
            zhou_items = _zhou_items()
            cid = uid(NS, "customer", "zhouyating")
            iids = {f'{i["t"]}:{i["k"]}': uid(NS, "item", f"zhou:{i['t']}:{i['k']}") for i in zhou_items}
            specs = {f'{i["t"]}:{i["k"]}': i for i in zhou_items}

            def zinj(keys):
                return [(iids[k], specs[k]["t"], specs[k]["conf"] if specs[k]["t"] in ("profile", "preference") else None)
                        for k in keys]

            await insert_customer(conn, tenant, cid, 8 * 24, 3)
            await insert_identity(conn, tenant, uid(NS, "identity", ZHOU_EXT), cid, ZHOU_EXT, 8 * 24)
            for i in zhou_items:
                await insert_item(conn, tenant, iids[f'{i["t"]}:{i["k"]}'], cid, i,
                                  last_referenced_h=3 if i["status"] == "active" else None)

            msg_po_0 = T(zh="你好，我是悦读教育的周雅婷，想确认下企业版报价。",
                         en="Hi, this is Zhou Yating from Yuedu Education. I'd like to confirm the enterprise pricing.",
                         zh_tw="你好，我是悅讀教育的周雅婷，想確認下企業版報價。",
                         hi="नमस्ते, मैं Yuedu Education से Zhou Yating बोल रही हूँ। मुझे एंटरप्राइज़ प्राइसिंग कन्फ़र्म करनी है।")
            conv_po = uid(NS, "conversation", f"{ZHOU_EXT}:profileonly")
            await insert_conversation(
                conn, tenant, conv_po, f"{ZHOU_EXT}:profileonly",
                pairs([msg_po_0]), 10.1, 10, agents["profileonly"]["id"], cid=cid)
            po_inj = zinj(["profile:company", "profile:team_size"])
            await insert_run(conn, tenant, NS, "zhou:profileonly:0", conv_po, agents["profileonly"],
                             settings.model_backend, msg_po_0,
                             10, cid=cid, injected=po_inj)

            msg_rv_0 = T(zh="我们试用期结束了，整体很满意，想聊聊正式采购。",
                         en="Our trial has ended and we're very satisfied overall — we'd like to discuss a formal purchase.",
                         zh_tw="我們試用期結束了，整體很滿意，想聊聊正式採購。",
                         hi="हमारा ट्रायल समाप्त हो गया है और हम कुल मिलाकर बहुत संतुष्ट हैं — हम औपचारिक खरीद पर चर्चा करना चाहते हैं।")
            msg_rv_1 = T(zh="后续联系我的话，尽量下午两点以后打电话。",
                         en="For future contact, please call after 2 PM if possible.",
                         zh_tw="後續聯繫我的話，盡量下午兩點以後打電話。",
                         hi="आगे संपर्क करने के लिए, कृपया संभव हो तो दोपहर 2 बजे के बाद कॉल करें।")
            conv_rv = uid(NS, "conversation", f"{ZHOU_EXT}:review")
            await insert_conversation(
                conn, tenant, conv_rv, f"{ZHOU_EXT}:review",
                pairs([msg_rv_0, msg_rv_1]),
                26.1, 3, agents["review"]["id"], cid=cid)
            rv_inj = zinj(["profile:company", "profile:team_size", "preference:contact_channel",
                           "preference:budget", "event:evt-202610-trial"])
            await insert_run(conn, tenant, NS, "zhou:review:0", conv_rv, agents["review"],
                             settings.model_backend, msg_rv_0,
                             26, cid=cid, injected=rv_inj)
            rv_inj2 = zinj(["profile:company", "preference:contact_channel"])
            await insert_run(conn, tenant, NS, "zhou:review:1", conv_rv, agents["review"],
                             settings.model_backend, msg_rv_1,
                             3, cid=cid, injected=rv_inj2)

            # review 模式：抽取结果全部进待确认（added=0, pending=1）。
            await insert_audit(
                conn, tenant, "memory", "memory.extract.applied", cid,
                {"conversation_id": f"{ZHOU_EXT}:review", "input_messages": 3, "added": 0,
                 "updated": 0, "pending": 1, "dropped": 0, "pii_masked": 0, "usage": {}},
                25)

    return inserted


if __name__ == "__main__":
    run_standalone(seed, lambda: T(zh="阶段5Agent记忆配置演示数据", en="Phase 5 agent memory-config demo data",
                                   zh_tw="階段5Agent記憶配置演示數據", hi="चरण 5 एजेंट मेमोरी-कॉन्फ़िग डेमो डेटा"))
