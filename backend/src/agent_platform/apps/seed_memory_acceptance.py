"""Seed the phase 1-5 memory acceptance datasets; safe to run again.

Phase 6 has its own seeder (``seed_memory_demo``); this entrypoint runs the
five per-phase modules built on ``seed_memory_common`` so a fresh server
boots into a console that demonstrates every shipped memory capability:
conversation summaries (P1), extracted customer profiles (P2), event memory
and search (P3), governance states (P4) and agent-level memory switches
(P5). Each module is idempotent, so re-runs only report what already exists.

Display strings follow ``Settings.seed_locale`` via ``seed_memory_i18n``;
identity keys stay constant so the locale only affects first-time seeding.
"""

import asyncio

from sqlalchemy import text

from agent_platform.apps import (
    seed_memory_phase1,
    seed_memory_phase2,
    seed_memory_phase3,
    seed_memory_phase4,
    seed_memory_phase5,
)
from agent_platform.apps.seed_memory_common import LOCK_ID
from agent_platform.apps.seed_memory_i18n import T, set_locale
from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.settings import Settings


def _phases():
    """Phase list; built per call so T() sees the active locale."""
    return [
        (T(zh="阶段1·会话摘要", en="Phase 1 · Conversation Summaries",
           zh_tw="階段1·會話摘要", hi="चरण 1 · वार्तालाप सारांश"), seed_memory_phase1),
        (T(zh="阶段2·客户画像", en="Phase 2 · Customer Profiles",
           zh_tw="階段2·客戶畫像", hi="चरण 2 · ग्राहक प्रोफ़ाइल"), seed_memory_phase2),
        (T(zh="阶段3·事件记忆与搜索", en="Phase 3 · Event Memory & Search",
           zh_tw="階段3·事件記憶與搜尋", hi="चरण 3 · इवेंट मेमोरी व खोज"), seed_memory_phase3),
        (T(zh="阶段4·记忆治理", en="Phase 4 · Memory Governance",
           zh_tw="階段4·記憶治理", hi="चरण 4 · मेमोरी गवर्नेंस"), seed_memory_phase4),
        (T(zh="阶段5·Agent记忆配置", en="Phase 5 · Agent Memory Config",
           zh_tw="階段5·Agent記憶配置", hi="चरण 5 · Agent मेमोरी कॉन्फ़िग"), seed_memory_phase5),
    ]


async def main():
    settings = Settings()
    tenant = settings.tenant_id
    locale = set_locale(settings)
    async with runtime_services(settings) as runtime, runtime.platform.engine.connect() as lock:
        await lock.execute(text(f"SELECT pg_advisory_lock({LOCK_ID})"))
        try:
            marks = []
            for label, module in _phases():
                inserted = await module.seed(runtime, settings)
                mark = (T(zh="✓新入库", en="✓seeded", zh_tw="✓新入庫", hi="✓सीड हुआ") if inserted
                        else T(zh="·已存在", en="·exists", zh_tw="·已存在", hi="·मौजूद"))
                marks.append(f"{label}{mark}")
            print(T(zh=f"记忆验收演示数据就绪（{'，'.join(marks)}；租户：{tenant}）。",
                    en=f"Memory acceptance demo data ready ({', '.join(marks)}; tenant: {tenant}; locale: {locale}).",
                    zh_tw=f"記憶驗收演示數據就緒（{'，'.join(marks)}；租戶：{tenant}）。",
                    hi=f"मेमोरी स्वीकृति डेमो डेटा तैयार ({', '.join(marks)}; टेनेंट: {tenant}; लोकेल: {locale})।"))
        finally:
            await lock.execute(text(f"SELECT pg_advisory_unlock({LOCK_ID})"))


if __name__ == "__main__":
    asyncio.run(main())
