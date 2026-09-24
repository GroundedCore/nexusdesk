# 异步客服 Runtime（第一版）

## 已实现范围

- LangGraph 编译一次、按 Run 隔离状态：model → tools → model → END。
- 可注入模型协议，提供明确标记的 DemoModel 和 ChatOpenAI 异步适配器。
- 服务端固定配置 GET 工具，参数经过 JSON Schema 校验；URL、HTTP 方法和鉴权头不能由模型改写。
- 同一轮的独立只读工具并行执行，共享进程内工具并发上限。
- PostgreSQL 持久化会话摘要式历史、任务队列、运行终态和阶段事件。
- API 提交后立即返回 202，Worker 异步执行；支持查询、取消、SSE 与断线重放。
- 模型轮次、工具次数、模型超时、运行超时、队列容量、排队期限及工具响应体大小限制。

## PostgreSQL 数据与并发

| 表 | 数据 |
|---|---|
| runtime_conversations | 租户、外部会话标识、最近 N 轮用户消息和最终回复 |
| runtime_runs | 输入、配置指纹、运行状态、Worker 所有权、租约、输出、错误码 |
| runtime_events | 按 Run 单调递增的事件序号、类型、数据和时间 |

API 使用短事务与数据库 advisory lock 控制全局准入数量，部分唯一索引保证一个会话最多一个 queued/running 任务。会话繁忙返回 409；容量不足返回 429。

Worker 使用 `FOR UPDATE SKIP LOCKED` 领取任务。不同 Worker 可以同时消费不同任务；每个 Worker 的并发由 `AGENT_WORKER_CONCURRENCY` 控制，部署多个 Worker 时总执行并发会累加。`QUEUE_CAPACITY` 和 `TENANT_CAPACITY` 统计 queued + running，不等于模型 RPM/TPM 配额。

模型和工具等待期间不持有数据库事务。心跳每秒续租并检查取消标记，默认租约 30 秒。租约失效后，旧 Worker 不能再提交成功结果；清理器将任务标为 worker_lost。任务不会自动重新执行。已排队任务可在 Worker 重启后继续领取。

运行配置包含非敏感指纹。API 与 Worker 应使用相同的模型、工具、提示词和预算配置；部署变更后，指纹不一致的排队任务返回 configuration_changed，避免静默换配置执行。

## 状态与事件

状态：queued → running → completed / failed / cancelled。

取消 queued 任务立即生效；取消 running 任务先记录 cancel_requested，Worker 通常在下一个心跳检测时取消本地 await。无法保证撤销外部服务已接收的请求，也不保证模型提供方停止计费。

事件：run.queued、run.started、model.started、model.completed、tool.started、tool.completed、run.cancel_requested、run.completed、run.failed、run.cancelled。

当前 SSE 是**阶段事件流**，最终回复位于 run.completed.data.output；不是逐 Token 输出。普通事件不暴露工具参数、原始结果或密钥。最终回复和会话历史仍属于客户数据，需要配置保留期限与访问控制。

SSE 从 PostgreSQL 每 0.5 秒读取一次，包含 15 秒心跳，支持 `Last-Event-ID` 或 `after`。终态和最终事件在同一事务提交。高连接量时可将唤醒机制升级为 LISTEN/NOTIFY 或消息总线，数据库事件仍作为重放依据。

## 启动（PowerShell）

```powershell
cd C:\code\ai-project
docker compose up -d postgres
cd backend
uv sync
Copy-Item .env.example .env
uv run alembic upgrade head
uv run uvicorn agent_platform.apps.api.main:app --host 127.0.0.1 --port 8000
```

没有 Docker 时，使用已有 PostgreSQL 16+，创建专用数据库并在 `.env` 中配置 `AGENT_DATABASE_URL`。示例账号和密码仅用于本地开发。不要覆盖已有 `.env`。

演示业务 API 在另一个终端启动：

```powershell
cd C:\code\ai-project\backend
uv run uvicorn examples.mock_business:app --host 127.0.0.1 --port 8010
```

默认 `.env.example` 配置 DemoModel 和 `examples/tools.demo.json`。发送“查询演示”会通过真实 HTTP 请求查询演示服务，不调用收费模型。演示数据明确标记为 demo。

分离 API 和 Worker：API 设置 `AGENT_EMBEDDED_WORKER=false`，另开进程执行：

```powershell
uv run python -m agent_platform.apps.worker
```

Worker 使用与 API 相同的 `.env`。数据库迁移是显式发布步骤，进程启动不自动建表。

## 接入真实模型和 API

在 `.env` 中设置 `AGENT_MODEL_BACKEND=openai`、明确的 `AGENT_MODEL_NAME` 和 `AGENT_MODEL_API_KEY`。默认使用 Responses API；兼容 Chat Completions 的服务可配置 `AGENT_MODEL_BASE_URL` 并将 `AGENT_MODEL_USE_RESPONSES_API=false`。需选择支持工具调用的模型并自行验证提供方兼容性。本次测试不需要真实密钥，也不验证真实模型质量。

复制 tools.demo.json 为自己的工具配置，替换固定 URL 与参数 Schema。鉴权示例：

```json
"headers_from_env": {"Authorization": "ORDER_SERVICE_AUTH"}
```

环境变量 ORDER_SERVICE_AUTH 保存完整请求头值，例如 Bearer 加业务令牌。配置文件只引用变量名。工具不跟随重定向、不自动重试；非 2xx、非 JSON、超时、过大响应均转成结构化错误供模型观察。网络层 trust_env=false，不隐式使用环境代理。

只注册真正只读的 GET 接口；HTTP 方法本身不能证明接口没有副作用。内部地址由管理员配置白名单式固定端点，不能开放用户自助注册 URL。业务服务仍需依据服务端身份实施对象级权限检查。

## 第一版边界

- 持久化的是运行与事件，不是 LangGraph 节点检查点；进程崩溃后不承诺从中间节点继续。平台已实现运行之外的待确认工单流程与人工接管；LangGraph 节点级审批暂停恢复仍未实现。
- 暂未开放 POST/PATCH/DELETE、任意代码和沙箱；写入工具需要先补充授权确认、幂等及结果核查。
- 当前提供固定部署租户和可选 Bearer 服务令牌。生产模式强制令牌，但它不等于完整用户认证与多租户 RBAC；接入业务前应从可信认证层派生租户与用户身份。
- 模型轮数、输出 Token 上限和时间预算已实现；提供方 RPM/TPM、费用预算及自适应限流尚未实现。
- 最近 N 轮只保存用户消息与最终回复，不保留完整工具结果作为后续上下文；原始运行步骤仅在本轮内存中存在。
- 持久记录需要后续添加清理策略、加密和运营审计。当前未实现幂等提交键，网络重试可能在前一运行完成后重复提交。

## 测试

```powershell
uv run pytest -q
uv run ruff check .
```

PostgreSQL 集成测试必须使用已迁移的专用测试库：

```powershell
$env:AGENT_DATABASE_URL = 'postgresql+asyncpg://user:password@127.0.0.1:5432/agent_runtime_test'
uv run alembic upgrade head
$env:TEST_DATABASE_URL = $env:AGENT_DATABASE_URL
uv run pytest -q
```

未配置 TEST_DATABASE_URL 时数据库测试显式跳过。测试只清理自身生成的 test-UUID 租户数据。
