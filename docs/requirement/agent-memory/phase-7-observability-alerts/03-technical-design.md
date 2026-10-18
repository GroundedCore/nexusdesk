# Phase 7 · 生产观测与告警 — 技术落地文档

## 改动总览

| 层 | 文件 | 改动 |
|---|---|---|
| 迁移 | `backend/migrations/versions/NNNN_memory_observability.py`（编号以实施时主线最新为准） | `memory_tasks` 加 `created_at`、`duration_ms` 列（D1） |
| Worker | `modules/memory/worker.py`（:310 行；maintenance :251-259、rule_scan :229-236、embedding 重试 :260-273、`_finish`） | 任务耗时记录；维护完成审计；embedding 重试审计；新增告警检查周期任务 |
| 新模块 | `modules/memory/alerting.py` | 告警规则求值、冷却判定、审计写入、webhook 外发 |
| 事件 | `modules/agent_runtime/platform_tools.py`（:195 `memory_search.completed`） | 事件 data 扩展 `mode`（vector/text/keyword_fallback）与 `returned` |
| 观测 | `modules/observability/service.py`（`memory_summary` :121-225） | 扩展十类指标聚合（或拆 `memory_health()` 保持函数可读） |
| API | `apps/api/platform_routes.py`（:665-669） | 响应体扩展；新增 `GET /observability/memory/alerts` |
| 前端 | `frontend/src/features/observability/ObservabilityPage.tsx`（MemoryMetrics :13） | 指标分组卡片 + 告警列表 |

## 数据模型（迁移 `NNNN_memory_observability.py`）

```sql
ALTER TABLE memory_tasks ADD COLUMN created_at TIMESTAMPTZ NOT NULL DEFAULT now();
ALTER TABLE memory_tasks ADD COLUMN duration_ms INTEGER;   -- done/failed 时由 worker 写入
```

**决策 D1（补任务时间戳）**：现状 `memory_tasks`（0030:19-39）无创建时间与耗时字段，"最老 pending 年龄"只能用 `run_after` 近似、"按 kind 执行耗时"完全无法计算。补两列：存量行 `created_at` 回填 `run_after`（近似），`duration_ms` 留 NULL（聚合时排除）。worker claim 时记录起始时间，`_finish` 写 `duration_ms`。

**决策 D2（告警不建新表）**：告警记录复用 `audit_records`，action `memory.alert.<rule>`，与平台审计风格一致（同 Phase 4 R17 决策）。冷却判定 = 查同窗内同 action 记录是否存在。观测页告警列表 = audit 按 `memory.alert.%` 过滤查询。不维护告警状态机（事件制，恢复靠指标卡变绿呈现）。

## 核心组件

### 1. 指标采集补点

| 采集点 | 改动 |
|---|---|
| 检索模式 | `memory_search.completed` 事件 data 由 `{hits}` 扩为 `{mode, hits, returned}`：mode 取 store 实现类型（PgVectorStore→vector / TsVectorStore→text），单次查询向量生成失败的降级（store.py:213-219）记 `keyword_fallback`；`returned` 为实际返回条数 |
| 维护完成 | worker 每轮 maintenance / rule_scan / embedding 重试结束写审计 `memory.maintenance.completed`，details `{job, result, stats}`（stats 如 expired 条数、重试成功数）；失败写 `memory.maintenance.failed` details `{job, error}` |
| embedding 重试 | 重试审计即上表 job=`embedding_retry`，stats `{scanned, succeeded, failed}`；当前积压直查 `memory_items.embedding_failed=true` 计数 |
| 任务耗时 | D1 的 duration_ms，按 kind 聚合 avg/P95 |

### 2. 观测聚合（`observability/service.py`）

`memory_summary()` 保持既有返回结构不破坏（Phase 4 前端在用），新增字段或拆 `memory_health()`：

```python
{
  ...既有字段...,
  "tasks": {"pending": n, "running": n, "failed_15m": n,
            "oldest_pending_age_minutes": x,
            "by_kind": [{"kind": "memory_extract", "avg_ms": x, "p95_ms": x, "done": n, "failed": n}]},
  "embedding": {"failed_backlog": n, "retry_scanned_7d": n, "retry_succeeded_7d": n},
  "search_modes": {"vector": n, "text": n, "keyword_fallback": n, "fallback_rate": f},
  "search_quality": {"calls": n, "empty": n, "empty_rate": f, "avg_returned": f},
  "maintenance": {"ttl_last_at": t, "decay_last_at": t, "rule_scan_last_at": t,
                  "embedding_retry_last_at": t, "overdue": ["rule_scan"]},
  "agents": [{"agent_id": ..., "customer_runs": n, "injected_runs": n,
              "inject_rate": f, "items_written": n, "tasks_failed": n}],
  "injection": {"avg_items": f, "by_section": {"flag": n, "profile": n, ...}},   # 见 D3
  "pending_quality": {"confirmed_7d": n, "rejected_7d": n, "confirm_rate": f}
}
```

