# 中间件与外部服务

NexusDesk 使用 PostgreSQL 保存业务数据与任务队列，其他服务按功能启用。Docker 操作见 [部署手册](../deploy/README.md)。

| 服务 | 用途 | 启用条件 |
| --- | --- | --- |
| PostgreSQL | 会话、工单、配置、Runtime 与知识库任务队列 | 必需；部署模板使用 PostgreSQL 17 |
| Milvus | 向量索引与召回 | 向量及混合检索需要；关键词检索不需要 |
| MinIO / S3 | 原始文档归档 | 可选；未配置时原始文件保存在 PostgreSQL |
| 模型 API | Chat、Embedding、Rerank、OCR、语音调用 | 使用对应真实模型能力时配置 |
| 外部文档解析服务 | 扫描件 OCR 与部分特殊格式解析 | 超出本地解析能力时配置 |
| Nginx | 前端静态资源与 `/api` 反向代理 | 应用镜像已包含；本地开发使用 Vite 代理 |

当前任务调度不依赖 Redis、RabbitMQ、Kafka 或 Celery。使用外部模型 API 时，应用服务器无需部署 vLLM 或配置 GPU。Milvus 的 etcd、对象存储等依赖取决于所选部署模式，应遵循对应部署配置。

## 环境配置

生产模板见 [deploy/production/.env.example](../deploy/production/.env.example)，本地开发模板见 [backend/.env.example](../backend/.env.example)。凭据通过环境配置注入，不写入代码或镜像。

| 环境变量 | 配置内容 |
| --- | --- |
| `AGENT_DATABASE_URL` | PostgreSQL 连接串，使用 `postgresql+asyncpg://` 驱动前缀 |
| `AGENT_MILVUS_URL` / `AGENT_MILVUS_TOKEN` | Milvus API 地址与凭据 |
| `AGENT_KNOWLEDGE_S3_ENDPOINT` / `AGENT_KNOWLEDGE_S3_BUCKET` | 对象存储地址与桶名 |
| `AGENT_KNOWLEDGE_S3_ACCESS_KEY` / `AGENT_KNOWLEDGE_S3_SECRET_KEY` | 对象存储凭据 |
| `AGENT_KNOWLEDGE_PARSER_URL` | 外部解析服务地址 |
| `AGENT_MODEL_GATEWAY_ALLOWED_HOSTS` | 模型服务域名白名单 |
| `AGENT_MODEL_CREDENTIAL_KEY_FILE` | 凭据加密主密钥路径 |

未配置外部解析服务时只使用本地解析能力；模型网关的 OCR 接口不能直接替代文档解析适配器。

## 数据与部署边界

- PostgreSQL、Milvus 和对象存储分别做持久化与备份。迁移已有数据库时必须保留原来的凭据主密钥，API 与 Worker 使用同一份密钥。
- [根目录 Compose](../compose.yaml) 仅启动开发用 PostgreSQL。
- [快速体验 Compose](../deploy/quickstart/compose.yaml) 启动 App 与 PostgreSQL 两容器。
- [生产 Compose](../deploy/production/compose.yaml) 启动 Web、API、Runtime Worker、Knowledge Worker，以及一次性迁移服务，复用外部中间件。
- [知识库中间件 Compose](../deploy/knowledge/compose.yaml) 是特定服务器的部署配置，包含固定地址与 host 网络设置，不能直接作为通用模板。历史环境信息见 [服务器记录](middleware-server.md)。
- 容器访问同一 Compose 网络内的服务时使用服务名，访问外部中间件时使用可达地址；容器内的 `127.0.0.1` 不代表宿主机。
