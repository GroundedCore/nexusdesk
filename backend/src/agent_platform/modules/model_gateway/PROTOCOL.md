# 模型网关接入与运行

本文件描述当前代码的实际协议；README 保留后续完整规划。

## 启动

在 `C:\code\ai-project\backend` 执行：

```powershell
uv sync --dev
uv run alembic upgrade head
# 可选：初始化五类能力的演示配置，不会修改已有 Agent 绑定。
uv run python -m agent_platform.apps.seed_models
uv run uvicorn agent_platform.apps.api.main:app --reload --port 8000
```

数据库地址沿用 `.env` 的 `AGENT_DATABASE_URL`。初始化会增加 `gateway_*` 表；上线前按部署流程备份数据库。前端继续使用 `npm install`、`npm run dev`，在“模型网关”页面依次配置连接、模型和方案。执行本次验证使用独立测试数据库，未迁移用户业务数据库。

真实服务凭据由 API 与 Worker 的进程环境注入，例如 PowerShell：

```powershell
$env:AGENT_MODEL_GATEWAY_ALLOWED_HOSTS='["api.openai.com","models.example.com"]'
# 在部署平台安全注入 AGENT_MODEL_SECRET_PRIMARY，不在管理台提交密钥正文。
```

`credential_ref` 只接受 `AGENT_MODEL_SECRET_` 前缀的环境变量名。自定义变量不会因为写在 Pydantic 的 `.env` 文件里自动进入 `os.environ`；请使用服务管理器、容器 Secret 或进程环境注入。密钥值轮换后重启相关进程；引用名称或地址修改需要重新发布方案。环境变量为空则调用返回 `model_credential_unavailable`，不会发出匿名请求。

## 管理接口与权限

所有路径以 `/api/v1/model-gateway` 为前缀，沿用平台 Bearer 服务令牌和可信租户上下文。

| 路径 | 用途 | 权限 |
|---|---|---|
| GET /connections、/models、/profiles | 查看目录，最多最近 200 条 | Reader |
| POST /connections、/models、/profiles | 创建资源，Schema 见 contracts.py | Admin |
| PUT /{kind}/{id}?revision=N | 更新完整草稿，乐观锁 | Admin |
| PATCH /{kind}/{id} | `{revision, enabled}` 启停 | Admin |
| POST /connections/{id}/probe | 非推理连通性检测 | Admin |
| POST /profiles/{id}/publish | `{revision}`，生成不可变版本 | Admin |
| POST /profiles/{id}/rollback | `{version}`，切换发布指针 | Admin |
| GET /profiles/{id}/versions | 历史发布快照 | Reader |
| POST /invoke | 六种 operation 的非流式调用 | Operator |
| POST /stream | Chat SSE；demo / openai_compatible | Operator |
| GET /calls、/calls/{id} | 调用元数据与每次尝试 | Reader |
| POST /media | 原始二进制请求体，Content-Type 指定格式 | Operator |
| GET /media/{id} | 本租户媒体下载，需 Bearer 令牌 | Operator |

资源类型 kind 为 connections/models/profiles。普通用户不能通过请求参数指定 tenant_id、供应商 URL、凭据或任意路径。

### 连接、模型、方案示例

```json
{"name":"主模型连接","protocol":"openai_compatible","base_url":"https://api.openai.com/v1","credential_ref":"AGENT_MODEL_SECRET_PRIMARY","concurrency":8}
```

```json
{"name":"客服 Chat","connection_id":"替换为连接 UUID","model_name":"供应商的模型标识","operations":["chat"],"tool_calling":true,"max_input_chars":32000,"max_batch":32}
```

```json
{"name":"客服快速问答","operation":"chat","model_id":"替换为模型 UUID","fallback_model_ids":[],"require_tools":true,"parameters":{"max_tokens":1024},"timeout_seconds":30,"retries":1}
```

Embedding 模型必须填写 `embedding_dimension` 与不可混用的 `vector_space` 版本标识；TTS 必须登记供应商允许的 `voices`。参数 `temperature` / `max_tokens` 只适用于 Chat，并需选择支持这些参数的实际模型；暂未自动探测各供应商的全部模型规格。

方案的 operation 创建后不可修改，变更能力需新建方案。发布会保存模型及连接配置快照，不含密钥值。Agent 的 `model_profile_id` 与 `model_profile_version` 必须同时填写，只接受 Chat；使用工具的 Agent 要求所有候选模型支持工具调用。修改草稿、重新发布或回滚发布指针均不改变已经绑定的固定版本。停用方案、模型或连接会阻止其新调用。连接并发值按当前配置生效；地址、模型、生成参数按发布快照执行。

## 三种适配协议

| 协议 | 能力 | 说明 |
|---|---|---|
| demo | 六种 operation | 显式返回 demo=true；哈希向量、字符重合排序、固定文本、静音 WAV，不是真实模型 |
| openai_compatible | chat、embed、transcribe、synthesize | Chat Completions / embeddings / audio/transcriptions / audio/speech；不假设兼容 Rerank 或 OCR |
| gateway_http | 六种 operation，非流式 | 平台自定义 HTTP 契约，适合自建 Rerank/OCR/模型服务，需要提供方实现此协议 |

