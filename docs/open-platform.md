# 开放平台：企业 Agent API 与集成

入口：控制台「开放平台」。管理员创建应用、授权已发布 Agent、创建应用密钥；企业服务端通过 `/openapi/v1` 调用。页面与 Agent 管理采用相同主色、卡片和间距规范。

## 接入方式与职责

开放平台统一展示两个入口：

| 入口 | 适用场景 | 当前协议 |
| --- | --- | --- |
| 应用接入 | 自研网站、App、企业系统调用 Agent，管理授权、密钥、限流与集成 | `/openapi/v1` |
| 通用消息接入 | 外部客服消息映射到会话，经过客服路由处理并拉取 Agent、人工回复 | `/api/v1/ingress/channels/{id}/messages` 与 `replies` |

“通用消息接入”由原“配置管理 → 渠道接入”迁入，旧页面地址 `#/channels` 自动转到 `#/open-platform/channels`。现有渠道 Token、HMAC、去重和接口保持有效；应用密钥与渠道 Token 不互通。企微、飞书等原生协议适配尚未实现。

Webhook、调用日志和 SDK 文档仍属于具体应用，先进入“应用接入”选择应用后管理。此调整统一导航，不把两套会话语义当作相同接口：渠道经过客服路由，开放平台消息当前直接提交 Runtime。后续统一底层客服会话能力后再收敛通用协议，避免丢失人工协同能力。

## 配置与权限

- 每个应用具有独立 App ID、启停状态、Agent 授权、每分钟请求额度和最大并发数。
- 密钥 `opk_...` 由服务端生成，仅创建/轮换响应返回完整值。数据库只保存 SHA-256 摘要与掩码。支持有效期、撤销和最长 24 小时的轮换过渡期。
- 后台 admin 可修改应用与密钥，operator/viewer 只读。应用密钥不接受后台 API 访问，也不会回退到开发环境匿名管理权限。
- 只有已发布且未归档的 Agent 可授权；运行采用应用指定版本的快照；未固定时采用当前发布版本。
- 企业服务端负责登录用户认证，再传入 `X-External-User-ID`。持有应用密钥的服务端可以代表本应用用户调用，这个头本身不是用户认证凭据。密钥不得置于公开网页或移动端。
- 会话与运行归属按应用和外部用户双重检查。撤销授权后旧会话与运行也不可继续访问。

## 接口

所有接口要求 `Authorization: Bearer <APP_KEY>`，会话和运行接口还要求 `X-External-User-ID`。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /openapi/v1/agents | 当前应用可调用的 Agent |
| POST | /openapi/v1/conversations | 创建或复用会话 |
| GET | /openapi/v1/conversations/{id}/messages | 消息历史，after/limit 游标分页 |
| POST | /openapi/v1/conversations/{id}/messages | 普通或 SSE 调用 |
| GET | /openapi/v1/runs/{id} | 查询运行结果 |
| GET | /openapi/v1/runs/{id}/events | SSE 事件流、断线重连 |
| POST | /openapi/v1/runs/{id}/cancel | 请求取消运行 |

创建会话正文：`{"agent_id":"...","external_session_id":"session-001"}`。同一应用、用户、外部会话标识的重试返回同一 conversation_id；更换 Agent 需新会话标识。

发送消息正文：`{"message":"你好","stream":false,"wait_seconds":30}`。要求 `Idempotency-Key` 头，范围为应用＋用户；同一消息的网络重试复用此值，同一值对应不同正文或会话返回 409。请求去重与创建运行在同一事务内，支持多个 API 进程并发。

普通调用最多等待 30 秒，完成返回 200；仍在排队/运行返回 202，随后查询运行或连接事件流。HTTP 200 不代表模型执行必然成功，需检查 status/error_code。

## SSE 语义

当前 Runtime 使用完整模型响应，**不支持模型 token 增量输出**。一期开放真实运行事件流，而非模拟逐字输出：