- 全部聚合限 `window_hours` 时间窗（默认 24，前端可切 7d）；行表（memory_tasks/memory_items/audit_records/runtime_events）按既有索引 + 时间窗过滤，explain 验证无全表扫描；
- Agent 维度：`runtime_runs.config->>'agent_id'` 分组（config JSONB 含 agent 快照）；写入量按审计 `memory.extract.applied` details 聚合（该审计记录于抽取执行时，需确认 details 含 agent 上下文——若无，则按 run 关联回填，实施时以对齐实际 schema 为准，允许降级为"按租户总量 + run 维度注入率"）；
- **决策 D3（注入明细降级）**：`memory_injection_log` 存在（Phase 6 已上）时从明细表聚合 avg_items 与 section 分布；不存在时按 `memory.injected` 事件 data 计数聚合——检测方式：`to_regclass('memory_injection_log')`，两口径在同一字段返回，前端无感。

### 3. 告警检查（`memory/alerting.py` + worker 周期任务）

```python
RULES = [
    Rule("task_failed",        check=lambda ctx: ctx.failed_tasks_15m > 0),
    Rule("pending_task_age",   check=lambda ctx: ctx.oldest_pending_age > timedelta(minutes=60)),
    Rule("embedding_growing",  check=lambda ctx: ctx.embedding_backlog_rising_2_cycles),
    Rule("vector_fallback",    check=lambda ctx: ctx.search_calls_24h >= 20 and ctx.fallback_rate > 0.2),
    Rule("maintenance_silent", check=lambda ctx: ctx.last_maintenance_at < now() - timedelta(hours=24)),
    Rule("rule_scan_missed",   check=lambda ctx: rule_scan_enabled() and ctx.last_rule_scan_at < now() - 2 * rule_scan_interval()),
    Rule("pending_backlog",    check=lambda ctx: ctx.pending_items > 500),
    Rule("worker_heartbeat",   check=lambda ctx: standalone_mode() and ctx.last_claim_at < now() - timedelta(minutes=30)),
]

def alert_scan(ctx):
    for rule in RULES:
        if not rule.check(ctx):
            continue
        if alert_exists(action=f"memory.alert.{rule.name}", within=cooldown):   # 冷却
            continue
        audit(f"memory.alert.{rule.name}", details={value, threshold, window})  # 不含敏感数据
        maybe_send_webhook(rule, details)                                      # FR-3，超时 5s，失败仅记日志
```

- **embedding 持续增长判定（R3）**：检查任务在内存中维护最近两次 backlog 值（worker 进程内状态），连续两周期上升且 > 0 触发；worker 重启则重新采样（宁漏不误）；
- **worker 心跳（R8）**：仅在独立 memory-worker 形态判定（quickstart 内嵌形态 API 活即 worker 活，不判定）；心跳 = claim 循环每轮写一条轻量记录（复用 `memory.maintenance.completed` job=`heartbeat`，低频每 5 分钟）；
- **检查周期**：`memory_alert_scan_interval_minutes`（默认 15，0 停用），挂在 MemoryWorker 空闲周期，与 rule_scan 同模式；单轮 ≤ 5s，全部查询限时间窗；
- **R6 与 R5 的区分**：R6 只看规则扫描（受 `memory_rule_scan_interval_minutes=0` 停用时豁免），R5 看维护三件套任一。

### 4. API 与前端

- `GET /observability/memory` 响应扩展（向后兼容，纯新增字段）；
- `GET /observability/memory/alerts?window_hours=24`：audit `memory.alert.%` 倒序列表（规则、当前值/阈值、时间）；
- `MemoryMetrics` 组件分组重排：任务健康 / embedding / 检索 / 维护 / Agent 维度 / 注入 / 质量；维护超期与告警卡片红色强调；新增"记忆告警"列表卡（轮询刷新，复用既有轮询模式）。

## 配置（settings.py 增量）

```python
memory_alert_enabled: bool = True
memory_alert_scan_interval_minutes: int = 15
memory_alert_cooldown_minutes: int = 60
memory_alert_pending_task_max_age_minutes: int = 60
memory_alert_pending_backlog_threshold: int = 500
memory_alert_vector_fallback_rate: float = 0.2
memory_alert_webhook_url: str = ""
memory_alert_webhook_timeout_seconds: int = 5
```

## 测试方案

详见 `04-test-plan.md`。核心：指标聚合数值正确性（构造数据对账）、八条告警规则逐一触发与冷却、webhook 失败容错、降级口径（无 Phase 6 表）、quickstart 形态。

## 上线与回滚

- 迁移为加列（默认值回填），无锁表风险；
- `memory_alert_enabled=false` 整体关闭告警，指标照常（AC-9）；回滚直接退代码，告警审计记录保留无害；
- 指标为只读聚合，上线顺序：先迁移与采集补点（事件/审计新字段）→ 观察 1-2 天核对数值 → 再开告警（阈值可先松后紧）。
