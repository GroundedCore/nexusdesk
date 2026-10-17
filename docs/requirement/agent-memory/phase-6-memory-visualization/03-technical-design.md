# Phase 6 · 客户记忆可视化与注入可解释性 — 技术落地文档

## 改动总览

| 层 | 文件 | 改动 |
|---|---|---|
| 迁移 | `backend/migrations/versions/NNNN_memory_injection_log.py`（编号以实施时主线最新为准，沿用 Phase 4 R1 教训不占死号） | 新建 `memory_injection_log` 表 + 3 个索引 |
| 埋点 | `modules/agent_runtime/worker.py`（`_customer_memory` :110-172） | 收集当次注入的 (item_id, section, score) 到 run 上下文 |
| 落库 | `modules/agent_runtime/repository.py`（:326 `memory.injected` 事件写入处） | 事件 payload 增加 `item_ids`；同事务批量 INSERT 注入明细 |
| 服务 | `modules/memory/service.py` | 注入记录查询、条目生命周期时间线聚合、客户记忆统计 |
| API | `modules/memory/routes.py`（:15 prefix=/memory） | 注入记录、条目注入反查、客户审计时间线端点 |
| 治理联动 | `modules/memory/governance.py`（删除客户 :465 文件内）+ `modules/memory/worker.py`（maintenance :251-259） | 客户删除级联清理明细；TTL 任务同车清理超期明细 |
| 前端 | `frontend/src/features/memory/`（CustomerList / CustomerDetail / MemoryItemTable / CustomerMemoryDrawer）+ `shared/api/client.ts`（:72-93） | 三层视图；新增 InjectionLog / ItemDetail / AuditTimeline 组件 |

## 数据模型（迁移 `NNNN_memory_injection_log.py`）

```sql
CREATE TABLE memory_injection_log (
    id BIGSERIAL PRIMARY KEY,
    tenant_id UUID NOT NULL,
    run_id UUID NOT NULL,
    customer_id UUID NOT NULL,
    item_id UUID NOT NULL,
    section VARCHAR(20) NOT NULL,        -- flag | profile | preference | event
    score REAL,                          -- 注入时排序分（profile/preference 为 effective_confidence；flag 为 NULL）
    injected_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX ix_injlog_customer ON memory_injection_log (tenant_id, customer_id, injected_at DESC);
CREATE INDEX ix_injlog_item     ON memory_injection_log (tenant_id, item_id, injected_at DESC);
CREATE INDEX ix_injlog_run      ON memory_injection_log (run_id);
```

**决策 D1（不加外键）**：item 可删除/淘汰、customer 可删除，日志不以外键阻塞治理操作；清理靠治理级联与 TTL（见下）。`run_id` 关联 `runtime_runs` 同样不加外键（runtime 数据有独立生命周期）。

**决策 D2（summary 不入明细）**：滚动摘要无 item_id，且其"是否注入"由 `runtime_conversations.summary` 存在性即可判定，不落明细行；`section` 取值仅为四种 item 类型。02 文档 FR-1 的 section 枚举相应收窄（评审确认）。

**决策 D3（保留期）**：`settings.memory_injection_log_retention_days: int = 90`，每日 TTL 任务（worker maintenance :251-259）同车 `DELETE WHERE injected_at < now() - interval`。90 天与运营排障窗口匹配，避免表无限膨胀。

## 核心组件

### 1. 注入埋点与批量落库

- `_customer_memory` 渲染注入段时，各段渲染函数（`render_flag_injection` service.py:125 / `render_memory_injection` :92 / `render_event_injection` :109）当前只返回文本——调整为同时返回 `(text, injected: list[InjectedItem])`，`InjectedItem = (item_id, section, score)`；score 对 profile/preference 取查询时已算的 effective_confidence（decay.py:54 公式在注入 SQL 内联，需把分值 SELECT 出来带回），flag 为 NULL；
- 收集结果挂到 run 上下文（与 L3 overlap_ids :150-156 同一通道）；
- `repository.finish`（:326 附近）在写 `memory.injected` 事件的**同一事务**内执行单条批量 INSERT（`INSERT INTO memory_injection_log (...) SELECT * FROM unnest(...)`），无逐条写、无额外事务；
- 事件 payload 兼容：保留 `items/flags/events` 计数，新增 `item_ids: [...]`（只放 id 列表，事件体量可控）。

**热路径预算**：注入条目 ≤ 20 + flag ≤ 3，单条批量 INSERT 增加 P95 ≤ 50ms（04 测试 P6-IT-07 断言）。无注入（未绑定客户/读关闭）时零写入零开销。

