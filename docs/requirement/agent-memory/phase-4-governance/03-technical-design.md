# Phase 4 · 治理与标记记忆 — 技术落地文档

## 改动总览

| 层 | 文件 | 改动 |
|---|---|---|
| 迁移 | `backend/migrations/versions/0037_memory_governance.py`（R1：0033/0034 已被渠道集成占用，0035/0036 属 Phase 2/3） | `memory_items` 加审计辅助列；审计复用 audit_records（见决策） |
| 抽取 | `modules/memory/extraction.py` | 人工保护逻辑；flag 拒绝；PII 脱敏调用 |
| 新模块 | `modules/memory/pii.py` | 脱敏纯函数集 |
| 新模块 | `modules/memory/governance.py` | 删除 / 合并 / 打标 / 内置规则 |
| 新模块 | `modules/memory/decay.py` | 衰退：effective_confidence 计算、状态迁移、容量淘汰、强化落库 |
| API | `modules/memory/routes.py` | 删除、合并、flag、恢复自动管理端点 |
| 注入 | `modules/agent_runtime/worker.py`（R15：注入取数渲染在 `_customer_memory`，engine.py 零改动） | flag 小节注入（最高优先级）；注入查询带 effective_confidence 过滤 |
| 观测 | `modules/observability/service.py` | 记忆维度指标（含衰退指标） |
| Worker | `modules/memory/worker.py` | 规则扫描任务；每日衰退迁移/淘汰任务；强化批量落库（memory_tasks 租约/reaper 已随 Phase 1 落地，见 §4） |

## 数据模型（迁移 `0037_memory_governance.py`）

```sql
-- 治理辅助列
ALTER TABLE memory_items ADD COLUMN protected BOOLEAN NOT NULL DEFAULT false;
-- protected=true 等价于 source_type='human' 的冗余标记，用于抽取器快速过滤；
-- 不新增表，保持模型扁平

-- flag 的受控 key 用 CHECK 约束保证（仅 type='flag' 时）
ALTER TABLE memory_items ADD CONSTRAINT memory_items_flag_key_check
    CHECK (type <> 'flag' OR key IN ('vip', 'complaint_risk', 'blacklist'));
```

审计决策：**复用现有 `audit_records` 表**（ObservabilityService.audit 已在流式读取），不建新表。记忆治理 action 命名沿用既有 `memory.*` 点分风格（R17）：

| 事件 | action |
|---|---|
| 客户删除 | `memory.customer.deleted` |
| 客户合并 | `memory.customer.merged` |
| 打标/更新标记 | `memory.flag.upserted` |
| 解除标记（人工/规则） | `memory.flag.removed` |
| 恢复自动管理 | `memory.item.released` |
| 衰退 profile→pending | `memory.decay_pending` |
| 衰退 preference→expired | `memory.decay_expired` |
| 容量淘汰 | `memory.evicted` |
| reinforce 任务终败 | `memory.reinforce.failed` |

编辑/删除/恢复/确认/拒绝沿用既有 `memory.item.*` 不变；PII 命中不独立成行，内嵌各事件 details `pii_masked=n`（R5）；payload 只记操作明细（操作者、key、计数），永远不含原值或记忆内容（合规 C4）。

## 核心组件

### 1. 人工保护（`extraction.py` 的合并逻辑调整）

```python
def apply_op(existing, op):
    if op.op == "update" and existing.protected:
        # 不覆盖；转为新建 pending 建议条目
        insert(key=existing.key + ":suggested", status="pending",
               source_type="extracted", content=op.content)
        return
    # 原有覆盖逻辑
```

- 控制台"恢复自动管理"：`protected=false, source_type='extracted'`；
- 人工编辑路径统一置 `protected=true`。

### 2. PII 脱敏（`memory/pii.py`）

```python
def mask(text: str) -> tuple[str, int]:
    """返回 (脱敏后文本, 命中次数)。纯函数，无外部依赖。"""
    # 手机号: 1[3-9]\d{9} → 保留前3后2
    # 身份证: \d{17}[\dXx] → 保留前6后4
    # 银行卡: \d{16,19} → 保留后4
    # 邮箱: 本地名保留首字符 + ***@域名
```

调用点（R5，不做"统一 upsert_item"重构）：四个写入点分别调用 `pii.mask()`——`apply_extracted`（抽取 content 落库前）、`add_event_item`（事件，R6：summarize 截断后再脱敏）、`edit_item`（人工编辑保存前）、governance 的 flag upsert（标记说明）。开关 `memory_pii_masking_enabled=false` 时原样透传；命中计数不新增独立审计 action，内嵌既有审计 details `pii_masked=n`（批次/单次同口径），观测 SQL 以 `sum(CAST(details->>'pii_masked' AS int))` 聚合。

### 3. 标记记忆与注入

- flag CRUD 走 `governance.py`，强制 `source_type in ('human','rule')`；`upsert_flag` 对 `source_type='extracted'` 直接抛错（纵深防御，抽取器输出侧也过滤，flags 永不来自抽取 P0）；
- 注入（`agent_runtime/worker.py::_customer_memory`，置于画像段之前，R15）：

