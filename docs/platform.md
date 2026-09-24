# 客服平台 MVP 使用说明

后端为 Python / FastAPI，执行层为异步 LangGraph，数据与队列保存在 PostgreSQL；前端为 TypeScript / React。普通客服工具直接调用固定 API，无需代码沙箱。

## 升级与启动

已有项目请在后端目录执行（保留已有 `.env`）：

```powershell
cd C:\code\ai-project\backend
uv sync
uv run alembic upgrade head
uv run python -m agent_platform.apps.seed
uv run uvicorn agent_platform.apps.api.main:app --reload --host 127.0.0.1 --port 8000
```

`seed` 是可选的示例初始化，创建“示例服务知识库”“示例客服 Agent”和 `sample-support` 会话。重复执行不会覆盖已编辑内容。必须先配置 `AGENT_DATABASE_URL` 并执行迁移；新增迁移为 `0002_platform`，保留已有 Runtime 表和数据。

另开终端启动前端：

```powershell
cd C:\code\ai-project\frontend
npm ci
npm run dev
```

访问 http://127.0.0.1:5173。默认 `AGENT_MODEL_BACKEND=demo` 可离线演示知识查询和工单流程，回复会明确显示演示模式；它不是通用语言模型。知识检索、工单和人工协作演示无需启动外部工具服务。使用 `lookup_demo` 才需要 [Runtime 文档](runtime.md) 中的 mock_business。

真实模型需要在后端设置 `AGENT_MODEL_BACKEND=openai`、`AGENT_MODEL_NAME`、`AGENT_MODEL_API_KEY`，可选 `AGENT_MODEL_BASE_URL`。API 与独立 Worker 必须使用相同运行配置。密钥不传入前端。

## 模块与操作流程

| 模块 | 已实现能力 | 管理台入口 |
|---|---|---|
| Agent 配置 | 草稿修订、工具与知识库授权、不可变发布版本、版本回滚 | Agent 管理 |
| Runtime | 模型—工具循环、异步队列、并发预算、取消、SSE 阶段事件 | 会话工作台 / 运行与审计 |
| 模型网关 | 演示模式、OpenAI 适配、超时和 Token 用量 | 访问与策略展示配置 |
| 知识服务 | 文本/Markdown、文档版本、分块索引、中英文关键词检索、引用 | 知识库 |
| 工具网关 | 固定 GET API 注册、参数校验、主机白名单、启停和超时 | 工具目录 |
| 会话管理 | 消息账本、Agent 绑定、自动/等待/人工/关闭状态 | 会话工作台 |
| 客服业务 | 工单提案、确认/拒绝、幂等创建和工单状态流转 | 会话工作台 / 服务工单 |
| 人工协同 | 转接、摘要、接管、人工回复、恢复 Agent、关闭 | 人工协同 |
| 渠道接入 | 独立令牌、通用 Webhook、消息去重、会话映射、回复拉取 | 渠道接入 |
| 策略权限 | admin/operator/viewer、部署租户隔离、工具及知识范围、预算 | 访问与策略 |
| 观测审计 | 24 小时统计、运行记录、事件回放、管理操作审计 | 运行与审计 |
| 质量评测 | 用例、模拟业务工具、输出/工具断言、按版本保存报告 | 质量评测 |

建议按以下顺序体验：

1. 在知识库中创建服务文档并检索验证；或使用初始化示例。
2. 新建 Agent，选择 `knowledge_search`、`propose_ticket` 及知识库，保存草稿后发布。未发布草稿不会进入真实运行；提交的 Run 固定当时发布版本。
3. 新建会话时选择已发布 Agent，发送“什么时候发货？”，查看回复及 `knowledge.retrieved` 事件中的文档引用。默认 Runtime 只使用服务器配置的工具。
4. 发送“请创建工单”，等待运行结束，在待确认卡片中确认。只有确认成功后才会创建 PostgreSQL 工单，重复确认返回同一张工单。
5. 点击转人工，或发送精确文本“转人工”/“人工客服”。在人工协同页接管，到会话页回复，完成后恢复 Agent 或关闭会话。
6. 创建评测用例，填入模拟工具结果及预期文本后运行。业务工具不会调用真实接口；真实模型模式仍会调用模型服务。

会话首次加载显示最近 200 条消息；接口 `after` 可增量读取之后的消息。模型上下文独立受 `AGENT_HISTORY_TURNS` 限制，不等于消息存储保留期限。

## 权限及工具配置

| 环境变量 | 作用 |
|---|---|
| `AGENT_API_TOKEN` | 管理员令牌；非 development 环境必填 |
| `AGENT_OPERATOR_API_TOKEN` | 可选客服操作令牌 |
| `AGENT_VIEWER_API_TOKEN` | 可选只读令牌 |
| `AGENT_TENANT_ID` | 服务端固定租户，不接受模型或请求指定租户 |
| `AGENT_TOOL_ALLOWED_HOSTS` | 可注册工具主机 JSON 数组，例如 `["crm.internal"]` |
| `AGENT_TOOL_SECRET_*` | 工具请求头引用的服务端凭据 |

三个角色使用不同令牌。开发环境未配置管理员令牌时允许本地管理员访问；共享部署应设置令牌。工作台“访问与策略”填写服务令牌，保存在当前浏览器 sessionStorage 中。admin 管理配置、渠道和评测；operator 操作会话、人工接管与工单；viewer 只读。审计记录当前令牌角色，尚不是个人员工身份审计。

API 注册工具只能使用 `AGENT_TOOL_ALLOWED_HOSTS` 中的固定主机。参数要求 JSON Schema `type=object`、`additionalProperties=false`，GET 参数仅支持标量。`headers_from_env` 只能引用 `AGENT_TOOL_SECRET_` 前缀的环境变量；实际凭据由启动进程环境注入。服务器上的 `AGENT_TOOLS_FILE` 属于受信任运维配置。停用数据库工具会阻止后续实际调用；不会撤回已经发出的 HTTP 请求。

