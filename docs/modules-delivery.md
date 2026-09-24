# 模块首轮实施说明

本轮在已有可运行模块基础上增量实施。各模块 README 第 1 节是当前代码状态；规划表的 P0/P1 不代表全部已完成。逐模块交付和剩余工作见 [实施记录](implementation-progress.md)。

## 安装与迁移

在 backend 执行 `uv sync`，然后使用目标 PostgreSQL 配置执行 `uv run alembic upgrade head`。本轮新增 0004_tool_versions、0005_service_identity、0006_knowledge_jobs、0007_vector_index。现有开发数据库未自动迁移；启动新版前需要执行迁移。前端在 frontend 执行 `npm ci`。

API：`uv run uvicorn agent_platform.apps.api.main:app --host 127.0.0.1 --port 8000`。
前端：`npm run dev`。
文档 Worker（单独终端、相同数据库和租户配置）：`uv run python -m agent_platform.apps.worker.knowledge`。
文档任务不会因只启动 API 自动被处理。Worker 可运行多个进程，通过 PG SKIP LOCKED 领取任务；120 秒租约，单次执行总预算 90 秒。进程意外退出后过期任务重新领取，三次失联后失败；人工重试需显式调用。

## 配置外部文档服务

配置名称均以 AGENT_ 开头，见 backend/.env.example。Parser 由部署者配置固定服务地址，不接收终端用户指定上游 URL。

- KNOWLEDGE_PARSER_URL：统一 Parser 基址，调用 `POST /parse`，multipart 字段 `file`。
- KNOWLEDGE_S3_ENDPOINT / KNOWLEDGE_S3_BUCKET / KNOWLEDGE_S3_ACCESS_KEY / KNOWLEDGE_S3_SECRET_KEY：可选 S3 原件归档，兼容 MinIO；私密配置只在进程使用。
- MILVUS_URL / MILVUS_TOKEN：Milvus REST v2 地址与访问令牌。

Parser 返回示例：

```json
{"pages":[{"page_num":1,"text":"页面正文","elements":[]}]}
```

页码必须从 1 连续递增；elements 为解析器扩展数据，当前仅保存，尚未转换成带坐标的检索引用。PDF、Office、图片、OFD、CAJ、XPS 全部依赖 Parser 实际支持，未配置时返回 parser_not_configured，不能把扩展名接收当成格式验收。TXT/Markdown 直接 UTF-8 解码。

当前限制：默认上传 20 MB，解析 HTTP 60 秒，响应体不超过上传上限，聚合文本最多 200000 字符。Parser 整体返回 JSON 后按页写入 PG；重试重新解析，不提供解析器内部逐页恢复。S3 只归档原件，PG 暂存内容尚未自动清理，生产需完成容量/保留期治理。

Milvus 使用独立集合构建，成功后更新 PG 指针；单次最多 1000 个当前有效片段。需先在模型网关发布 Embedding 配置，在知识库页面手动重建。向量检索接口位于 `/api/v1/knowledge-bases/{id}/vector-search`（以 OpenAPI 路由为准），Runtime 默认继续使用关键词检索。文档更新后需手动重建；旧集合和失败构建集合暂不自动删除。查询结果经过 PG 再校验，删除、停用及旧版本不会作为有效引用返回。

协议参考：[Milvus REST v2](https://milvus.io/api-reference/restful/v2.5.x/v2/Collection%20(v2)/Create.md)、[Boto3 S3](https://docs.aws.amazon.com/boto3/latest/reference/services/s3.html)。真实 Parser/Milvus/S3 尚未联调。

## 工具、人员与评估

工具新建自动发布 v1；后续编辑只更新草稿，显式发布后 Agent 再发布才固定新版本。现有 Agent 版本仍保留旧快照，停用工具立即影响运行授权。模拟检查不请求上游。HTTP 工具仍只读，业务写入走提议与确认。

访问设置可创建个人 operator/viewer 凭证，一次显示，数据库保存哈希。普通员工只能操作自己负责的交接与人工回复，管理员可代管。渠道可配置 HMAC 密钥引用：hex(HMAC-SHA256(secret, timestamp + '.' + raw_body))，请求头 X-Channel-Timestamp、X-Channel-Signature；300 秒时钟窗口，不取代渠道令牌及消息 ID 去重。

评估可选固定 Chat 配置用于 Judge、Embedding 配置用于相似度。硬规则失败优先；Judge 的四维结果严格校验，未校准结果进入 needs_review。尚无人工复核保存和自动发布门禁，不应把 needs_review 当成通过。

## 验证结果

2026-09-20：82 项后端测试（81 项全量回归与新增 1 项 Judge 契约测试）通过，覆盖真实 PostgreSQL 迁移/事务与模拟上游协议；TypeScript 检查和 Vite 生产构建通过；2 条 Playwright 流程通过（模型发布调用绑定，以及知识→Agent→会话→工单确认→人工接管→评估）。这些测试不代表真实模型效果、全格式文档质量或生产并发容量验收。

后续新增 0008_ticketing、0009_ticket_settings；本页前述 0004～0007 为首轮记录，最新迁移与兼容范围见 [数据库迁移说明](database-migrations.md)。