```
【服务标记】
- VIP 客户：优先服务，使用尊称
- 投诉风险：避免承诺时限类表述，争议问题建议转人工
- 黑名单：涉承诺一律转人工确认（blacklist 存在时追加）
```

- flag 不受 `memory_inject_max_items` 限制，独立上限 3 条。

### 4. 内置规则（Worker 周期扫描，每 15 分钟）

```python
def scan_overdue_tickets(tenant):
    # 超时未响应 → upsert flag complaint_risk（source_type='rule', source_ref=ticket_id）
    # 30 天内无超时工单 → 解除 rule 来源的 complaint_risk
    # （人工打的标不受规则解除影响：仅处理 source_type='rule' 的条目）
```

**memory_tasks 租约与 reaper：已随 Phase 1（2026-10-10）落地，本阶段零新增（R2）**：

迁移 0030 的 `memory_tasks` 表已含 `owner` / `lease_expires_at` / `attempts` / `max_attempts` 列；`modules/memory/worker.py` 已实现 claim 写 owner + 120s 租约、30s 心跳续约、claim 前置 reaper（过期 running → pending；`attempts>=max_attempts` → `failed('memory_worker_lost')`）与 `_finish` owner 守卫；覆盖用例 P1-IT-15~21 已在 Phase 1 测试/验收报告中关闭（R3/L3 均"已关闭 2026-10-10"）。本阶段仅将 P1-IT-15~21 纳入回归红线，无新增代码与排期。

### 5. 客户删除（`governance.py`）

```python
def delete_customer(tenant_id, customer_id, operator):
    with transaction():
        DELETE FROM memory_items WHERE ...        # embedding 列随行清除
        DELETE FROM memory_identities WHERE ...
        UPDATE runtime_conversations SET customer_id=NULL WHERE ...
        DELETE FROM memory_customers WHERE id=...
    audit("memory.delete_customer", {operator, items_deleted, ...})
```

- `MemoryStore` 接口补 `delete_customer(customer_id)`：pgvector 实现为 no-op（行删即向量删）；未来 Milvus 实现在此删 collection 数据——接口先行，实现后置；
- 工单 `customer_id` 保留字符串但前端展示"客户已删除"（查询客户不存在即降级展示，无需改 tickets 表）。

### 6. 客户合并（`governance.py`）

```python
def merge_customers(tenant_id, source_id, target_id, operator):
    with transaction():
        UPDATE memory_identities SET customer_id=target WHERE customer_id=source
        for item in source 的 active/pending items:
            冲突 (type,key) 已存在于 target:
                保留方 = protected 优先，其次 updated_at 较新
                被弃方物理删除并记入审计明细
            否则: UPDATE customer_id=target
        DELETE 源 customer
    audit("memory.merge", {source, target, moved, conflicts: [...]})
```

### 7. 衰退机制（`memory/decay.py`）

**计算策略：查询时算分 + 周期任务改状态，effective_confidence 不落库**（避免每日批量重写全表）。

```python
HALF_LIFE_DAYS = {"profile": 365, "preference": 90, "event": 30}   # 由 settings 三标量组装（R9）；flag 不衰减

def effective_confidence(item) -> float:
    if item.protected or item.type == "flag":
        return item.confidence                      # 免疫
    age_s = (now() - item.last_referenced_at).total_seconds()
    return item.confidence * 2 ** (-age_s / (HALF_LIFE_DAYS[item.type] * 86400))
```

**(a) 查询时过滤**（实时、无批量写）：阈值条件**只加在注入查询**（`MemoryService.active_items`，R7）——`MemoryStore.search`（event 检索）不参与置信度过滤，其可见性由 Phase 3 既有 TTL 与排序衰减管理，避免同一内容在"注入"与"检索"两个入口表现不一致之外的二次收窄。SQL 内联衰减公式（`>=` 等号保留，恰在阈值的条目仍注入）：

```sql
WHERE status='active'
  AND confidence * exp(-ln(2) * EXTRACT(EPOCH FROM (now()-last_referenced_at)) / :half_life)
      >= :min_confidence
```

**(b) 每日状态迁移任务**（Worker 周期扫描，与 TTL 清理同车；R8 按 02 FR-6.1 收敛路由）：
- profile 跌破阈值 → `status='pending'`（被动再确认队列，复用 Phase 2 的 pending 语义）；
- preference 跌破阈值 → `status='expired'`；
- **event 不参与置信度迁移**，仅走 Phase 3 既有 180 天 TTL 软过期（若按 30 天半衰期迁移，confidence=1.0 的事件约 52 天即跌破 0.3，180 天留存形同虚设）；
- flag / protected 免疫（SQL 条件 `protected=false AND type IN ('profile','preference')` 双重保证）；
- 容量淘汰：单客户条目超 `memory_max_items_per_customer`，按 effective_confidence 升序淘汰至 `expired`（跳过 protected 与 flag，可恢复）；
- 每次变迁写审计（`memory.decay_expired` / `memory.decay_pending` / `memory.evicted`，含变迁前分值）。

