"""Seed the phase-1 (conversation summary) acceptance dataset; safe to re-run.

Demonstrates the Phase 1 acceptance scenarios (A1/A4/A5 plus the rolling
state) with genuine-shaped rows:

- 陈静 (demo-p1-chenjing): A1 + rolling — a 13-round conversation whose early
  facts (订单号 A123456, 花生过敏) fell out of the sliding window; two
  ``memory.summary.generated`` audits (first generation, then a rolling
  merge), two ``done`` ``memory_tasks`` rows with merged dropped-message
  payloads, ``summary.queued`` run events on the truncating runs, and the
  current rolling summary on the conversation row. Rounds 12-13 arrived
  after the last generation — the "摘要生成后又有新消息" state.
- 刘洋 (demo-p1-liuyang): A4 — the summary model profile faulted; the task
  exhausted its attempts (``failed``, attempts=2, error
  ``model_provider_unavailable``), a ``memory.summary.failed`` audit row
  exists, the old summary is preserved on the conversation, and every run
  still completed normally (async degradation).
- 孙悦 (demo-p1-sunyue): A5 contrast — a short in-window conversation with
  ``summary`` NULL, no summary tasks, no ``summary.queued`` events and no
  summary audits.

All ids are uuid5-deterministic under the ``phase1`` namespace and the whole
module is skipped once the first identity binding exists, so re-runs never
duplicate rows. No ``pending`` task rows are seeded on purpose: a live
worker would claim them on boot and overwrite the curated summaries.

Display strings are localized via ``seed_memory_i18n`` (AGENT_SEED_LOCALE);
identity keys stay constant so the locale only affects first-time seeding.
"""

import json

from sqlalchemy import text