OpenAI 兼容服务必须把 base_url 配到 API 根路径（通常含 `/v1`）。ASR 使用 multipart 文件请求；TTS 接收二进制音频。语音字段与端点参考 [OpenAI Audio API](https://platform.openai.com/docs/api-reference/audio)。此适配器并不保证所有“兼容 OpenAI”的供应商支持全部能力，需要实际联调。

连接检测：openai_compatible 调用 `GET {base_url}/models`，gateway_http 调用 `GET {base_url}/health`。检测只证明该端点可访问，不证明模型推理、工具调用或质量合格。检测结果返回并写管理审计，没有后台健康检查任务。

### 统一调用

```json
{"profile_id":"替换为方案 UUID","version":1,"payload":{"operation":"chat","messages":[{"role":"user","content":"你好"}],"tools":[]}}
```

其他 operation 的 payload：

```json
{"operation":"embed","inputs":[{"id":"doc-1","text":"售后政策"}]}
```
```json
{"operation":"rerank","query":"如何退货","candidates":[{"id":"doc-1","text":"退货流程"}],"top_n":1}
```
```json
{"operation":"transcribe","media_id":"已上传的音频 UUID","language":"zh"}
```
```json
{"operation":"synthesize","text":"您好","voice_id":"供应商允许的声音标识","format":"wav"}
```
```json
{"operation":"recognize","pages":[{"source_id":"page-1","media_id":"已上传的图片 UUID"}]}
```

响应包含 `call_id`、`attempt_id`、`operation`、`profile_id`、`profile_version`、`model_id`、`model_name`、`demo`、`payload`、`usage`。用量未知返回 null，已知项只保留有限的非负数值单位，不估算费用。真实调用可能收费；重试可能产生重复推理用量，网关不重试业务工具。

### gateway_http 的提供方契约

网关向 `{base_url}/{operation}` 发 POST，固定请求结构：

```json
{"model":"provider-model-id","request":{"operation":"rerank","query":"退货","candidates":[{"id":"a","text":"退货流程"}],"top_n":1},"parameters":{},"media":{}}
```

媒体操作的 media 字典以已校验租户的 UUID 为键，每项包含 `mime_type` 与 `base64`。提供方不需要访问平台媒体下载接口。此处仅在服务间传输媒体，普通调用日志不保存正文。

提供方返回 `{"payload": <下表>, "usage": null}`：

| operation | payload |
|---|---|
| chat | `{content: "文本", tool_calls: [{id, name, args: {}}]}` |
| embed | `{vectors: [{id: "输入 ID", vector: [0.1, 0.2]}]}`，顺序与输入一致 |
| rerank | `{results: [{id: "候选 ID", score: 0.8}]}`，恰好 top_n 条 |
| transcribe | `{text: "转写文本"}`，静音时允许空文本 |
| synthesize | `{audio_base64: "...", mime_type: "audio/wav"}` 或 audio/mpeg |
| recognize | `{pages: [{source_id: "page-1", text: "识别文本"}]}`，顺序与输入一致 |

未知/重复结果 ID、错误向量维度、非有限浮点数、错误音频格式会被拒绝。当前 OCR 只承诺页面文本，不声明 bbox、表格、公式、版面分析；不能直接把 Foil 的 Layout/General 返回当作此协议。需要在 Parser/供应商服务侧实现适配。ASR 当前不承诺时间戳、说话人或时长检测。

### 流式 Chat

`POST /stream` 使用同一个 Invocation 请求体，返回 SSE：`delta`、`completed`、`error`。delta 包含文本或 OpenAI 工具参数增量；最终工具调用以 completed.payload 为准，消费者不能执行未完成的参数片段。completed 包含完整结果与调用 ID。demo 仅发一次演示增量；gateway_http 当前显式拒绝流式请求。

输出前对 429、5xx、网络错误做有限重试/主备切换；已发送增量后遇到错误直接发 error，绝不拼接另一模型输出。上游缺少 `[DONE]` 视作流中断。队列大小 1，响应缓冲有上限；消费者断开会取消生产任务。API 流式与现有 Runtime 运行事件流独立，Runtime 内仍使用完整一轮回复驱动工具循环。

## 运行与存储边界

- 并发为**每个 API/Worker 进程**的 operation 隔离额度，以及跨方案共享的连接额度。多个进程的总并发会叠加；尚不是分布式供应商配额。部署时按进程数分配供应商预算，批量任务可使用独立连接保留 Chat 额度。
- 总超时覆盖网关排队、重试和模型调用；Runtime 自身的单轮/Run 总预算仍优先约束。取消记录只代表本地停止等待，不保证供应商停止计费。
- 只接受管理员配置的主机白名单；禁用环境代理和重定向，非本机连接要求 HTTPS。生产应配合网络出站策略约束 DNS/地址变更。
- 媒体首版暂存 PostgreSQL BYTEA，有效期 24 小时，默认单文件 5 MB、每租户总量 100 MB。上传时清理同租户最多 100 个过期对象；运维应定期执行 `DELETE FROM gateway_media WHERE expires_at < now()`。尚未迁移 MinIO/S3，勿用于大规模音视频任务。
- 上传格式：WAV、MP3、OGG、WebM、PNG、JPEG。只进行大小、声明类型和用途校验，真实解码/采样率/像素与音频时长限制需媒体层及供应商校验。
- 元数据记录只保留实际模型、状态、错误码、耗时与分项用量。没有完整内容日志、自动数据保留任务或供应商实账。进程崩溃留下的 running 记录表示终态未知，不能当作成功。
- 统一媒体/调用服务供其他模块接入；本次没有替换知识检索为 Milvus、实现文档解析调度、Judge 评测或语音渠道。

## 验证

`tests/test_model_gateway.py` 使用独立 PostgreSQL 和 httpx MockTransport，覆盖六种 operation、发布快照、权限、禁用、租户隔离、模型空间、输出校验、重试、取消和流式边界。浏览器测试位于 `frontend/tests/model-gateway.spec.ts`。没有真实模型密钥时，契约测试不等于实际模型质量验收。

```powershell
$env:TEST_DATABASE_URL='postgresql+asyncpg://测试用户@127.0.0.1:测试端口/测试库'
uv run pytest -q
```
