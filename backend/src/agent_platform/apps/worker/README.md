# Runtime Worker

运行：`uv run python -m agent_platform.apps.worker`。

从 PostgreSQL 领取任务，执行异步 Runtime，续租并检查取消。支持多个进程消费；单会话活跃运行受数据库唯一索引保护。配置与 API 共用环境变量。
