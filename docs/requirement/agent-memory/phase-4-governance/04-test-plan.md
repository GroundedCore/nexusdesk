# Phase 4 · 治理、衰退与标记记忆 — 测试方案

> 状态：待实施（本阶段开发完成后执行）
> 创建：2026-10-10
> 依据：`02-requirements.md`（FR-1~FR-7、AC-1~AC-13）、`03-technical-design.md`（迁移 `0033`、人工保护、PII、删除/合并、衰退机制）

## 1. 测试范围与原则

**范围**：人工保护（防覆盖）、标记记忆（flag）与内置规则、PII 脱敏、客户删除（"忘记我"）、客户合并、衰退机制（衰减/强化/容量淘汰）、记忆观测指标。

**原则**：

1. **"人工 > 自动"是最高优先级**：任何路径下自动抽取都不得覆盖人工条目——纵深防御（抽取器过滤 + upsert 入口抛错 + DB 约束）逐层验证。
2. **合规项即阻断项**：PII 不可逆、删除级联无残留、flag 抽取物理不可达，任一不过不得上线。
3. **衰退一律软过期**：所有状态迁移可恢复、可审计；`effective_confidence` 不落库（查询时计算）的行为必须固化。
4. **时间类用例不等待**：衰退/规则扫描/容量淘汰全部通过直接改 `last_referenced_at` / `expires_at` / `created_at` 构造，手动触发单轮周期任务。
5. **开关兜底必测**：`memory_decay_enabled=false` 后行为与 Phase 3 完全一致。

## 2. 测试环境与数据构造

| 环境 | 用途 |
|---|---|
| 单测 | PII 纯函数、decay 公式、保护逻辑、合并冲突矩阵（无外部依赖，可穷尽边界） |
| 集成（PG 17 + pytest postgres fixture） | 删除级联、合并事务、周期任务、规则扫描、观测聚合 |
| 集成 B（pgvector 镜像） | 删除后向量列随行清除验证（复用 Phase 3 CI job） |
| mock/demo 模型 | 抽取产出可编程（含 PII 的内容、flag 类型输出等攻击性输入） |

**数据构造约定**：超龄条目用 `UPDATE last_referenced_at = now() - interval 'N days'`；容量场景批量插入 210 条；周期任务（衰退迁移/规则扫描/强化落库）测试内手动触发单轮执行断言终态。

## 3. 单元测试

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P4-UT-01 | PII：手机号 `13812345678` | `138******78`（前3后2） | AC-4 |
| P4-UT-02 | PII：身份证 18 位（含 X 结尾） | 保留前 6 后 4 | FR-3 |
| P4-UT-03 | PII：银行卡 16-19 位 | 保留后 4 | FR-3 |
| P4-UT-04 | PII：邮箱 | 本地名首字符 + `***@域名` | FR-3 |
| P4-UT-05 | PII 误伤控制 | 工单号 `TK-123456`、订单号 `A123`、普通数字语境不误脱；`mask()` 返回命中次数正确 | 技术设计 §2 |
| P4-UT-06 | PII 开关 | `memory_pii_masking_enabled=false` 时原文入库（仅显式配置下）；开关关闭不影响已脱敏数据 | FR-3 |
| P4-UT-07 | 人工保护 | protected 条目遇抽取 update → 不覆盖，新建 `key:suggested` pending 条目 | AC-1/FR-1 |
| P4-UT-08 | 解除保护 | "恢复自动管理"后 `protected=false`、`source_type='extracted'`，后续抽取可正常覆盖 | FR-1 |
| P4-UT-09 | flag 写入权限 | `type='flag' AND source_type='extracted'` 在 upsert 入口直接抛错；抽取器输出 flag 被过滤；DB CHECK 约束拒绝非法 flag key | FR-2 |
| P4-UT-10 | decay 公式 | `effective_confidence` 各类型边界：age=0 → 原值；age=半衰期 → 一半；profile 365 天/preference 90 天/event 30 天；flag 与 protected 免疫 | FR-6.1 |
| P4-UT-11 | 阈值路由 | 跌破 `memory_decay_min_confidence`（0.3）：profile→pending；preference/event→expired；恰好等于阈值的行为固化 | FR-6.1 |
| P4-UT-12 | 合并冲突矩阵 | 同 (type,key) 冲突：protected 优先于非 protected；同保护级取 updated_at 较新者；被弃方物理删除并入审计明细 | AC-6/FR-5 |
| P4-UT-13 | 强化语义 | 强化只刷新 `last_referenced_at` 与 `reference_count`，不提升 base confidence | FR-6.2 |

