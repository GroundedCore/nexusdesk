"""Seed the phase 1-5 memory acceptance datasets; safe to run again.

Phase 6 has its own seeder (``seed_memory_demo``); this entrypoint runs the
five per-phase modules built on ``seed_memory_common`` so a fresh server
boots into a console that demonstrates every shipped memory capability:
conversation summaries (P1), extracted customer profiles (P2), event memory
and search (P3), governance states (P4) and agent-level memory switches
(P5). Each module is idempotent, so re-runs only report what already exists.
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
from agent_platform.modules.agent_runtime.bootstrap import runtime_services
from agent_platform.settings import Settings

PHASES = [
    ("阶段1·会话摘要", seed_memory_phase1),
    ("阶段2·客户画像", seed_memory_phase2),
    ("阶段3·事件记忆与搜索", seed_memory_phase3),
    ("阶段4·记忆治理", seed_memory_phase4),
    ("阶段5·Agent记忆配置", seed_memory_phase5),
]


async def main():
    settings = Settings()
    tenant = settings.tenant_id
    async with runtime_services(settings) as runtime, runtime.platform.engine.connect() as lock:
        await lock.execute(text(f"SELECT pg_advisory_lock({LOCK_ID})"))
        try:
            marks = []
            for label, module in PHASES:
                inserted = await module.seed(runtime, settings)
                marks.append(f"{label}{'✓新入库' if inserted else '·已存在'}")
            print(f"记忆验收演示数据就绪（{'，'.join(marks)}；租户：{tenant}）。")
        finally:
            await lock.execute(text(f"SELECT pg_advisory_unlock({LOCK_ID})"))


if __name__ == "__main__":
    asyncio.run(main())
