# Runtime Worker

运行：`uv run python -m agent_platform.apps.worker`。

从 PostgreSQL 领取任务，执行异步 Runtime，续租并检查取消。支持多个进程消费；单会话活跃运行受数据库唯一索引保护。配置与 API 共用环境变量。

# Memory Worker

运行：`uv run python -m agent_platform.apps.worker.memory`。

生产拓扑的独立常驻进程：认领 `memory_tasks` 异步记忆任务（会话摘要等），SKIP LOCKED + 120 秒租约 + 心跳续约 + reaper，多副本安全、可独立伸缩。quickstart/开发不经过此入口——`AGENT_EMBEDDED_WORKER=true` 时同一认领循环内嵌于 API 进程。`AGENT_SUMMARY_ENABLED=false` 时进程空转待命（不消费任务也不退出，避免容器重启循环）。
