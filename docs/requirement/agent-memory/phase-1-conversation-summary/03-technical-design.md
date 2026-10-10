# Phase 1 · 会话摘要记忆 — 技术落地文档

## 改动总览

| 层 | 文件 | 改动 |
|---|---|---|
| 迁移 | `backend/migrations/versions/0030_conversation_summary.py` | 新增：`runtime_conversations.summary` 列；`memory_tasks` 任务表（含 `owner` + `lease_expires_at` 租约列） |
| 新模块 | `backend/src/agent_platform/modules/memory/__init__.py` | 空包占位（后续阶段扩展） |
| 新模块 | `backend/src/agent_platform/modules/memory/summary.py` | 摘要生成服务 |
| 新模块 | `backend/src/agent_platform/modules/memory/worker.py` | 摘要任务认领循环（SKIP LOCKED + 120s 租约 + 心跳续约 + reaper） |
| 新模块 | `backend/src/agent_platform/modules/memory/bootstrap.py` | 两种部署形态的装配：API 内嵌 / 独立进程 |
| 入口 | `backend/src/agent_platform/apps/worker/memory.py` | 生产独立 memory-worker 进程入口 |
| 入口 | `backend/src/agent_platform/apps/api/main.py` | `AGENT_EMBEDDED_WORKER=true` 时 lifespan 装配内嵌认领循环 |
| 部署 | `deploy/production/compose.yaml` | 新增 `memory-worker` 常驻服务（复用后端镜像） |
| 运行时 | `modules/agent_runtime/engine.py` | 摘要注入消息序列 |
| 运行时 | `modules/agent_runtime/repository.py` | `finish()` 投递摘要任务；`claim()` 读出 summary |
| 接管 | `modules/human_handoff/service.py` | handoff summary 拼接逻辑 |
| 配置 | `backend/src/agent_platform/settings.py` | 新增 3 个配置项 |
| Agent 配置 | `modules/agent_config/service.py` | `AgentConfig` 增加 `summary_enabled` |

## 数据模型

### 迁移 `0030_conversation_summary.py`

```sql
-- 会话滚动摘要
ALTER TABLE runtime_conversations ADD COLUMN summary TEXT;

-- 通用记忆任务表（Phase 2 的抽取任务复用此表，kind 区分）
CREATE TABLE memory_tasks (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL,
    kind         VARCHAR(30) NOT NULL,          -- 'conversation_summary'（Phase 2 增加 'memory_extract'）
    status       VARCHAR(20) NOT NULL DEFAULT 'pending',  -- pending/running/done/failed
    payload      JSONB NOT NULL,                -- {conversation_id, dropped_messages, old_summary}
    attempts     INT NOT NULL DEFAULT 0,
    max_attempts INT NOT NULL DEFAULT 2,
    error        TEXT,
    run_after    TIMESTAMPTZ NOT NULL DEFAULT now(),
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX memory_tasks_claim ON memory_tasks (status, run_after) WHERE status = 'pending';
```

设计要点：
- `memory_tasks` 仿照 `runtime_runs` / `knowledge_tasks` 的"任务表 + claim"模式，Phase 2/3/4 的所有异步记忆任务复用此表，避免重复造队列。
- 摘要直接存会话行上，继承 `tenant_id` 隔离与行级生命周期（会话删除即摘要删除）。

## 核心流程

### 1. 任务投递（`RunRepository.finish()` 内）

```
finish() 写回截断后 history 时：
  dropped = 本次被 history_turns 截掉的消息
  if dropped 非空 且 agent.summary_enabled 且 settings.summary_enabled:
      INSERT memory_tasks(kind='conversation_summary',
          payload={conversation_id, dropped_messages: dropped[-40:], old_summary})
```

- `dropped_messages` 截取最近 40 条（`summary_max_input_messages`），超出部分依赖上一次摘要已覆盖；
- 同一会话已有 pending/running 的摘要任务时**合并**：把新 dropped 追加到已有任务 payload，避免任务堆积（用 `SELECT ... FOR UPDATE` 在同事务内判断）。

### 2. 摘要生成（`memory/summary.py`）

```python
class SummaryService:
    def generate(self, task) -> None:
        # 1. 组装 prompt：SUMMARY_PROMPT + old_summary + dropped_messages
        # 2. 调用模型网关 Chat profile（复用该会话 Agent 绑定的 profile，
        #    无绑定时回退租户默认 profile；demo 环境走 mock 模型）
        # 3. 输出校验：非空、≤ 500 字（超出则取尾部并截断）
        # 4. UPDATE runtime_conversations SET summary = ... WHERE id = ... 
        #    （带 revision 检查，会话已关闭/删除则跳过）
```

摘要 prompt 要点（模板常量放 `summary.py`）：
- 指令：将旧摘要与新对话片段合并为一份不超过 500 字的滚动摘要；
- 必保留字段清单：客户身份/联系方式、诉求、关键事实与约束、已达成结论、待办与承诺；
- 明确丢弃：寒暄、重复内容、工具调用细节；
- 输出纯文本，不加任何前缀标记。

