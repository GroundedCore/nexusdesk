# Runtime Worker

运行：`uv run python -m agent_platform.apps.worker`。

从 PostgreSQL 领取任务，执行异步 Runtime，续租并检查取消。支持多个进程消费；单会话活跃运行受数据库唯一索引保护。配置与 API 共用环境变量。

# Memory Worker

运行：`uv run python -m agent_platform.apps.worker.memory`。

生产拓扑的独立常驻进程：认领 `memory_tasks` 异步记忆任务（会话摘要等），SKIP LOCKED + 120 秒租约 + 心跳续约 + reaper，多副本安全、可独立伸缩。quickstart/开发不经过此入口——`AGENT_EMBEDDED_WORKER=true` 时同一认领循环内嵌于 API 进程。`AGENT_SUMMARY_ENABLED=false` 时进程空转待命（不消费任务也不退出，避免容器重启循环）。
# Channel Worker

独立运行：`uv run python -m agent_platform.apps.worker.channel`。

消费入站 outbox（`channel_inbound_events`）与出站投递队列（`channel_deliveries`），把外部聊天渠道的收发从 API 进程里拆出来，便于独立扩缩容与隔离第三方故障。两个循环都用 `FOR UPDATE SKIP LOCKED` 加租约领取，多副本安全。

quickstart/开发版保持嵌入：`AGENT_EMBEDDED_CHANNEL_WORKER` 未设置时跟随 `AGENT_EMBEDDED_WORKER`（默认 true，循环嵌在 API 进程）；生产设置 `AGENT_EMBEDDED_WORKER=false`（或显式 `AGENT_EMBEDDED_CHANNEL_WORKER=false`）后由本容器接管，API 不再启动这两个循环。

唤醒与并发：入站落库、出站入队与人工重放都在同一事务里 `pg_notify('channel_work')`，两个循环用共享 `LISTEN` 连接即时唤醒；`AGENT_CHANNEL_WORKER_POLL_SECONDS`（默认 5）是未收到通知或回收过期租约时的兜底轮询。每进程并发领取循环数由 `AGENT_CHANNEL_WORKER_CONCURRENCY`（默认 4）控制，`FOR UPDATE SKIP LOCKED` 保证同一行只被处理一次。

共享 token：各 provider 的 access token 走 `channel_access_tokens` 二级缓存（scope 用密钥哈希，不落明文），避免多副本各自向 provider 取 token。