### 2. 注入记录与审计查询（`memory/service.py`）

```python
def list_injections(tenant_id, customer_id, limit=50)        # run 维度列表：run_id, injected_at, 条数, 关联会话
def injection_detail(tenant_id, run_id)                       # 当次明细：item  join 当前 key/type/status（条目已删则标"已删除"）
def item_injections(tenant_id, item_id, limit=20)             # 条目反查
def customer_audit_timeline(tenant_id, customer_id, limit=100)  # audit_records WHERE action LIKE 'memory.%' 按时间倒序
def customer_memory_stats(tenant_id, customer_ids)            # 列表层统计：各类条数/pending 数/max(updated_at)，单条 GROUP BY 批量查询
def item_timeline(tenant_id, item_id)                         # 单条生命周期：审计按 resource=item_id 过滤 + 注入摘要
```

- 审计时间线**渲染自 `audit_records`**（0002_platform.py:78，action `memory.*` 点分风格），payload 本不含记忆明文（Phase 4 C4），前端只做结构化展示，不引入新的泄露面；
- 注入明细只存 id/section/score，条目内容靠 join 当前行获取；条目已物理删除时展示"条目已删除"，不试图还原内容（内容还原靠 `prev_content` 单级 + 审计计数，多级版本管理维持"不做"）。

### 3. API（`memory/routes.py` 增量，沿用 Reader 只读 / Operator 可写）

| 端点 | 说明 |
|---|---|
| `GET /memory/customers?with_stats=true` | 列表层统计列（单条批量 SQL，避免 N+1） |
| `GET /memory/customers/{cid}/injections` | 注入记录列表（run 维度） |
| `GET /memory/runs/{run_id}/injections` | 当次 run 注入明细（会话视图与注入记录下钻共用） |
| `GET /memory/items/{iid}/injections` | 条目反查注入记录 |
| `GET /memory/items/{iid}/timeline` | 单条生命周期时间线 |
| `GET /memory/customers/{cid}/audit` | 客户维度审计时间线 |

全部 Reader 可读；租户隔离沿用既有依赖注入（跨租户 404）。

### 4. 治理联动

- **客户删除**：`governance.py::delete_customer` 事务内增加 `DELETE FROM memory_injection_log WHERE tenant_id=? AND customer_id=?`，审计 details 增加 `injection_logs_deleted` 计数；
- **客户合并**：明细行 `customer_id` 改挂目标（与 identities 同事务），保持注入历史连续；
- **TTL**：worker maintenance 同车清理超期明细（D3）。

### 5. 前端

- `client.ts`（:72-93）补类型：`MemoryInjection` / `MemoryInjectionDetail` / `MemoryAuditEvent` / `CustomerMemoryStats`；
- `CustomerList.tsx`：统计列（记忆总数、三类条数、待确认高亮、flag 徽标、最近更新），排序与标记筛选；
- `CustomerDetail.tsx`：重构为 8 Tab（概览/画像/偏好/事件/服务标记/待确认/注入记录/审计记录），Tab 懒加载；画像/偏好/事件复用 `MemoryItemTable` 按类型过滤；
- `MemoryItemTable.tsx`：字段列补全（confidence、effective_confidence、source_type/source_ref、protected、last_referenced_at、reference_count、revision），行点击开 `MemoryItemDetailDrawer`；
- 新增 `MemoryItemDetailDrawer`：完整字段 + prev_content diff（简单行级 diff 展示）+ 生命周期时间线 + 最近注入列表；
- 新增 `InjectionLogPanel`：run 列表 + 展开明细（会话工作台与详情页共用）；
- `CustomerMemoryDrawer.tsx`：增加"本次对话注入"分区（按 run 拉取 `/memory/runs/{run_id}/injections`）；
- 空态：历史 run 无明细 → "该会话无注入明细记录（功能上线前数据）"。

## 配置

```python
# settings.py
memory_injection_log_enabled: bool = True        # 明细落库总开关（关时退化为仅计数事件）
memory_injection_log_retention_days: int = 90    # D3 保留期
```

## 测试方案

详见 `04-test-plan.md`。核心：明细落库正确性与批量写入性能、三入口查询、删除/合并级联、TTL 清理、审计时间线无泄露面、前端三层视图。

## 上线与回滚

- 迁移纯增量建表，无存量数据迁移风险；
- `memory_injection_log_enabled=false` 时回退为 Phase 4 行为（仅计数事件），前端空态兜底已内置；
- 回滚直接回退代码 + 删表（表无外部依赖）。