### 3. Worker（`memory/worker.py`）

```python
class MemoryWorker:
    # 复用 RuntimeWorker 的认领循环模式（含知识库 Worker 的租约先例）：
    # UPDATE memory_tasks SET status='running', owner=:owner,
    #   lease_expires_at=now()+120s ... WHERE id = (
    #   SELECT id FROM memory_tasks WHERE status='pending' AND run_after<=now()
    #   ORDER BY created_at LIMIT 1 FOR UPDATE SKIP LOCKED) RETURNING *
    # 执行期间心跳续约；每次 claim 前置 reaper 将租约过期的 running 重置 pending。
```

- 部署形态：分两种形态，与 Runtime Worker 自身的部署模式对齐——
  - 开发 / quickstart：作为 API 进程内的并发循环启动（`AGENT_EMBEDDED_WORKER=true` 时随 API lifespan 装配），两容器承诺不变；
  - 生产：独立 `memory-worker` 常驻容器（复用后端镜像、独立进程运行认领循环，不依附 Runtime Worker / API 进程），与 runtime-worker 解耦、可独立伸缩；并发度 = memory-worker 副本数（`FOR UPDATE SKIP LOCKED` 保证多副本认领安全，伸缩模型同 runtime-worker）。
- 失败处理：`attempts < max_attempts` 则 `run_after = now() + 指数退避` 重新 pending，否则置 `failed` 并记审计事件。
- 优雅停机：跟随所属进程（quickstart：API 进程；生产：memory-worker 容器）的 shutdown 信号，running 任务重置为 pending。

> 修订（2026-10-10）：部署形态由"Runtime Worker 进程内并发循环、不新增容器角色"修订为"quickstart 内嵌 API 进程 / 生产独立 memory-worker 容器"。compose 服务与独立入口（`apps/worker/memory.py`）已随本次代码对齐落地；多副本生产的 lease/reaper（claim 写 owner + `lease_expires_at=now()+120s`、执行期间心跳续约、claim 前置 reaper 重置过期 running）按 Phase 4 技术设计 §4 提前一并实施（承接测试报告 R3 / 验收报告 L3，先例为知识库 Worker 的 SKIP LOCKED + 120s 租约 + reap 模式）。

### 4. 注入（`engine.py`）

现状消息序列：`SystemMessage(system_prompt)` + 截断 history + HumanMessage。

改为：

```
SystemMessage(system_prompt)
+ SystemMessage(f"【此前对话摘要】\n{summary[:800 tokens]}")   # summary 非空时
+ 截断 history
+ HumanMessage
```

- `RunRepository.claim()` 查询时一并读出 `summary`，经 `runtime_runs` 行透传给 Worker → engine；
- token 截断用简易估算（字符数 / 2 近似），不引入 tokenizer 依赖。

### 5. 接管摘要（`human_handoff/service.py`）

```python
def _build_handoff_summary(conversation, recent_messages):
    parts = []
    if conversation["summary"]:
        parts.append("【对话摘要】" + conversation["summary"])
    parts.append("【最近消息】\n" + 现有拼接逻辑)
    return "\n\n".join(parts)
```

### 6. 配置

`settings.py` 新增：

```python
summary_enabled: bool = True
summary_max_input_messages: int = 40
summary_max_output_chars: int = 1000   # 500 中文字约 1000 字符上限
```

`AgentConfig` 新增 `summary_enabled: bool = True`，随 `agents.draft` JSONB 走既有草稿/发布/快照链路，无需改表。

## 模型调用路径

摘要调用走模型网关而非直连，复用：`GatewayChatModel`、用量落库（`gateway_calls`）、mock 模型（quickstart 演示环境零成本）。profile 选择顺序：

1. 会话 Agent 快照绑定的 `model_profile_id/version`；
2. 租户级默认 Chat profile（若无绑定）；
3. 无可用 profile → 任务标记 failed（`error='no_chat_profile'`），功能降级。

## 测试方案

| 层 | 用例 |
|---|---|
| 单测 `test_summary.py` | prompt 组装、输出校验/截断、任务合并逻辑、失败重试与退避 |
| 单测 engine | 有/无/超长摘要三种注入形态；`summary_enabled=False` 不注入 |
| 集成 | 模拟 25 轮对话（mock 模型），断言：截断发生后产生任务 → worker 执行 → 会话 summary 更新 → 后续 run 注入摘要 |
| 集成 handoff | 转人工后 handoff.summary 含摘要段落 |
| 回归 | 现有 conversation / runtime / handoff 测试全绿（空 summary 时行为不变） |

## 上线与回滚

- 迁移只加列加表，可在线执行；
- 全局 `summary_enabled=false` 即整体关闭（行为=现状）；
- 回滚仅需关开关，无需回滚迁移。