工具目录的注册和编辑表单支持“接口鉴权”：选择“密钥鉴权”后默认直接输入密钥，不需要设置工具环境变量。`Authorization` 填写完整的 `Bearer <实际密钥>`，`X-API-Key` 直接填写实际密钥；可以添加多个请求头。已保存密钥不回显，留空保留、输入新值替换，移除请求头或选择“无需鉴权”后保存会清空草稿中的对应配置。修改接口地址或请求头名称时必须重新输入密钥。原环境变量方式保留兼容入口。

首次启用直接输入密钥前，执行 `uv run alembic upgrade head` 并重启后端及独立 Worker。新增迁移为 `0021_tool_credentials`。密钥使用共享密钥库加密，独立保存在 `tool_credentials`，配置、版本、Agent 快照与审计只含引用和请求头名称。安装主密钥沿用 `model_credential_key_file`（默认 `backend/data/credentials/master.key`，由后端首次保存密钥时自动创建），无需为工具手动创建环境变量。API 与独立 Worker 必须使用同一持久化主密钥文件；备份数据库时同时备份该文件，已有加密记录时文件丢失会拒绝保存和调用，不能重新生成替代。

密钥配置随工具版本固定：保存草稿不会改变正在使用的发布版本，修改后需发布工具版本并重新发布 Agent。旧版本继续使用对应旧密钥；如需立即阻断调用，可停用工具或在上游撤销旧密钥。

## 接口分组

基础路径 `/api/v1`；详细字段和校验规则在后端 `/docs`。

| 路径 | 操作 |
|---|---|
| `/me`、`/policy` | 当前角色及运行策略 |
| `/agents`、`/agents/{id}` | 列表、创建、修改草稿 |
| `/agents/{id}/versions`、`/publish`、`/rollback` | 版本查询、发布、回滚 |
| `/tools`、`/tools/{id}` | 注册、列表、启停 |
| `/knowledge-bases`、`/{id}/documents` | 知识库及文档维护 |
| `/documents/{id}`、`/knowledge/search` | 文档启停、检索 |
| `/conversations`、`/{id}`、`/{id}/messages` | 会话及客户消息 |
| `/conversations/{id}/human-replies`、`/handoffs` | 人工回复、转接 |
| `/handoffs`、`/handoffs/{id}/transition` | 队列、接管/恢复/关闭 |
| `/actions/{id}/decision`、`/tickets`、`/tickets/{id}` | 确认动作、工单维护 |
| `/channels`、`/channels/{id}` | 渠道配置、启停 |
| `/ingress/channels/{id}/messages`、`/replies` | 渠道消息及回复 |
| `/observability/summary`、`/runs` | 统计及运行列表 |
| `/audit` | 管理员操作审计 |
| `/agents/{id}/evaluation-cases`、`/evaluation-reports`、`/evaluate` | 用例、报告、运行评测 |

表中相邻缩写路径继承同一资源前缀，例如发布为 `/agents/{id}/publish`，统计运行列表为 `/observability/runs`。原 `/runs` Runtime 接口仍可用，会遵守绑定 Agent 及人工接管状态。

渠道是可信服务端之间的接口：创建时返回一次明文 token，数据库只保存摘要。调用方传 `X-Channel-Token`，提交：

```json
{"message_id":"upstream-unique-id","session_id":"customer-session","text":"什么时候发货？"}
```

同一渠道相同 message_id 和内容返回已处理结果；同一 ID 不同内容返回 409。通过 `/replies?session_id=customer-session&after=0` 拉取回复并推进游标。渠道令牌不应发给终端客户；上游服务负责认证客户并保证 session_id 所有权。当前没有主动推送到第三方平台。

## 验证和当前边界

```powershell
cd C:\code\ai-project\backend
uv run ruff check .
# 集成测试只对专用测试库运行，先对该库执行 alembic upgrade head
$env:TEST_DATABASE_URL='postgresql+asyncpg://agent:password@127.0.0.1:5432/agent_platform_test'
uv run pytest -q
cd C:\code\ai-project\frontend
npm run build
# 启动使用专用测试库、demo 模型、无令牌的 API 与前端后执行；会创建测试业务数据
npx playwright install chromium
npm run test:e2e
```

可通过 `E2E_CHROME_PATH` 使用已安装 Chrome，通过 `E2E_BASE_URL` 指定测试站点。没有 `TEST_DATABASE_URL` 时 PostgreSQL 测试会跳过。

当前为完整基础流程的 MVP，扩展边界明确如下：

- 采用服务令牌角色和部署级租户，未实现员工账号、SSO、细粒度组织授权及渠道客户身份系统。
- 知识库为 PostgreSQL 关键词检索，支持中英文；尚无向量召回、重排、PDF/OCR、异步大文件管线。
- 工单写入本地 PostgreSQL；外部 CRM、订单、退款等写入接口仍需专用适配器。普通 API 调用不需要沙箱，未来开放代码执行再独立设计沙箱。
- 渠道为通用协议，微信/企业微信等原生签名验证、回调和主动回复适配尚未接入。
- SSE 是阶段事件，不是逐 Token 输出。Worker 失联会把运行标为失败，不支持 LangGraph 节点检查点续跑。
- 评测最多 20 个用例、单次总预算 60 秒，当前通过请求执行，进程内限流；尚无分布式评测队列和模型效果基准。
- 列表采用有限条数，管理台暂无全量搜索/分页、数据保留清理及附件对象存储。耗时与并发仍需针对实际模型和业务 API 压测；本次验证不代表生产容量承诺。