from agent_platform.apps.seed_memory_common import (
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

NS = ns("phase1")


def _chen_v1() -> str:
    """陈静 rolling summary v1 (first generation from the first truncated batch)."""
    return T(
        zh="客户陈静，恒瑞食品贸易有限公司采购主管，负责休闲零食类目选品。"
           "订单号 A123456（2026-10-05 试吃装批次），客户要求跟进进度。"
           "客户本人对花生严重过敏：含花生成分的零食一律排除，推荐前必须确认配料表。"
           "Q4 试吃装采购意向 300 盒，拟走年框合同流程。",
        en="Customer Chen Jing, Procurement Supervisor at Hengrui Food Trading Co., "
           "responsible for snack category selection. "
           "Order A123456 (2026-10-05 sample-pack batch); the customer asked for progress updates. "
           "The customer has a severe peanut allergy: exclude all snacks containing peanuts, "
           "and always confirm the ingredient list before recommending. "
           "Intent to purchase 300 sample packs in Q4, planned under an annual frame contract.",
        zh_tw="客戶陳靜，恆瑞食品貿易有限公司採購主管，負責休閒零食類目選品。"
              "訂單號 A123456（2026-10-05 試吃裝批次），客戶要求跟進進度。"
              "客戶本人對花生嚴重過敏：含花生成分的零食一律排除，推薦前必須確認配料表。"
              "Q4 試吃裝採購意向 300 盒，擬走年框合同流程。",
        hi="ग्राहक Chen Jing, Hengrui Food Trading Co. में खरीद पर्यवेक्षक, "
           "स्नैक श्रेणी के चयन के लिए जिम्मेदार। "
           "ऑर्डर A123456 (2026-10-05 सैंपल-पैक बैच); ग्राहक ने प्रगति अपडेट मांगे हैं। "
           "ग्राहक को मूंगफली से गंभीर एलर्जी है: मूंगफली युक्त सभी स्नैक्स बाहर रखें, "
           "और सुझाव देने से पहले सामग्री सूची अवश्य जांचें। "
           "Q4 में 300 सैंपल पैक खरीदने का इरादा, वार्षिक फ्रेम अनुबंध प्रक्रिया के तहत नियोजित।")


def _chen_v2() -> str:
    """陈静 rolling summary v2 (current value after rolling-merging the second batch)."""
    return T(
        zh="客户陈静，恒瑞食品贸易有限公司采购主管，负责休闲零食类目选品。"
           "订单号 A123456（2026-10-05 试吃装批次），需跟进进度。"
           "客户对花生严重过敏：含花生成分零食一律排除，推荐前必须确认配料表。"
           "Q4 试吃装采购意向 300 盒，走年框合同流程；混合坚果礼盒需确认无花生版本并留两箱样品；"
           "已要求按 300 盒出具含税含运费报价单。",
        en="Customer Chen Jing, Procurement Supervisor at Hengrui Food Trading Co., "
           "responsible for snack category selection. "
           "Order A123456 (2026-10-05 sample-pack batch); progress follow-up needed. "
           "The customer has a severe peanut allergy: exclude all snacks containing peanuts, "
           "and always confirm the ingredient list before recommending. "
           "Intent to purchase 300 sample packs in Q4 under an annual frame contract; "
           "confirm a peanut-free version of the mixed-nut gift box and reserve two sample cases; "
           "a tax- and shipping-inclusive quote for 300 packs has been requested.",
        zh_tw="客戶陳靜，恆瑞食品貿易有限公司採購主管，負責休閒零食類目選品。"
              "訂單號 A123456（2026-10-05 試吃裝批次），需跟進進度。"
              "客戶對花生嚴重過敏：含花生成分零食一律排除，推薦前必須確認配料表。"
              "Q4 試吃裝採購意向 300 盒，走年框合同流程；混合堅果禮盒需確認無花生版本並留兩箱樣品；"
              "已要求按 300 盒出具含稅含運費報價單。",
        hi="ग्राहक Chen Jing, Hengrui Food Trading Co. में खरीद पर्यवेक्षक, "
           "स्नैक श्रेणी चयन की जिम्मेदार। "
           "ऑर्डर A123456 (2026-10-05 सैंपल-पैक बैच); प्रगति पर फ़ॉलो-अप आवश्यक। "
           "ग्राहक को मूंगफली से गंभीर एलर्जी है: मूंगफली युक्त स्नैक्स पूरी तरह बाहर रखें, "
           "सुझाव से पहले सामग्री सूची अवश्य जांचें। "
           "Q4 में वार्षिक फ्रेम अनुबंध के तहत 300 सैंपल पैक खरीदने का इरादा; "
           "मिक्स्ड-नट गिफ्ट बॉक्स का मूंगफली-रहित संस्करण सुनिश्चित करें और दो सैंपल केस आरक्षित रखें; "
           "300 पैक के लिए कर व शिपिंग सहित कोटेशन मांगा गया है।")


def _liu_old() -> str:
    """刘洋's old summary, preserved verbatim after later summary tasks failed (A4 degradation)."""
    return T(
        zh="客户刘洋，远洋家居连锁集团售后对接人。"
           "2026-10-12 反馈主卧衣柜门板变形（型号 YG-2205 原木色，订单号 SO-20261008，左侧上门板），"
           "换货工单 TK-20261012 处理中，客户门店急着出样。",
        en="Customer Liu Yang, after-sales contact at Yuanyang Home Furnishing Chain Group. "
           "On 2026-10-12 reported a warped master-bedroom wardrobe door panel "
           "(model YG-2205 natural wood, order SO-20261008, upper-left door panel); "
           "replacement ticket TK-20261012 in progress — the customer's store urgently needs the display sample.",
        zh_tw="客戶劉洋，遠洋家居連鎖集團售後對接人。"
              "2026-10-12 反饋主臥衣櫃門板變形（型號 YG-2205 原木色，訂單號 SO-20261008，左側上門板），"
              "換貨工單 TK-20261012 處理中，客戶門店急著出樣。",
        hi="ग्राहक Liu Yang, Yuanyang Home Furnishing Chain Group में आफ्टर-सेल्स संपर्क सूत्र। "
           "2026-10-12 को मास्टर बेडरूम की अलमारी के दरवाज़े के पैनल के मुड़ने की शिकायत दर्ज की "
           "(मॉडल YG-2205 नैचुरल वुड, ऑर्डर SO-20261008, ऊपर-बायां दरवाज़ा पैनल); "
           "रिप्लेसमेंट टिकट TK-20261012 प्रगति पर है — ग्राहक के स्टोर को डिस्प्ले सैंपल की तत्काल ज़रूरत है।")


def _chen_msgs() -> list[str]:
    """陈静's 13 user messages; built per call so T() sees the active locale."""
    return [
        T(zh="你好，我是恒瑞食品的陈静，我们 10 月 5 号下的试吃装订单 A123456，麻烦帮我查下进度。",
          en="Hi, this is Chen Jing from Hengrui Food. Could you check the progress of our sample-pack order A123456 placed on October 5?",
          zh_tw="你好，我是恆瑞食品的陳靜，我們 10 月 5 號下的試吃裝訂單 A123456，麻煩幫我查下進度。",
          hi="नमस्ते, मैं Hengrui Food से Chen Jing बोल रही हूँ। क्या आप 5 अक्टूबर को दिए गए हमारे सैंपल-पैक ऑर्डर A123456 की प्रगति जांच सकते हैं?"),
        T(zh="补充一句，这批试吃装是我自己先尝的，我对花生严重过敏，含花生成分的品类千万不要推荐。",
          en="One more thing: I'm tasting these sample packs myself and I'm severely allergic to peanuts — never recommend anything containing peanuts.",
          zh_tw="補充一句，這批試吃裝是我自己先嚐的，我對花生嚴重過敏，含花生成分的品類千萬不要推薦。",
          hi="एक बात और: ये सैंपल पैक मैं खुद चख रही हूँ और मुझे मूंगफली से गंभीर एलर्जी है — मूंगफली वाली कोई भी वस्तु कभी सुझाएं नहीं।"),
        T(zh="我们 Q4 打算采购 300 盒试吃装，走年框合同流程，需要准备哪些材料？",
          en="We plan to purchase 300 sample packs in Q4 under an annual frame contract. What materials do we need to prepare?",
          zh_tw="我們 Q4 打算採購 300 盒試吃裝，走年框合同流程，需要準備哪些材料？",
          hi="हम Q4 में वार्षिक फ्रेम अनुबंध के तहत 300 सैंपल पैक खरीदने की योजना बना रहे हैं। हमें कौन-से दस्तावेज़ तैयार करने होंगे?"),
        T(zh="混合坚果礼盒有没有不含花生的版本？有的话帮我留两箱样品。",
          en="Does the mixed-nut gift box come in a peanut-free version? If so, please reserve two sample cases for me.",
          zh_tw="混合堅果禮盒有沒有不含花生的版本？有的話幫我留兩箱樣品。",
          hi="क्या मिक्स्ड-नट गिफ्ट बॉक्स का मूंगफली-रहित संस्करण उपलब्ध है? यदि हां, तो कृपया मेरे लिए दो सैंपल केस आरक्षित रखें।"),
        T(zh="报价单麻烦按 300 盒的量出一份，要含税含运费。",
          en="Please issue a quote for 300 packs, including tax and shipping.",
          zh_tw="報價單麻煩按 300 盒的量出一份，要含稅含運費。",
          hi="कृपया 300 पैक की मात्रा के लिए एक कोटेशन जारी करें, जिसमें कर और शिपिंग शामिल हों।"),
        T(zh="你们正常发货周期是几天？我们要赶 11 月初的行业展会。",
          en="What's your normal shipping lead time? We need to make an industry trade show in early November.",
          zh_tw="你們正常發貨週期是幾天？我們要趕 11 月初的行業展會。",
          hi="आपका सामान्य शिपिंग समय कितने दिनों का है? हमें नवंबर की शुरुआत में एक उद्योग ट्रेड शो के लिए तैयारी करनी है।"),
        T(zh="展会现场要摆样品，能不能先单独发 20 盒样品过来？",
          en="We need display samples at the show — could you ship 20 sample packs separately first?",
          zh_tw="展會現場要擺樣品，能不能先單獨發 20 盒樣品過來？",
          hi="शो में डिस्प्ले सैंपल चाहिए — क्या आप पहले 20 सैंपल पैक अलग से भेज सकते हैं?"),
        T(zh="样品走顺丰到付就行，收货地址我稍后发你。",
          en="Ship the samples via SF Express freight collect; I'll send you the delivery address shortly.",
          zh_tw="樣品走順豐到付就行，收貨地址我稍後發你。",
          hi="सैंपल SF Express से फ्रेट कलेक्ट पर भेज दें; डिलीवरी पता मैं थोड़ी देर में भेज दूँगी।"),
        T(zh="回复的时候给我列要点就行，先说结论再讲细节，谢谢。",
          en="Just give me bullet points in your replies — conclusion first, then details. Thanks.",
          zh_tw="回覆的時候給我列要點就行，先說結論再講細節，謝謝。",
          hi="जवाब में बस बुलेट पॉइंट्स दे दीजिए — पहले निष्कर्ष, फिर विवरण। धन्यवाद।"),
        T(zh="收货地址：杭州市滨江区恒瑞大厦 3 层，陈静收，电话还是那个手机号。",
          en="Delivery address: 3F Hengrui Building, Binjiang District, Hangzhou, attn. Chen Jing; phone is the same mobile number.",
          zh_tw="收貨地址：杭州市濱江區恆瑞大廈 3 層，陳靜收，電話還是那個手機號。",
          hi="डिलीवरी पता: 3F Hengrui Building, Binjiang District, Hangzhou, attn. Chen Jing; फ़ोन नंबर वही मोबाइल नंबर है।"),
        T(zh="合同流程走到哪一步了？我们法务这周要评审。",
          en="Where is the contract process now? Our legal team reviews it this week.",
          zh_tw="合同流程走到哪一步了？我們法務這週要評審。",
          hi="अनुबंध प्रक्रिया अभी किस चरण पर है? हमारी कानूनी टीम इस सप्ताह समीक्षा करेगी।"),
        T(zh="报价单我收到了，价格没问题，等合同盖章后回传。",
          en="I received the quote — the pricing is fine. I'll send it back once the contract is stamped.",
          zh_tw="報價單我收到了，價格沒問題，等合同蓋章後回傳。",
          hi="कोटेशन मिल गया — कीमत ठीक है। अनुबंध पर मुहर लगते ही मैं इसे वापस भेज दूँगी।"),
        T(zh="在吗？帮我推荐两款适合展会派发的小包装零食，还是老规矩，注意我的过敏情况。",
          en="Are you there? Please recommend two individually wrapped snacks suitable for handing out at the show — same as always, mind my allergy.",
          zh_tw="在嗎？幫我推薦兩款適合展會派發的小包裝零食，還是老規矩，注意我的過敏情況。",
          hi="क्या आप वहाँ हैं? कृपया शो में बांटने के लिए दो छोटे पैक वाले स्नैक्स सुझाएं — हमेशा की तरह, मेरी एलर्जी का ध्यान रखें।"),
    ]


def _liu_msgs() -> list[str]:
    """刘洋's 10 user messages; built per call so T() sees the active locale."""
    return [
        T(zh="你好，我是远洋家居的刘洋，上周送到的主卧衣柜有一块门板变形了，麻烦安排换货。",
          en="Hi, this is Liu Yang from Yuanyang Home Furnishing. The master-bedroom wardrobe delivered last week has a warped door panel — please arrange a replacement.",
          zh_tw="你好，我是遠洋家居的劉洋，上週送到的主臥衣櫃有一塊門板變形了，麻煩安排換貨。",
          hi="नमस्ते, मैं Yuanyang Home Furnishing से Liu Yang बोल रहा हूँ। पिछले सप्ताह डिलीवर हुई मास्टर बेडरूम की अलमारी का एक दरवाज़ा पैनल मुड़ गया है — कृपया रिप्लेसमेंट की व्यवस्था करें।"),
        T(zh="型号是 YG-2205 原木色，订单号 SO-20261008，变形的是左侧上门板。",
          en="The model is YG-2205 natural wood, order SO-20261008; the warped one is the upper-left door panel.",
          zh_tw="型號是 YG-2205 原木色，訂單號 SO-20261008，變形的是左側上門板。",
          hi="मॉडल YG-2205 नैचुरल वुड है, ऑर्डर नंबर SO-20261008; मुड़ा हुआ पैनल ऊपर-बायां दरवाज़ा है।"),
        T(zh="换货工单 TK-20261012 建立了吗？我们门店急着出样。",
          en="Has replacement ticket TK-20261012 been created? Our store urgently needs the display sample.",
          zh_tw="換貨工單 TK-20261012 建立了嗎？我們門店急著出樣。",
          hi="क्या रिप्लेसमेंट टिकट TK-20261012 बन गया है? हमारे स्टोर को डिस्प्ले सैंपल की तुरंत ज़रूरत है।"),
        T(zh="新门板什么时候能发？最好这周发货。",
          en="When can the new door panel ship? Ideally this week.",
          zh_tw="新門板什麼時候能發？最好這週發貨。",
          hi="नया दरवाज़ा पैनल कब तक शिप हो सकता है? बेहतर होगा कि इसी सप्ताह शिप हो जाए।"),
        T(zh="安装师傅到货后 24 小时内能上门吗？需要提前约。",
          en="Can the installer come within 24 hours of delivery? We need to book in advance.",
          zh_tw="安裝師傅到貨後 24 小時內能上門嗎？需要提前約。",
          hi="क्या डिलीवरी के 24 घंटों के भीतर इंस्टॉलर आ सकता है? हमें पहले से अपॉइंटमेंट बुक करनी होगी।"),
        T(zh="旧门板需要退回吗？还是师傅安装时直接带走？",
          en="Do we need to return the old panel, or will the installer take it away during installation?",
          zh_tw="舊門板需要退回嗎？還是師傅安裝時直接帶走？",
          hi="क्या पुराना पैनल वापस भेजना होगा, या इंस्टॉलर इंस्टॉलेशन के समय उसे साथ ले जाएगा?"),
        T(zh="以后联系我用企业微信，工作时间电话不一定接得到。",
          en="Contact me via WeCom from now on — I may not answer phone calls during work hours.",
          zh_tw="以後聯繫我用企業微信，工作時間電話不一定接得到。",
          hi="अब से मुझसे WeCom पर संपर्क करें — काम के घंटों में फ़ोन कॉल का जवाब देना संभव नहीं हो पाता।"),
        T(zh="工单 TK-20261012 物流显示已发货，德邦 DB7712，大概哪天到？",
          en="Ticket TK-20261012 shows as shipped, Deppon DB7712. Roughly when will it arrive?",
          zh_tw="工單 TK-20261012 物流顯示已發貨，德邦 DB7712，大概哪天到？",
          hi="टिकट TK-20261012 की लॉजिस्टिक्स स्थिति 'शिप्ड' दिखा रही है, Deppon DB7712। अंदाज़न किस दिन पहुंचेगा?"),
        T(zh="德邦显示明天派送，麻烦帮忙约后天上午的安装。",
          en="Deppon shows delivery tomorrow — please book the installation for the morning of the day after.",
          zh_tw="德邦顯示明天派送，麻煩幫忙約後天上午的安裝。",
          hi="Deppon कल डिलीवरी दिखा रहा है — कृपया परसों सुबह की इंस्टॉलेशन बुक कर दीजिए।"),
        T(zh="安装约好了告诉我一声，我让门店留人。",
          en="Let me know once the installation is booked so I can have the store keep someone on site.",
          zh_tw="安裝約好了告訴我一聲，我讓門店留人。",
          hi="इंस्टॉलेशन बुक हो जाए तो मुझे बता दीजिएगा, ताकि मैं स्टोर पर किसी को मौजूद रखूँ।"),
    ]


def _sun_msgs() -> list[str]:
    """孙悦's 3 user messages; built per call so T() sees the active locale."""
    return [
        T(zh="你好，我是晴空户外的孙悦，想咨询一下帐篷的批发价，麻烦发份报价到我邮箱。",
          en="Hi, this is Sun Yue from Clear Sky Outdoor. I'd like to ask about tent wholesale pricing — please email me a quote.",
          zh_tw="你好，我是晴空戶外的孫悅，想諮詢一下帳篷的批發價，麻煩發份報價到我郵箱。",
          hi="नमस्ते, मैं Clear Sky Outdoor से Sun Yue बोल रही हूँ। मुझे टेंट के थोक मूल्य के बारे में जानना है — कृपया मुझे ईमेल पर कोटेशन भेज दें।"),
        T(zh="我们门店大概 80 平米，想先各款拿 5 顶试销。",
          en="Our store is about 80 square meters; we'd like to trial-sell 5 units of each model first.",
          zh_tw="我們門店大概 80 平米，想先各款拿 5 頂試銷。",
          hi="हमारा स्टोर लगभग 80 वर्ग मीटर का है; हम पहले हर मॉडल के 5-5 टेंट ट्रायल बिक्री के लिए लेना चाहेंगे।"),
        T(zh="报价收到了，双人帐那款再便宜 5 块可以吗？可以的话这周就下单。",
          en="Got the quote — could the two-person tent be 5 yuan cheaper? If so, we'll order this week.",
          zh_tw="報價收到了，雙人帳那款再便宜 5 塊可以嗎？可以的話這週就下單。",
          hi="कोटेशन मिल गया — क्या दो-व्यक्ति वाला टेंट 5 युआन सस्ता हो सकता है? यदि हां, तो हम इसी सप्ताह ऑर्डर कर देंगे।"),
    ]


def counts_for(injected):
    """Section counts for the memory.injected event, mirroring insert_run's default."""
    return {"flags": sum(1 for _, sec, _ in injected if sec == "flag"),
            "items": sum(1 for _, sec, _ in injected if sec in ("profile", "preference")),
            "events": sum(1 for _, sec, _ in injected if sec == "event")}


def events_with_summary(cid, injected_ids, counts, gateway_call_id, dropped):
    """The 8-event run sequence of a truncating run: summary.queued at seq 5.

    Emission order mirrors repository.finish(): summary dispatch first, then
    extraction, then the injection telemetry event, then run completion.
    """
    return [
        (1, "run.queued", {}),
        (2, "run.started", {}),
        (3, "model.started", {"round": 1}),
        (4, "model.completed", {"round": 1, "usage": {}, "tool_calls": 0, "duration_ms": 141,
                                "first_token_ms": 38, "gateway_call_id": gateway_call_id}),
        (5, "summary.queued", {"dropped": dropped}),
        (6, "memory_extract.queued", {"customer_id": cid}),
        (7, "memory.injected", {**counts, "item_ids": injected_ids}),
        (8, "run.completed", {"output": default_reply(), "error_code": None}),
    ]


async def insert_summary_task(conn, tenant, task_id, conv_id, dropped_messages, old_summary,
                              status, attempts, error, created_h, updated_h):
    """One memory_tasks row of kind conversation_summary (done or failed)."""
    payload = {"conversation_id": str(conv_id), "dropped_messages": dropped_messages,
               "old_summary": old_summary}
    await conn.execute(
        text("""INSERT INTO memory_tasks(id,tenant_id,kind,status,payload,attempts,max_attempts,
                error,run_after,created_at,updated_at)
                VALUES(:id,:t,'conversation_summary',:st,CAST(:p AS jsonb),:a,2,:err,:ra,:ca,:ua)
                ON CONFLICT DO NOTHING"""),
        {"id": task_id, "t": tenant, "st": status, "p": json.dumps(payload, ensure_ascii=False),
         "a": attempts, "err": error, "ra": ts(created_h), "ca": ts(created_h), "ua": ts(updated_h)},
    )


def dropped_pair(idx, messages):
    """The user/assistant pair of round ``idx`` as a task payload entry list."""
    return [{"role": "user", "content": messages[idx]},
            {"role": "assistant", "content": default_reply()}]


async def seed_chenjing(conn, tenant, agent, model_backend):
    """A1 + rolling: long conversation, generated summary, queued events, tasks."""
    msgs = _chen_msgs()
    cid = uid(NS, "customer", "chenjing")
    conv_id = uid(NS, "conversation", "demo-p1-chenjing")
    specs = {
        "profile:company": item("profile", "company",
                                T(zh="恒瑞食品贸易有限公司", en="Hengrui Food Trading Co.",
                                  zh_tw="恆瑞食品貿易有限公司", hi="Hengrui Food Trading Co."),
                                .98, st="manual", rc=9, ch=15 * 24, uh=30),
        "profile:role": item("profile", "role",
                             T(zh="采购主管，负责休闲零食类目选品与供应商对接",
                               en="Procurement Supervisor, responsible for snack category selection and supplier liaison",
                               zh_tw="採購主管，負責休閒零食類目選品與供應商對接",
                               hi="खरीद पर्यवेक्षक — स्नैक श्रेणी चयन एवं आपूर्तिकर्ता समन्वय की जिम्मेदार"),
                             .92, sr="demo-p1-chenjing", rc=4, ch=14 * 24, uh=30),
        "preference:allergy": item("preference", "allergy",
                                   T(zh="客户对花生严重过敏：含花生成分零食一律排除，推荐前必须确认配料表",
                                     en="Customer has a severe peanut allergy: exclude all snacks containing peanuts; always confirm the ingredient list before recommending",
                                     zh_tw="客戶對花生嚴重過敏：含花生成分零食一律排除，推薦前必須確認配料表",
                                     hi="ग्राहक को मूंगफली से गंभीर एलर्जी है: मूंगफली युक्त सभी स्नैक्स बाहर रखें; सुझाव देने से पहले सामग्री सूची अवश्य जांचें"),
                                   .99, sr="demo-p1-chenjing", rc=11, ch=49, uh=30),
        "preference:tone": item("preference", "tone",
                                T(zh="偏好要点式回复，先说结论再讲细节",
                                  en="Prefers bullet-point replies, conclusion first then details",
                                  zh_tw="偏好要點式回覆，先說結論再講細節",
                                  hi="बुलेट-पॉइंट शैली के उत्तर पसंद करती हैं — पहले निष्कर्ष, फिर विवरण"),
                                .9, sr="demo-p1-chenjing", rc=5, ch=32, uh=30),
    }
    item_ids = {k: uid(NS, "item", f"chenjing:{k}") for k in specs}
    # (hours_ago, message, dropped_this_run, injected item keys)
    rounds = [
        (50, msgs[0], 0, ["profile:company"]),
        (49, msgs[1], 0, ["profile:company", "preference:allergy"]),
        (48, msgs[2], 0, ["profile:company"]),
        (47, msgs[3], 0, ["preference:allergy", "profile:company"]),
        (44, msgs[4], 0, ["profile:company"]),
        (40, msgs[5], 0, ["profile:company"]),
        (36, msgs[6], 2, ["profile:company"]),
        (34, msgs[7], 2, ["profile:company"]),
        (32, msgs[8], 2, ["preference:tone", "profile:company"]),
        (30, msgs[9], 2, ["profile:company"]),
        (27, msgs[10], 2, ["profile:company"]),
        (20, msgs[11], 0, ["profile:company", "preference:tone"]),
        (3, msgs[12], 0, ["preference:allergy", "preference:tone",
                          "profile:company", "profile:role"]),
    ]
    await insert_customer(conn, tenant, cid, 15 * 24, 3)
    await insert_identity(conn, tenant, uid(NS, "identity", "demo-p1-chenjing"), cid,
                          "demo-p1-chenjing", 15 * 24)
    for key, spec in specs.items():
        await insert_item(conn, tenant, item_ids[key], cid, spec, last_referenced_h=3)
    await insert_conversation(conn, tenant, conv_id, "demo-p1-chenjing", pairs(msgs),
                              50.1, 3, agent["id"], summary=_chen_v2(), cid=cid)
    for n, (hours, msg, dropped, keys) in enumerate(rounds):
        injected = [(item_ids[k], specs[k]["t"],
                     specs[k]["conf"] if specs[k]["t"] in ("profile", "preference") else None)
                    for k in keys]
        events = None
        if dropped:
            run_id = uid(NS, "run", f"chenjing:{n}")
            events = events_with_summary(cid, [iid for iid, _, _ in injected],
                                         counts_for(injected), uid(NS, "gw", run_id), dropped)
        await insert_run(conn, tenant, NS, f"chenjing:{n}", conv_id, agent, model_backend,
                         msg, hours, cid=cid, injected=injected, events=events)
    # Two done summary tasks: task payloads hold the merged dropped pairs.
    task1, task2 = uid(NS, "task", "chenjing:1"), uid(NS, "task", "chenjing:2")
    await insert_summary_task(conn, tenant, task1, conv_id,
                              [m for i in (0, 1, 2) for m in dropped_pair(i, msgs)],
                              None, "done", 1, None, 32, 31.9)
    await insert_summary_task(conn, tenant, task2, conv_id,
                              [m for i in (3, 4) for m in dropped_pair(i, msgs)],
                              _chen_v1(), "done", 1, None, 27, 26.9)
    await insert_audit(conn, tenant, "memory", "memory.summary.generated", conv_id,
                       {"conversation_id": conv_id, "input_messages": 6, "old_length": 0,
                        "new_length": len(_chen_v1()), "usage": {}}, 31.9)
    await insert_audit(conn, tenant, "memory", "memory.summary.generated", conv_id,
                       {"conversation_id": conv_id, "input_messages": 4,
                        "old_length": len(_chen_v1()), "new_length": len(_chen_v2()), "usage": {}}, 26.9)


async def seed_liuyang(conn, tenant, agent, model_backend):
    """A4: summary model fault — task failed, old summary kept, runs unaffected."""
    msgs = _liu_msgs()
    cid = uid(NS, "customer", "liuyang")
    conv_id = uid(NS, "conversation", "demo-p1-liuyang")
    specs = {
        "profile:company": item("profile", "company",
                                T(zh="远洋家居连锁集团", en="Yuanyang Home Furnishing Chain Group",
                                  zh_tw="遠洋家居連鎖集團", hi="Yuanyang Home Furnishing Chain Group"),
                                .97, st="manual", rc=6, ch=10 * 24, uh=30),
        "event:open_ticket:TK-20261012": item(
            "event", "open_ticket:TK-20261012",
            T(zh="衣柜门板变形换货工单（型号 YG-2205，订单 SO-20261008）：新门板已发货，"
                 "德邦 DB7712 在途，到货后 24 小时内约安装",
              en="Wardrobe door-panel warping replacement ticket (model YG-2205, order SO-20261008): "
                 "new panel shipped, Deppon DB7712 in transit; book installation within 24 hours of arrival",
              zh_tw="衣櫃門板變形換貨工單（型號 YG-2205，訂單 SO-20261008）：新門板已發貨，"
                    "德邦 DB7712 在途，到貨後 24 小時內約安裝",
              hi="अलमारी दरवाज़ा-पैनल वार्पिंग रिप्लेसमेंट टिकट (मॉडल YG-2205, ऑर्डर SO-20261008): "
                 "नया पैनल शिप हो चुका है, Deppon DB7712 रास्ते में; आगमन के 24 घंटों के भीतर इंस्टॉलेशन बुक करें"),
            .95, st="manual", sr="demo-p1-liuyang", rc=3, ch=72, uh=8),
        "preference:contact_channel": item("preference", "contact_channel",
                                           T(zh="工作时间优先企业微信沟通，电话不一定接得到",
                                             en="Prefers WeCom during work hours; phone calls may go unanswered",
                                             zh_tw="工作時間優先企業微信溝通，電話不一定接得到",
                                             hi="काम के घंटों में WeCom को प्राथमिकता देते हैं; फ़ोन कॉल का जवाब मिलना संभव नहीं हो पाता"),
                                           .88, sr="demo-p1-liuyang", rc=2, ch=50, uh=50),
    }
    item_ids = {k: uid(NS, "item", f"liuyang:{k}") for k in specs}
    ticket = "event:open_ticket:TK-20261012"
    rounds = [
        (80, msgs[0], 0, ["profile:company"]),
        (78, msgs[1], 0, ["profile:company"]),
        (72, msgs[2], 0, ["profile:company", ticket]),
        (70, msgs[3], 0, [ticket]),
        (60, msgs[4], 0, [ticket, "profile:company"]),
        (55, msgs[5], 0, [ticket]),
        (50, msgs[6], 0, ["preference:contact_channel", "profile:company"]),
        (30, msgs[7], 0, [ticket, "profile:company"]),
        (8, msgs[8], 2, [ticket, "preference:contact_channel"]),
        (2, msgs[9], 0, [ticket, "preference:contact_channel", "profile:company"]),
    ]
    await insert_customer(conn, tenant, cid, 10 * 24, 2)
    await insert_identity(conn, tenant, uid(NS, "identity", "demo-p1-liuyang"), cid,
                          "demo-p1-liuyang", 10 * 24)
    for key, spec in specs.items():
        await insert_item(conn, tenant, item_ids[key], cid, spec, last_referenced_h=2)
    await insert_conversation(conn, tenant, conv_id, "demo-p1-liuyang", pairs(msgs),
                              80.1, 2, agent["id"], summary=_liu_old(), cid=cid)
    for n, (hours, msg, dropped, keys) in enumerate(rounds):
        injected = [(item_ids[k], specs[k]["t"],
                     specs[k]["conf"] if specs[k]["t"] in ("profile", "preference") else None)
                    for k in keys]
        events = None
        if dropped:
            run_id = uid(NS, "run", f"liuyang:{n}")
            events = events_with_summary(cid, [iid for iid, _, _ in injected],
                                         counts_for(injected), uid(NS, "gw", run_id), dropped)
        await insert_run(conn, tenant, NS, f"liuyang:{n}", conv_id, agent, model_backend,
                         msg, hours, cid=cid, injected=injected, events=events)
    # The failed rolling-summary task: two attempts, then permanently failed.
    task_id = uid(NS, "task", "liuyang:1")
    await insert_summary_task(conn, tenant, task_id, conv_id, dropped_pair(2, msgs),
                              _liu_old(), "failed", 2, "model_provider_unavailable", 8, 7.4)
    # The earlier successful generation that produced the preserved summary.
    await insert_audit(conn, tenant, "memory", "memory.summary.generated", conv_id,
                       {"conversation_id": conv_id, "input_messages": 4, "old_length": 0,
                        "new_length": len(_liu_old()), "usage": {}}, 48)
    await insert_audit(conn, tenant, "memory", "memory.summary.failed", conv_id,
                       {"task_id": task_id, "error": "model_provider_unavailable",
                        "attempts": 2}, 7.4)


async def seed_sunyue(conn, tenant, agent, model_backend):
    """A5 contrast: short in-window conversation, no summary anywhere."""
    msgs = _sun_msgs()
    cid = uid(NS, "customer", "sunyue")
    conv_id = uid(NS, "conversation", "demo-p1-sunyue")
    specs = {
        "profile:company": item("profile", "company",
                                T(zh="晴空户外用品店", en="Clear Sky Outdoor Gear Store",
                                  zh_tw="晴空戶外用品店", hi="Clear Sky Outdoor Gear Store"),
                                .9, sr="demo-p1-sunyue", rc=2, ch=3 * 24, uh=20),
        "preference:contact_channel": item("preference", "contact_channel",
                                           T(zh="偏好邮件沟通，报价与合同资料发邮箱",
                                             en="Prefers email communication; send quotes and contract materials by email",
                                             zh_tw="偏好郵件溝通，報價與合同資料發郵箱",
                                             hi="ईमेल द्वारा संचार पसंद करती हैं; कोटेशन और अनुबंध दस्तावेज़ ईमेल पर भेजें"),
                                           .85, sr="demo-p1-sunyue", rc=2, ch=26, uh=20),
    }
    item_ids = {k: uid(NS, "item", f"sunyue:{k}") for k in specs}
    rounds = [
        (26, msgs[0], ["profile:company", "preference:contact_channel"]),
        (20, msgs[1], ["profile:company"]),
        (4, msgs[2], ["profile:company", "preference:contact_channel"]),
    ]
    await insert_customer(conn, tenant, cid, 3 * 24, 4)
    await insert_identity(conn, tenant, uid(NS, "identity", "demo-p1-sunyue"), cid,
                          "demo-p1-sunyue", 3 * 24)
    for key, spec in specs.items():
        await insert_item(conn, tenant, item_ids[key], cid, spec, last_referenced_h=4)
    await insert_conversation(conn, tenant, conv_id, "demo-p1-sunyue", pairs(msgs),
                              26.1, 4, agent["id"], summary=None, cid=cid)
    for n, (hours, msg, keys) in enumerate(rounds):
        injected = [(item_ids[k], specs[k]["t"],
                     specs[k]["conf"] if specs[k]["t"] in ("profile", "preference") else None)
                    for k in keys]
        await insert_run(conn, tenant, NS, f"sunyue:{n}", conv_id, agent, model_backend,
                         msg, hours, cid=cid, injected=injected)


async def seed(runtime, settings) -> bool:
    """Insert the phase-1 dataset once; return False when already seeded."""
    p = runtime.platform
    tenant = settings.tenant_id
    async with p.engine.connect() as conn:
        if await identity_bound(conn, tenant, "web", "demo-p1-chenjing"):
            return False
    agent = await ensure_agent(
        p, tenant, "seed",
        T(zh="摘要演示助手", en="Summary Demo Assistant",
          zh_tw="摘要演示助手", hi="सारांश डेमो सहायक"),
        T(zh="阶段1会话摘要验收演示：滚动摘要生成、注入与失败降级（摘要开关开启）",
          en="Phase 1 conversation-summary acceptance demo: rolling summary generation, injection, and failure degradation (summary switch on)",
          zh_tw="階段1會話摘要驗收演示：滾動摘要生成、注入與失敗降級（摘要開關開啟）",
          hi="चरण 1 वार्तालाप-सारांश स्वीकृति डेमो: रोलिंग सारांश निर्माण, इंजेक्शन और विफलता डिग्रेडेशन (सारांश स्विच चालू)"),
        AgentConfig(system_prompt=T(
            zh="你是一名企业客服助手。结合对话摘要记住客户早期提供的关键信息，回答简洁专业、结论先行。",
            en="You are an enterprise customer-service assistant. Use the conversation summary to remember key information the customer provided earlier; reply concisely and professionally, conclusion first.",
            zh_tw="你是一名企業客服助手。結合對話摘要記住客戶早期提供的關鍵信息，回答簡潔專業、結論先行。",
            hi="आप एक एंटरप्राइज़ ग्राहक-सेवा सहायक हैं। वार्तालाप सारांश की मदद से ग्राहक द्वारा पहले दी गई मुख्य जानकारी याद रखें; उत्तर संक्षिप्त और पेशेवर दें, निष्कर्ष पहले रखें।"),
                    tool_names=[], knowledge_base_ids=[], summary_enabled=True))
    async with p.engine.begin() as conn:
        await seed_chenjing(conn, tenant, agent, settings.model_backend)
        await seed_liuyang(conn, tenant, agent, settings.model_backend)
        await seed_sunyue(conn, tenant, agent, settings.model_backend)
    return True


if __name__ == "__main__":
    run_standalone(seed, lambda: T(zh="阶段1·会话摘要演示数据",
                                   en="Phase 1 conversation-summary demo data",
                                   zh_tw="階段1·會話摘要演示數據",
                                   hi="चरण 1 · वार्तालाप-सारांश डेमो डेटा"))
