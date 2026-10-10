# 智能体回答流式输出（LISTEN/NOTIFY）

## 目标与非目标

**目标**：让智能体回答在内部控制台逐字出现（含思维链 `reasoning_content`），而不是"转圈 → 整段出现"。

**手段约束**：使用 Postgres `LISTEN/NOTIFY`，不引入 Redis，不新增数据库迁移。

**非目标**：

- 不改对外的最终答案契约（`run.completed` 仍携带完整 `view(final)`）
- 不削弱任何现有校验（工具调用合法性、空响应、64000 字上限、轮次/工具上限）
- 不动 LangGraph 图结构
- 公开 API 的逐字流式（见「按面呈现」）

---

## 为什么现在不是逐字输出

模型网关其实已经支持流式（`ModelGateway.invoke(..., emit=)`，目前只有 `/model-gateway/stream` 路由在用），但运行时链路断了三处：

| 断点 | 位置 | 现状 |
|---|---|---|
| ① 运行时拿不到 delta | `model_gateway/gateway.py` `GatewayChatModel` | 只有 `ainvoke`（阻塞），没有流式方法 |
| ② 事件写入重 | `agent_runtime/repository.py` `append` | 每条事件 `SELECT … FOR UPDATE` 锁 run 行 |
| ③ SSE 靠轮询 | `apps/api/runtime_routes.py` | 固定 `sleep(0.5)` 查库 |

---

## 核心设计决策

### 决策 A：delta 是瞬态的，不入库

数据分三类，只有一类要持久：

| 数据 | 性质 | 去向 |
|---|---|---|
| 运行生命周期 / 工具进度 | 必须可重放 | `runtime_events`（照旧） |
| 最终答案 | 必须持久 | `runtime_runs.output`，`finish()` 写入（照旧） |
| 逐字增量（含思维链） | 纯实时观感 | `NOTIFY` 直传，不落一行 |

收益：0 次新增数据库写入、0 次新增行锁，不需要合并缓冲、不需要新事件类型、不需要客户端分片重组。

### 决策 B：NOTIFY 就是消息队列

生产拓扑 worker 与 API 是独立进程，纯内存不通用。`NOTIFY` 天然跨进程、事务性、零新基础设施。

### 决策 C：ReAct 循环必须按轮流式

流式发生在 `_model` 节点内部，节点照旧返回完整 state，图语义不变。但只有**最后一轮**（无工具调用的轮次）的文字才是答案（`engine.py` 中 `output if not reply.tool_calls else ""`）。

客户端渲染不变式：

| 事件 | 动作 |
|---|---|
| `model.started{round:n}` | 重置该轮缓冲 |
| `model.delta{round:n}` | 追加 |
| `model.completed{round:n, tool_calls:k}` | `k>0` → 该轮是"过程"；`k==0` → 该轮是"答案" |

只转发 `content` delta；`tool_calls` 分片（残缺 JSON 参数）不转发。

### 决策 D：保证边界

> 答案是**有保证的**（持久 + 终态事件携带完整内容）；逐字增量是**尽力而为的**（瞬态，可丢）。

客户端断线重连、通知丢失、API 进程重启，最坏情况退化成今天的行为（转圈 → 整段出现），而非一致性错误。

---

## 数据流

```
worker 进程
  engine._model
    ├─ content / reasoning delta ─→ pg_notify(chan, {"k":"delta","round":n,"text":…,"reasoning":…})
    └─ 轮结束 ─→ emit("model.completed") ─→ _event 入库                    ← 照旧
  运行结束 ────→ finish(output=完整答案) ─→ runtime_runs 入库               ← 照旧

api 进程（每进程一条专用 LISTEN 连接，可监听多通道）
  收到 "delta"   → 直接 yield（不碰数据库，不动游标）
  收到 "durable" → 从 cursor 读 runtime_events 并 yield（重放语义不变）
  15s 安全网      → 兜底轮询（防漏通知）
```

两种通知载荷，靠 `k` 字段区分：

| 形状 | 含义 | 客户端 |
|---|---|---|
| `{"k":"delta","round":n,"text":"…","reasoning":"…"}` | 瞬态增量 | 渲染，不动游标 |
| `{"k":"durable","seq":n,"type":"…"}` | 持久事件指针 | 从 `cursor` 读库渲染，推进游标 |

---

## 按面呈现（独白与思维链）

| 面 | 端点 | 过程轮（中间轮文字） | 思维链 | 最终答案 |
|---|---|---|---|---|
| 内部调试（`Trace`、可观测性、会话工作台） | 内部 SSE `/api/v1/runs/{id}/events` | 转发，前端标记为"过程" | 流式转发 | 流式 |
| 公开 API（集成方、`EmbedChat`、集成调试台） | 公开 SSE `/openapi/v1/.../messages?stream=true` | 转发，但客户端按下方规则丢弃 | **不发送** | 流式，且 `run.completed` 带完整 `view(final)` |