## 4. 集成测试

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P4-IT-01 | 人工编辑 → 客户改口自报不同姓名 | 人工值不被覆盖；生成 pending 建议；控制台可见 | AC-1 |
| P4-IT-02 | PII 端到端 | 抽取产出含手机号的条目：入库即脱敏；全库（含 prev_content、audit payload）无明文；人工编辑保存同样脱敏；命中次数写审计（`memory.pii_masked`，不含原值） | AC-4 |
| P4-IT-03 | flag 打标注入 | 标记 vip 后进线：注入段最前出现 `【服务标记】`；flag 不占用 20 条画像/偏好上限；独立上限 3 条 | AC-2 |
| P4-IT-04 | 黑名单行为 | blacklist 存在：注入含"涉承诺一律转人工确认"；`memory_blacklist_auto_handoff=true` 时进线自动转人工 | FR-2 |
| P4-IT-05 | 内置规则 | 工单超 `due_at` 未响应 → 规则扫描（手动触发单轮）打 `complaint_risk`（source_type='rule'）；构造 30 天无超时 → 自动解除；人工打的 complaint_risk 不被规则解除 | AC-3 |
| P4-IT-06 | 客户删除 | 执行删除：items/identities/customer 物理删除；会话保留且 `customer_id=NULL` 可继续对话；工单 customer_id 字符串保留、详情展示"客户已删除"；审计含操作人/条数且无客户数据；该 external_key 再进线建新 customer（全新无记忆） | AC-5 |
| P4-IT-07 | 删除性能 | 单客户 200 条（容量上限）删除 P99 ≤ 2s | 非功能 |
| P4-IT-08 | 客户合并 | 两客户（含同键冲突一边人工一边抽取）合并：identities 改挂；冲突保留人工条目；源 customer 删除；审计含源/目标/取舍明细；事务失败时整体回滚 | AC-6 |
| P4-IT-09 | 衰退每日任务 | 构造：preference 200 天未引用、profile 400 天未引用、protected 500 天未引用、flag 500 天：任务执行后 preference→expired、profile→pending、protected 与 flag 保持 active 且照常注入；审计含变迁前分值 | AC-8/AC-10/AC-11 |
| P4-IT-10 | 查询时过滤实时生效 | 每日任务未跑时，跌破阈值条目已不出现在注入与检索（SQL 内联衰减过滤） | 技术设计 §7a |
| P4-IT-11 | 强化 L1 | 同键 upsert 刷新 `last_referenced_at`（构造临近过期条目，客户再次提及同 key → 时钟重置，任务执行后不迁移） | AC-9 |
| P4-IT-12 | 强化 L2/L3 批量落库 | 投递 `reinforce` 任务（聚合同 id）：批量更新 `last_referenced_at`/`reference_count`；`memory_reinforce_sample_rate=0` 时不落库 | FR-6.2 |
| P4-IT-13 | 强化不跑热路径 | run 执行期间无对 memory_items 的逐条强化写库（SQL 计数断言） | FR-6.2 |
| P4-IT-14 | 容量淘汰 | 构造 210 条（含 5 条 protected）：任务执行后 ≤200 条；被淘汰的是 effective_confidence 最低者；protected 全部保留；审计含淘汰明细 | AC-12 |
| P4-IT-15 | expired 恢复 | 控制台恢复 expired 条目 → active 且 `last_referenced_at` 刷新；恢复后不立即被再次衰退（复活即满血） | FR-6.4 |
| P4-IT-16 | `memory_decay_enabled=false` | 无衰退过滤、无状态迁移、无淘汰；行为与 Phase 3 一致 | AC-13 |
| P4-IT-17 | 观测指标 | 构造已知数据后 `memory_summary()`：抽取量/命中率/pending 积压/治理计数/衰退计数与构造一致 | AC-7/FR-7 |
| P4-IT-18 | 向量清除 | pgvector 模式下删除客户后，embedding 列随行清除无残留（SQL 直查验证） | 技术设计 §5 |

## 5. API 测试（新增端点）

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P4-API-01 | 删除客户 | operator 可用、viewer 403、未认证 401；响应不含客户数据；审计写入 | FR-4 |
| P4-API-02 | 合并客户 | operator 可用；冲突取舍明细入审计；源/目标同一人时拒绝 | FR-5 |
| P4-API-03 | flag CRUD | key 仅接受 vip/complaint_risk/blacklist；非法 key 400；viewer 只读 | FR-2 |
| P4-API-04 | 恢复自动管理 / expired 恢复 | 状态与 protected 标志按预期变化；viewer 403 | FR-1/FR-6.4 |
| P4-API-05 | 租户隔离 | 跨租户访问一律 404 | 非功能 |

## 6. 通用核对项（本阶段适用）

| 编号 | 核对项 | 通过标准 |
|---|---|---|
| P4-CM-01 | 迁移 `0033` 在线性 | 存量库执行成功；CHECK 约束不影响存量非 flag 数据 |
| P4-CM-02 | 配置加载 | `memory_pii_masking_enabled` / `memory_rule_scan_interval_minutes` / `memory_decay_*` / `memory_max_items_per_customer` / `memory_reinforce_sample_rate` 等默认值与覆盖生效；`memory_rule_scan_interval_minutes=0` 停用规则扫描 |
| P4-CM-03 | 审计全覆盖 | 编辑/删除/合并/打标/衰退迁移/淘汰/PII 命中均落 `audit_records`（`memory.*` action）；payload 无 PII 原值 |
| P4-CM-04 | 前端 | 客户详情"删除客户"与"合并到…"均有二次确认；条目列表展示 effective_confidence 与 last_referenced_at；观测页记忆卡片区渲染 |

## 7. 回归范围

- Phase 1-3 全量测试不受影响（protected 默认 false、无 flag 时行为不变，有自动化断言）；
- 双模式检索（pgvector/TSVECTOR）在衰减过滤开启后结果仍正确（P4-IT-10 双模式各跑一遍）。

## 8. 缺陷分级与出口准则

| 级别 | 定义 | 处理 |
|---|---|---|
| P0 阻断 | 删除级联残留 PII；人工条目被自动覆盖；flag 被抽取写入；跨租户泄露 | 修复并回归后方可继续 |
| P1 严重 | 衰退误迁移（类型路由错、protected 不免疫）；合并丢记忆；PII 漏脱敏 | 修复前不得进入验收 |
| P2 一般 | PII 误伤非 PII 内容；观测指标偏差；注入顺序偏差 | 记录后可带问题验收，需产品签字 |
| P3 轻微 | 文案、日志问题 | 排期修复 |

**出口准则**：全部用例通过；AC-1~AC-13 复现通过；**"人工 > 自动"、删除级联完整性、PII 不可逆为阻断项**，任一不过不得上线；Phase 1-3 回归套件全绿；无 P0/P1 未关闭。