- `accepted`：包含 run_id/request_id，无事件序号。
- `run.queued`、`run.started`、`model.started`、`model.completed`、`tool.*` 等进度事件：公开流只返回 run_id，避免泄漏工具参数或内部追踪信息。
- `run.completed`：包含完整 output 和公开运行状态。
- `run.failed` / `run.cancelled`：包含最终状态与 error_code。
- `error`：连接存续期间密钥失效、应用停用或授权撤销后终止流。
- 每个持久化事件有递增 id，重连使用 `Last-Event-ID` 或 after。SSE 块可能跨多个网络数据块，客户端需缓冲解析。
- 心跳每约 15 秒一次，代理需关闭缓冲。关闭流不取消运行；取消需显式调用 cancel。

## 限流、日志及调试

按应用在 PostgreSQL 中使用固定分钟窗口计数，多个密钥和 API 实例共享额度；查询与轮询也计数。超过 RPM 返回 429 / Retry-After: 60，并发额度包含排队与运行中任务，超过返回 429 / Retry-After: 2。Runtime 全局与租户额度继续生效。

每次请求响应包含 X-Request-ID。已识别应用的请求日志包含路由模板、HTTP 状态、响应耗时、错误码、request_id/run_id，不存储密钥、请求头和正文。身份未知的无效密钥无法归属应用，不出现在应用日志中。SSE 响应耗时仅到响应头生成，最终运行状态/耗时通过关联 Runtime 查询。

在线调试使用用户直接输入的真实应用密钥，仅存页面内存；经过相同的公开鉴权、限流和日志链路。支持普通响应、事件流、查询结果和取消。页面提供 cURL / Python / JavaScript 服务端调用示例。

## 部署

执行 `alembic upgrade head` 增加 `0025_open_platform` 的六张独立表，不改写现有 Agent、知识库和会话。回滚会删除开放平台配置与日志，实际接入后不可直接降级。

Vite 和生产/快速体验 nginx 均转发 `/openapi/`。快速体验 nginx 对该路径传递原始 Authorization，不注入本地管理令牌。正式集成应使用 HTTPS 和已有部署的访问控制。

## 验证与后续

后端覆盖摘要存储、密钥轮换/过期/撤销、管理角色隔离、跨应用/用户/租户访问、授权撤销、请求幂等、并发重试、RPM/并发限制、终态 SSE、游标重连、取消及日志脱敏；迁移在临时数据库执行升级/降级/再升级。

第二期已加入版本固定、知识库写入、Webhook 与 SDK（见下文）。后续范围：真正的模型 token 流、SSO、日志保留策略与按 Token 的配额。

## 第二期：企业集成

新增「集成配置」「Webhook 回调」页面。迁移 `0026_open_integrations` 只增加开放平台字段和表，现有应用默认跟随当前发布版本、无知识库写权限、无回调。

### Agent 版本固定

`agent_versions` 是 Agent ID 到版本号的映射；未指定的 Agent 继续跟随当前发布版本。仅允许固定该应用已授权 Agent 的已发布历史版本。`GET /openapi/v1/agents` 的 `effective_version` 返回实际采用版本；新运行持久化该版本配置、工具和模型配置快照。归档、删除和工具/模型禁用限制继续生效。修改应用绑定不会改变已经开始的运行。主应用编辑保留集成配置，并通过 revision 防止覆盖并发修改。

### 知识库同步 API

管理员在应用「集成配置」单独授权 `knowledge_base_ids`。授权意味着可同步文档并显式发布**整个知识库**，包含该库的其他待发布草稿；可为企业系统建立专用知识库。

以下接口只要求应用 Bearer 密钥，无需外部用户头；仍计入应用 RPM 和请求日志。

| 方法 | 路径（前缀 /openapi/v1） | 参数 |
|---|---|---|
| GET | /knowledge-bases | 当前应用授权的知识库 |
| GET | /knowledge-bases/{id}/documents | offset，最多 100 条，仅本应用文档 |
| PUT | /knowledge-bases/{id}/documents/{external_id} | title、content、expected_version |
| DELETE | /knowledge-bases/{id}/documents/{external_id} | 查询参数 expected_version |
| POST | /knowledge-bases/{id}/publish | 头 Idempotency-Key |
| GET | /knowledge-tasks/{task_id} | 本应用发布任务状态与进度 |