**(c) 引用强化（异步批量，不在 run 热路径写库）**：
- 信号采集：L1 同键 upsert 直接刷新（Phase 2 已含）；L2 `memory_search` 命中条目 id 收集到 run 上下文；L3 注入条目与当轮用户消息的关键词重叠（近似引用）；
- 落库：run 结束后把命中 item_ids 投入 `memory_tasks`（kind=`reinforce`），Worker 聚合同 id 后单条 SQL 批量执行：
  ```sql
  UPDATE memory_items SET last_referenced_at=now(),
         reference_count=reference_count + :hits
  WHERE id = ANY(:ids)
  ```
- 保守规则：强化只重置时钟，不提升 confidence；`memory_reinforce_sample_rate` 支持采样限流防写放大。

**(d) 恢复**：控制台 expired 条目"恢复"操作 → `status='active'` + 刷新 `last_referenced_at`（复活即满血，不复活即再次衰退，防止僵尸条目反复打扰）。

### 8. 观测（`observability/service.py` 新增 `memory_summary()`）

聚合 SQL 直查 memory_items / memory_tasks / audit_records：

- 按日抽取量：聚合 `memory.extract.applied` 审计 details（added/updated/pending/dropped/pii_masked）——同键 upsert 会改写条目行，行表无法还原新增/更新分布（R20）；
- 命中率：24h 内有 `memory.injected` runtime event 的 run 数 / 同期 customer 绑定 run 数（`runtime_runs JOIN runtime_conversations ON customer_id IS NOT NULL`）。`memory.injected` 为本阶段新增记录点（R10：`runtime_runs` 无 customer_id 列，Phase 2 也从未写过 `memory_injected` 标记），由 `repository.finish` 在 run 完成时同事务写入，data 含 `{items, flags, events}` 三段条数；`memory_search` 调用另写 `memory_search.completed`（data `{hits}`），聚合调用量与空结果率；
- 积压：pending 计数与 max(age)；
- 治理：audit_records 按 `memory.*` action 计数；
- 衰退：按日统计 `memory.decay_*` / `memory.evicted` 审计量、强化刷新量（`reinforce` 任务计数）、expired 恢复率（恢复操作数 / 衰退过期数）。

前端观测页加一个"记忆"卡片区。

## 配置

```python
# settings.py
memory_pii_masking_enabled: bool = True
memory_rule_scan_interval_minutes: int = 15
memory_flag_inject_max: int = 3
memory_blacklist_auto_handoff: bool = False   # true 时黑名单客户进线自动转人工
# 衰退机制
memory_decay_enabled: bool = True
memory_decay_half_life_days_profile: int = 365     # R9：三标量而非 dict，沿用 AGENT_ 前缀标量惯例
memory_decay_half_life_days_preference: int = 90
memory_decay_half_life_days_event: int = 30        # 供容量淘汰的 effective_confidence 排序（R8：event 不做置信度迁移）
memory_decay_min_confidence: float = 0.3      # 跌破即按类型路由 pending / expired
memory_max_items_per_customer: int = 200      # 容量硬上限
memory_reinforce_sample_rate: float = 1.0     # 强化写库采样率，写放大失控时限流
```

## 测试方案

| 层 | 用例 |
|---|---|
| 单测 pii | 四类模式的脱敏正确性、误伤率（订单号 TK-123、纯数字上下文不误脱） |
| 单测 protection | protected 条目的 update 转 pending 建议；解除保护后正常覆盖 |
| 单测 governance | 删除的级联完整性；合并的冲突取舍矩阵（人工>抽取、新>旧） |
| 单测 decay | effective_confidence 各类型/边界计算；protected 与 flag 免疫；阈值路由（profile→pending，preference→expired，event 仅 TTL，R8） |
| 集成 decay | 构造超龄条目跑每日任务：状态迁移正确、审计完整、恢复后时钟刷新 |
| 集成 强化 | mock reinforce 任务：批量 UPDATE 聚合正确；采样率 0 时不落库 |
| 集成 容量 | 构造 210 条超限，任务执行后 200 条且被淘汰的是分值最低者（protected 跳过） |
| 集成 flag | 打标 → 注入位置与内容；黑名单自动转人工配置生效；规则扫描打标/解除 |
| 集成 删除 | 删除后全表无残留；会话可继续（customer_id=NULL）；审计存在 |
| 集成 观测 | 构造数据后 memory_summary 各指标数值正确 |
| 回归 | Phase 1-3 全量测试不受影响（protected 默认 false、无 flag 时行为不变） |

## 上线与回滚

- 全部为增量能力；规则扫描可通过 `memory_rule_scan_interval_minutes=0` 停用；衰退机制可通过 `memory_decay_enabled=false` 整体关闭（行为回到 Phase 3）；
- PII 开关关闭后仅影响新写入（已脱敏数据不可恢复，属预期）；
- 无数据迁移风险，可直接上线后逐项开启。