**两个面都逐字，也都只展示最后一轮答复。** 差别只有思维链：它是内部调试信息，公开 API 不暴露。

### 判定"哪一轮是答案"

多轮 ReAct 下，某轮流式输出时**无法预知**它末尾会不会补 tool_calls（`engine.py` 里带工具调用的轮次其 `output` 会被丢弃）。所以两个面都按同一条规则判定：

> **答案是最后一轮 `tool_calls == 0` 的轮次。** 以工具调用结束的轮次是模型的"过程话术"，客户端应丢弃其文本。

公开 SSE 因此转发 `model.delta{round, text}`，并转发带 `round` 与 `tool_calls` 的 `model.completed`，让客户端可以在该轮结束时丢弃它。`run.completed` 仍携带完整 `view(final)` 作为权威结果。

**已知代价**：某轮在结束前会被当作候选答案流式显示，若该轮最终以工具调用结束，客户端的文本会被清掉并换成真正的答案。这是在"要逐字"与"要零泄漏"之间必须二选一时选前者——结构性零泄漏只能靠缓冲整轮、从而丧失逐字效果。

---

## 具体改动

| # | 位置 | 改动 |
|---|---|---|
| 1 | `gateway.py` | `GatewayChatModel` 增加流式调用方法：以 `on_delta` 回调驱动 `gateway.invoke(..., emit=)`，返回完整 `AIMessage`；捕获 `model_stream_not_supported` 回退 `ainvoke` |
| 2 | `adapters.py` | `stream_chat_http` 把 `reasoning_content` 也 emit（当前只内部累积） |
| 3 | `engine.py` | `_model` 用流式方法 + `on_delta` 转发 `content` / `reasoning`；所有现有校验在完整回复上照跑 |
| 4 | worker 路径 | delta 直发 `pg_notify`（绕过 `_event`）；其余事件照走 `_event` |
| 5 | `repository.py` `_event` | 加 `SELECT pg_notify(channel, pointer)`，所有持久事件自动带通知 |
| 6 | 新增监听组件 | 每进程一条专用连接（asyncpg `add_listener`）、自动重连、`LISTEN`/`UNLISTEN` 生命周期 |
| 7 | `runtime_routes.py` | 内部 SSE 循环改事件驱动 + 15s 安全网（`wait_for` + 兜底轮询） |
| 8 | `open_platform/routes.py` | 公开 SSE 订阅同一通知，转发 `model.delta`（**只带可见文本，不带思维链**），并给 `model.started`/`model.completed` 补 `round`/`tool_calls` |
| 9 | 前端 `ui.tsx` | `useRunStream`：delta 累积到 ref、由 20ms 定时器定速放出，避免批处理把逐字压成大块跳跃；`answerRound` 选出最后一轮非工具调用轮 |
| 10 | 前端消费方 | 试聊、会话工作台、集成调试台、`EmbedChat` 均按同一规则只渲染最后一轮 |

---

## 不需要改的东西

- **数据库迁移**：本方案不新增事件类型、不改 `runtime_events` 表结构
- SSE 重连协议：`after` 游标 + `Last-Event-ID` 原样可用
- 公开 API 契约：`run.completed` 仍带完整 `view(final)`
- 最终答案：`finish()` 写入 `runtime_runs.output` 不变

---

## 风险与约束

| 风险 | 处理 |
|---|---|
| delta 走 NOTIFY 载荷，8000 字节上限 | 单块文本远小于此；限制块大小，不把整段答案塞进一条通知 |
| 流式是条件性的（非 `chat` / `gateway_http` → `model_stream_not_supported`） | `ainvoke` 回退，功能只增不减 |
| PgBouncer 事务池会破坏 LISTEN | 目前直连没问题；写入部署约束 |
| 工具执行期间无增量 | 前端显示 `tool.started`，不伪装成打字 |
| 嵌入 Worker 模式 LISTEN 是纯开销 | 特性开关 `AGENT_STREAM_MODEL_DELTAS`（默认开，可显式关闭） |
| delta 即时投递 vs `_event` 提交时才投递 | 极小概率乱序；客户端按轮缓冲可容忍 |

---

## 分期与验收

| 阶段 | 内容 | 验收 |
|---|---|---|
| P1 | 流式调用方法 + engine 消费 + 回退 | demo 后端可测（一次性发完整结果，验证接口通） |
| P1b | `reasoning_content` 也 emit | 真实 provider 下思维链逐字 |
| P2 | delta 走 `pg_notify`（不入库） | `runtime_events` 行数与今天完全一致 |
| P3 | 监听端 + SSE 端点改造 | 延迟 ~50ms；断开监听仍正确收敛 |
| P4 | 前端按轮渲染 | 三轮 ReAct 不把过程独白当答案 |

**特性开关**：`AGENT_STREAM_MODEL_DELTAS`，默认开；设置为 `false` 可一键回到非流式行为。