`external_id` 长度 1–128，建议使用不含斜线的业务文档 ID。本期支持文本/Markdown 正文，长度最多 200,000 字符；采用现有默认分块策略。首次写入 expected_version=0；修改/删除使用列表或写入响应中的 version。相同正文和标题的重复写入返回 unchanged=true，不增加版本；若后台手工改过文档，则须使用最新版本明确覆盖。跨应用无法更新或删除其他应用同步的文档，即使共享同一知识库。

文档保存为草稿，不立即覆盖线上发布版本。发布接口复用现有知识库索引队列，返回 202 / task_id；任务状态为 queued、running、completed、failed、cancelled。相同发布 Idempotency-Key 返回同一任务；修正失败原因后使用新 key 创建新任务。部署需运行现有 `python -m agent_platform.apps.worker.knowledge` 知识任务 Worker。

删除仅停用本库中本应用同步的关联，立即从检索中隐藏，保留历史，不删除其他库的关联。恢复已删除的同步文档会生成新草稿，需重新发布才恢复检索。

### Webhook

每个应用可配置一个回调地址。管理员可启停、更换地址、轮换签名密钥并重试失败投递。签名密钥仅首次创建/主动轮换展示一次，使用现有凭据保险库加密存储，并绑定租户、应用和地址。正常 GET 不返回密钥或密文。

回调由 API 进程中的独立任务分发，与 Runtime Worker 模式无关。数据库持久队列支持多个 API 实例；通过领取租约和 owner 防止并发重复领取，进程中断后 60 秒租约到期可恢复。无论重试多少次，event_id 保持稳定。投递语义为至少一次，接收端必须去重。

事件：`run.completed`、`run.failed`、`run.cancelled`、`knowledge.completed`、`knowledge.failed`、`knowledge.cancelled`。仅投递保存/启用回调配置之后终结的运行或任务，不回放此前历史。

```json
{"event_id":"...","type":"run.completed","app_id":"...","data":{"resource_id":"run UUID","request_id":"...","conversation_id":"...","error_code":null}}
```

知识事件的 resource_id 为发布 task_id。事件不包含消息正文、完整回答或应用密钥；企业系统可用自己的用户映射查询运行结果。

- 签名头：X-Webhook-ID、X-Webhook-Timestamp、X-Webhook-Signature。
- 签名：`sha256=` + HMAC-SHA256(signing_secret, timestamp + `.` + 原始请求体字节)。SDK 提供常量时间比较及默认 ±300 秒时间窗口校验；验签后仍需按 event_id 去重。
- 回调允许 HTTP/HTTPS 企业内网地址；正式部署推荐 HTTPS。禁用 URL 中的账号密码、片段和重定向；请求不使用环境代理。
- 2xx 表示成功；非 2xx 或网络异常最多尝试 5 次，失败后的间隔为 2、10、30、120 秒。单次 HTTP 超时 10 秒。接收端应快速落库/入队再返回 2xx。
- 投递记录展示事件、资源 ID、状态、次数、HTTP 状态和错误码，不保留响应正文。
- 保存回调配置会取消旧代待投递记录；更换密钥/地址后不自动把旧事件发送到新地址。只允许重试当前配置的 failed 记录。在途请求可能已发送，无法撤回。

### SDK 分发

控制台「集成配置」和「接入文档」提供 Python / Node.js ZIP 下载。源代码位于 `sdk/`，运行 `python sdk/build_archives.py` 重新生成前端公开下载包。包未上传 PyPI/npm，采用本地安装：`pip install ./python`、`npm install ./javascript`。

SDK 提供会话创建、消息/事件流、游标重连、运行查询/取消、知识同步/发布/任务查询、Webhook 验签。Python 使用标准库，无运行时依赖；JavaScript 要求 Node.js 20+。不自动重试写操作，由调用方保留 Idempotency-Key，避免网络超时后重复执行业务。ZIP 内 README 含完整示例。

第二期不包含模型 Token 增量输出、SSO、按 Token 配额和日志自动清理。


## 第三期：企业接入体验

已增加嵌入聊天组件、企业用户身份映射、通用 OIDC 和企业微信/钉钉/飞书登录，工作台和聊天入口均可使用。配置步骤、部署要求、服务端换票及权限边界见 [企业身份与嵌入聊天](open-platform-identity.md)。
