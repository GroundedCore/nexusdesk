# Phase 4 · 治理与标记记忆 — 技术落地文档

## 改动总览

| 层 | 文件 | 改动 |
|---|---|---|
| 迁移 | `backend/migrations/versions/0033_memory_governance.py` | `memory_items` 加审计辅助列；审计复用 audit_records（见决策） |
| 抽取 | `modules/memory/extraction.py` | 人工保护逻辑；flag 拒绝；PII 脱敏调用 |
| 新模块 | `modules/memory/pii.py` | 脱敏纯函数集 |
| 新模块 | `modules/memory/governance.py` | 删除 / 合并 / 打标 / 内置规则 |
| 新模块 | `modules/memory/decay.py` | 衰退：effective_confidence 计算、状态迁移、容量淘汰、强化落库 |
| API | `modules/memory/routes.py` | 删除、合并、flag、恢复自动管理端点 |
| 注入 | `modules/agent_runtime/engine.py` | flag 小节注入（最高优先级）；注入/检索查询带 effective_confidence 过滤 |
| 观测 | `modules/observability/service.py` | 记忆维度指标（含衰退指标） |
| Worker | `modules/memory/worker.py` | 规则扫描任务；每日衰退迁移/淘汰任务；强化批量落库；memory_tasks 租约/reaper（见 §4，多副本前置项） |

## 数据模型（迁移 `0033_memory_governance.py`）

```sql
-- 治理辅助列
ALTER TABLE memory_items ADD COLUMN protected BOOLEAN NOT NULL DEFAULT false;
-- protected=true 等价于 source_type='human' 的冗余标记，用于抽取器快速过滤；
-- 不新增表，保持模型扁平

-- flag 的受控 key 用 CHECK 约束保证（仅 type='flag' 时）
ALTER TABLE memory_items ADD CONSTRAINT memory_items_flag_key_check
    CHECK (type <> 'flag' OR key IN ('vip', 'complaint_risk', 'blacklist'));
```

审计决策：**复用现有 `audit_records` 表**（ObservabilityService.audit 已在流式读取），不建新表。记忆治理事件作为新的 action 类别写入：`memory.delete_customer` / `memory.merge` / `memory.flag` / `memory.edit` / `memory.pii_masked`，payload 记录操作明细。

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

调用点：`MemoryService.upsert_item()` 统一入口（抽取与人工编辑都走它），命中次数写入审计（`memory.pii_masked`，不含原值）。

### 3. 标记记忆与注入

- flag CRUD 走 `governance.py`，强制 `source_type in ('human','rule')`；`upsert_item` 对 `type='flag' AND source_type='extracted'` 直接抛错（纵深防御，抽取器输出侧也过滤）；
- 注入（engine.py，置于画像段之前）：

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

**memory_tasks 租约与 reaper（承接 Phase 1 R3/L3，生产多副本前置项）**：

- 背景：Phase 1 部署形态已修订为生产独立 `memory-worker` 容器且可多副本伸缩（见 `../phase-1-conversation-summary/03-technical-design.md` §核心流程3，2026-10-10 修订）；硬杀时 running 任务卡死的问题（Phase 1 测试报告 R3 / 验收报告 L3）在多副本下必须解决。
- 设计：`memory_tasks` claim 引入租约——认领时写 `owner`（实例标识）与 `lease_expires_at = now() + 120s`，执行期间心跳续约；Worker 周期 reaper 将 `status='running' AND lease_expires_at < now()` 的任务重置为 `pending`。
- 先例：直接借鉴知识库 Worker 的 SKIP LOCKED + 120 秒租约 + reap 模式（`docs/modules-delivery.md`），保持平台内 worker 语义一致。
- 排期：该项**随 memory-worker 独立容器落地提前实施**（生产多副本部署前置），不必等到 Phase 4 整体排期。

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
HALF_LIFE_DAYS = {"profile": 365, "preference": 90, "event": 30}   # flag 不衰减

def effective_confidence(item) -> float:
    if item.protected or item.type == "flag":
        return item.confidence                      # 免疫
    age_s = (now() - item.last_referenced_at).total_seconds()
    return item.confidence * 2 ** (-age_s / (HALF_LIFE_DAYS[item.type] * 86400))
```

**(a) 查询时过滤**（实时、无批量写）：注入查询（`MemoryService.active_items`）与 `MemoryStore.search` 增加阈值条件，SQL 内联衰减公式：

```sql
WHERE status='active'
  AND confidence * exp(-ln(2) * EXTRACT(EPOCH FROM (now()-last_referenced_at)) / :half_life)
      >= :min_confidence
```

**(b) 每日状态迁移任务**（Worker 周期扫描，与 TTL 清理同车）：
- profile 跌破阈值 → `status='pending'`（被动再确认队列，复用 Phase 2 的 pending 语义）；
- preference/event 跌破阈值 → `status='expired'`；
- 容量淘汰：单客户条目超 `memory_max_items_per_customer`，按 effective_confidence 升序淘汰至上限（跳过 protected）；
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

- 按日抽取量：`source_type='extracted'` 的 created_at 分布 + status 分布；
- 命中率：`runtime_runs` 中注入了记忆的占比（Phase 2 注入时在 run config 记录 `memory_injected=true` 标记，此处统计）；
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
memory_decay_half_life_days: dict = {"profile": 365, "preference": 90, "event": 30}
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
| 单测 decay | effective_confidence 各类型/边界计算；protected 与 flag 免疫；阈值路由（profile→pending，preference/event→expired） |
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
