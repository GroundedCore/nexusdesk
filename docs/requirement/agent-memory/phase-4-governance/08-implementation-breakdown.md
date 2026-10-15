# Phase 4 · 治理、衰退与标记记忆 — 实施拆解方案

> 状态：已裁定（技术负责人拆解，dev/qa 以此为执行基线）
> 创建：2026-10-15
> 依据：`01`~`05` 全部文档 + 现状代码核查（backend @ feat/agent-memory-phase-4，main 含 Phase 1/2/3 全部实现）
> 约束：测试本地优先执行（本机双容器 alpine/pgvector 矩阵）；代码提交统一由 dev 在 feat/agent-memory-phase-4 分支执行；pgvector 双模式为既有事实，治理功能须在两种检索模式下都成立

## 1. 任务拆解（按依赖顺序）

依赖主线：**迁移 → 纯函数/配置 → service 层治理与衰退 → worker 周期任务 → 注入/强化链路 → API → 前端 → 测试**。T1 阻塞一切含新列的代码；T3 与 T2 可并行；T11 前端在 T10 接口确定后并行。

| # | 任务 | 文件（新建/修改） | 依赖 | 溯源 |
|---|---|---|---|---|
| T1 | 迁移：`memory_items` 加 `protected BOOLEAN NOT NULL DEFAULT false` 并回填（`source_type='human'` → `protected=true`）；flag 受控 key CHECK 约束（`type<>'flag' OR key IN ('vip','complaint_risk','blacklist')`） | `backend/migrations/versions/0037_memory_governance.py`（新） | — | FR-1/FR-2/FR-6.1，见 R1/R3/R18 |
| T2 | 配置项落地（11 项，清单见 §3.1） | `backend/src/agent_platform/settings.py` | — | FR-2/FR-3/FR-6/03 §配置，见 R9 |
| T3 | PII 脱敏纯函数集 `mask(text) -> (masked, hits)`：手机号/身份证/银行卡/邮箱四类模式 + 边界防误伤 | `modules/memory/pii.py`（新） | — | FR-3/AC-4，见 R5/R6 |
| T4 | 治理服务：flag upsert/解除、客户删除（级联+审计）、客户合并（冲突取舍+审计）、内置规则扫描函数；`MemoryStore` 协议补 `delete_customer(customer_id)`（两实现 no-op 预留） | `modules/memory/governance.py`（新）、`modules/memory/store.py` | T1、T2、T3 | FR-2/FR-4/FR-5/AC-3/AC-5/AC-6，见 R12/R16/R17 |
| T5 | `service.py` 改造：① `apply_extracted` 人工保护降级为 `key:suggested` pending 建议（upsert 语义）+ PII 调用点；② `add_event_item` PII 调用点；③ `edit_item` 置 `protected=true` + PII 调用点；④ 新增 `release_item`（恢复自动管理）；⑤ `restore_item` 扩展 expired→active 并刷新时钟；⑥ `confirm_item` 刷新时钟 + suggested 合并语义；⑦ `active_flags` 查询；⑧ `customer_detail` 增 `protected/last_referenced_at/reference_count/effective_confidence` 字段 | `modules/memory/service.py` | T1、T2、T3、T6（effective_confidence 函数） | FR-1/FR-2/FR-3/FR-6.4/AC-1，见 R4/R5/R6/R18/R19 |
| T6 | 衰退模块：半衰期表（取自 settings 三标量）、`effective_confidence()`、注入查询 SQL 内联阈值过滤片段、每日状态迁移（profile→pending / preference→expired，含变迁前分值审计）、容量淘汰（跳过 protected，含明细审计） | `modules/memory/decay.py`（新）、`modules/memory/service.py`（`active_items` 过滤） | T1、T2 | FR-6.1/FR-6.3/FR-6.4/AC-8/AC-10/AC-11/AC-12，见 R7/R8/R9 |
| T7 | Worker 扩展：dispatcher 路由 `reinforce` 新 kind（聚合同 id 批量 UPDATE `last_referenced_at`/`reference_count`）、`FAILURE_AUDIT['reinforce']`；规则扫描独立计时（默认 15 分钟，0=停用，调用 T4 扫描函数）；每日衰退迁移/容量淘汰并入现有 `maintenance()`（与 TTL/embedding 重试同车，`memory_decay_enabled` 门控）；bootstrap 装配 | `modules/memory/worker.py`、`modules/memory/bootstrap.py`、`modules/memory/decay.py`（执行器） | T4、T6 | FR-2/FR-6.2/FR-6.3，见 R2/R14 |
| T8 | 注入链路：`render_flag_injection`（【服务标记】段，blacklist 追加固定指令）+ `_customer_memory` 组合（顺序：服务标记→画像→偏好→待跟进事项）+ L3 关键词重叠收集；`CustomerService.send` 黑名单自动转人工（`memory_blacklist_auto_handoff`） | `modules/memory/service.py`、`modules/agent_runtime/worker.py`、`modules/customer_service/service.py`、`apps/api/platform_services.py`（注入 memory） | T4、T5 | FR-2/FR-6.2/AC-2，见 R11/R13/R15 |
| T9 | 强化与观测记录点：`MemoryHit` 增 `id`（工具输出不变）；`memory_search` 命中 id 收集到 run 上下文 + `emit('memory_search.completed', {hits})`；worker finish 经 `_memory_context` 投递 `reinforce` 任务（投递侧采样）+ 写 `memory.injected` runtime event；`repository.finish` 内同事务 INSERT | `modules/memory/store.py`、`integrations/business_tools/platform_tools.py`、`modules/agent_runtime/worker.py`、`modules/agent_runtime/repository.py`、`modules/memory/events.py`（enqueue_reinforce_task 可独立放 governance 或新模块） | T8 | FR-6.2/FR-7，见 R10/R11 |
| T10 | API：`DELETE /memory/customers/{cid}`、`POST /memory/customers/{cid}/merge`、`PUT/DELETE /memory/customers/{cid}/flags/{key}`、`POST /memory/items/{iid}/release`；`GET /observability/memory`（`memory_summary()`，观测聚合 SQL）；`TicketUpdate` 增可选 `due_at` | `modules/memory/routes.py`、`modules/observability/service.py`、`apps/api/platform_routes.py`、`modules/customer_service/service.py`（due_at 落列） | T4、T5、T6 | FR-4/FR-5/FR-7/AC-7，见 R10/R12/R20 |
| T11 | 前端：客户详情 flag 卡片组（打标/解除/编辑说明）、"删除客户"与"合并到…"（均二次确认）；条目表增 effective_confidence/last_referenced_at/protected 标识、"恢复自动管理"与 expired 恢复操作；PendingQueue 建议条目标识（确认=合并回原条目文案）；工单详情"客户已删除"降级；观测页记忆卡片区；labels/i18n/e2e 增补 | `frontend/src/features/memory/*.tsx`、`features/tickets/TicketsPage.tsx`、`features/observability/ObservabilityPage.tsx`、`shared/api/client.ts`、`i18n`、`frontend/tests/memory.spec.ts` | T10 | FR-2/FR-4/FR-5/FR-6.4/FR-7/P4-CM-04，见 R4/R16 |
| T12 | 测试：单测（pii/protection/decay 公式/合并矩阵/强化语义）、PG 集成（删除级联/合并事务/周期任务/规则扫描/容量/观测聚合/双模式过滤）、API 测试、迁移测试、前端 e2e 增补、Phase 1-3 回归 | `backend/tests/test_memory_governance*.py` / `test_memory_decay*.py` / `test_memory_pii*.py`（新）、`frontend/tests` | T1~T11 | 04 全文，见 §3.2 红线 |
| T13 | 文档修订（dev 范围）：03 中迁移号 `0033`→`0037`、§4 租约/reaper 段标注"已随 Phase 1 落地，本阶段零新增"（R2）、§7a "MemoryStore.search 增加阈值条件"按 R7 修订为仅注入查询、§7b event 路由按 R8 修订、§8 "Phase 2 已记录 memory_injected" 误述按 R10 修订、改动总览 engine.py→worker.py（R15）、§2 upsert_item 统一入口措辞按 R5 修订、审计 action 命名按 R17 修订。**04/05 归 QA 所有，dev 不得修改**；04 的修订（依据行与 P4-CM-01 的 `0033`→`0037`、P4-IT-10 措辞按 R7/R8 改写）由 QA 在执行 T12 前落实，具体措辞见 R7 | `03-technical-design.md`（dev）；`04-test-plan.md`（qa） | — | R1/R2/R5/R7/R8/R10/R15/R17 |

报告格式沿用 Phase 2/3 的 `06-test-report.md` / `07-acceptance-report.md` 结构（qa / acceptance 参照执行）。

## 2. 文档与现实不一致清单及裁定

### R1 迁移编号：0033 → `0037_memory_governance.py`

文档（03 §改动总览/§数据模型、04 依据行与 P4-CM-01）写 `0033_memory_governance.py`。现实：0033/0034 已被原生渠道集成占用，0035 是 Phase 2 的 `memory_customers`，0036 是 Phase 3 的 `memory_events`（Phase 2/3 已有两次重编号先例）。**裁定**：新迁移编号 `0037_memory_governance.py`，`down_revision='0036_memory_events'`；dev 在 T13 同步修订 03 文档引用，qa 修订 04 依据行与 P4-CM-01。迁移内容按 R3 收敛。

### R2 租约/reaper：已随 Phase 1 落地，本阶段剩余工作量为零

03 §4 称 memory_tasks 租约与 reaper"随 memory-worker 独立容器落地提前实施（生产多副本部署前置），不必等到 Phase 4 整体排期"——这是 2026-10-10 编写时的排期假设。现实：该假设**已经兑现**。Phase 1 修订轮（提交 `d43a02b`..`e863e34`）已将租约/reaper 完整落地并经测试关闭：迁移 0030 的 `memory_tasks` 表已含 `owner UUID` / `lease_expires_at TIMESTAMPTZ` / `attempts` / `max_attempts` 列；`modules/memory/worker.py` 实现 claim 写 owner + 120s 租约、30s 心跳续约、claim 前置 reaper（`status='running' AND lease_expires_at<now()` → pending；`attempts>=max_attempts` → `failed('memory_worker_lost')`）、`_finish` owner 守卫；覆盖用例 P1-IT-15~21 已在 Phase 1 测试/验收报告中关闭（R3/L3 均"已关闭 2026-10-10"）。**裁定**：本阶段对租约/reaper **零新增代码、零排期**；仅将 P1-IT-15~21 纳入回归红线（§3.2）。dev 在 T13 修订 03 §4 措辞为"已随 Phase 1（2026-10-10）落地，本阶段无剩余工作"。

### R3 数据模型：文档假设的列大多已存在，迁移 0037 仅需两项

03 §数据模型假设需要审计辅助列。现实核查 `0035_memory_customers.py` + `0036_memory_events.py`：`memory_items` 已具备 `expires_at`、`last_referenced_at`（NOT NULL DEFAULT now()）、`reference_count`、`status` CHECK 含 `expired`、`source_type`、`prev_content`、`UNIQUE(tenant_id,customer_id,type,key)`；`embedding`（pgvector 探测块）随行删除即清除；`is_commitment` 在 `ticket_process_log`（0036），**不在** memory_items。**裁定**：迁移 0037 内容收敛为——① `protected` 列 + 回填（R18）；② flag 受控 key CHECK 约束。衰退（FR-6）与 TTL 无需任何新列。`memory_tasks.kind` 是 VARCHAR(30) 无 CHECK，新增 `reinforce` kind 不需要约束迁移（Phase 3 `event_write` 先例）。

### R4 人工保护建议条目的确认语义：confirm=合并回原条目，不是激活建议行

03 §1 设计 `insert(key=existing.key + ":suggested", status="pending")`，但未定义该 pending 条目被"确认"后的行为。若复用现有 `confirm_item`（pending→active），会得到 active 的 `name:suggested` 与 active 的人工 `name` **两条并存**，重复注入且违背 FR-1"人工确认后才生效"（生效=抽取值落到原条目）。**裁定**：

- `confirm_item` 对 key 以 `:suggested` 结尾的条目执行**合并语义**：原条目（同 customer/type、去掉后缀的 key）content 更新为建议值、`prev_content` 保留原值、`revision+1`、`last_referenced_at=now()`，`source_type`/`protected` 保持人工不变；建议条目物理删除；原条目不存在（人工条目被删）时退化为普通 confirm。
- `reject_item` 对建议条目不变（→deleted）。
- 抽取重复产出同键建议：upsert 建议行（pending 状态下刷新 content/confidence/updated_at），唯一约束天然幂等。
- 列宽边界：`key` 列 VARCHAR(100)，`custom_*` 扩展键最长 100——拼接 `:suggested` 会超长。**裁定**：基底 key 截断至 89 字符再拼后缀（截断后冲突由唯一约束吸收，仍幂等）。
- 前端 PendingQueue 对 `:suggested` 条目显示原键 + "建议更新（人工保护）"标签，确认文案改为"确认后将更新人工维护的原条目"。

### R5 PII 调用点：不做"统一 upsert_item"重构，四写入点调用同一纯函数；命中计数内嵌既有审计

03 §2 称"调用点：`MemoryService.upsert_item()` 统一入口（抽取与人工编辑都走它）"。现实：**不存在** `upsert_item` 方法——写入分散在 `apply_extracted`（抽取）、`add_event_item`（事件）、`edit_item`（人工编辑），各有各的事务语义，为脱敏发明统一入口是大重构且收益存疑。03 §数据模型又称命中次数写审计 action `memory.pii_masked`。**裁定**：

- PII 在四个写入点分别调用 `pii.mask()`：`apply_extracted`（content 落库前）、`add_event_item`（R6）、`edit_item`（保存前）、governance 的 flag upsert（标记说明同样可能含 PII）。开关 `memory_pii_masking_enabled=false` 时原样透传。
- 命中计数**不新增独立审计 action**，内嵌既有审计事件的 details：`memory.extract.applied` 增 `pii_masked=n`（批次聚合）、`memory.item.edited` / `memory.flag.upserted` / `memory.event.*` 增 `pii_masked=n`；观测 SQL 以 `sum(CAST(details->>'pii_masked' AS int))` 聚合（R20 同口径）。理由：审计行与业务操作一一对应，避免每次写入双行审计；payload 永远不含原值（合规 C4）。
- dev 在 T13 修订 03 §2 措辞。

### R6 事件内容同样脱敏（FR-3 字面范围的补全）

02 FR-3 写"时机：抽取写入前 + 人工编辑保存前"，未提事件写入；但 01 范围写"PII 脱敏（入库前对 content 做模式脱敏，**原值不落库**）"，且工单描述/备注是客户自由文本，恰是 PII 高发区。成功指标"全库脱敏覆盖率 ≥99%"也要求事件路径覆盖。**裁定**：`add_event_item` 同样调用 `pii.mask()`（R5 四写入点之一）；事件内容已被 `summarize()` 截断，脱敏在截断后执行（顺序：summarize → mask，避免被截断的半个号码绕过模式）。

### R7 查询时阈值过滤仅应用于注入查询；event 检索不参与置信度过滤

03 §7a 称"注入查询（`MemoryService.active_items`）与 `MemoryStore.search` 增加阈值条件"。两处现实冲突：① 02 FR-6.1 表格明确 event 的处理是"30 天排序衰减（Phase 3 已含）+ 180 天硬 TTL → expired"，**不含**置信度阈值路由；② Phase 3 已固化"衰减只降权不隐藏"（P3-UT-05 常驻断言，Phase 3 R 系列裁定双模式排序语义一致），对 event 检索加阈值会破坏该回归；③ commitment 行无 TTL，若按 30 天半衰期过滤，约 52 天未引用即不可检索/注入，违背"承诺待兑现直至兑现"语义。**裁定**：

- SQL 内联衰减阈值过滤只加在注入查询 `active_items`（types profile/preference）；`MemoryStore.search`（type='event'）**不加**阈值条件，event 可见性由 TTL 管理（Phase 3 maintenance 已有）。
- 注入排序不变（`updated_at DESC`），过滤只是 WHERE 增项；阈值等值保留（`>= :min_confidence`，P4-UT-11 固化）。
- 04 P4-IT-10 措辞由 **qa** 修订为："每日任务未跑时，跌破阈值的 profile/preference 条目已不出现在注入段（SQL 内联衰减过滤实时生效）；event 检索不参与置信度过滤，可见性由 TTL 管理（08 R7/R8）"。dev 在 T13 修订 03 §7a。

### R8 每日状态迁移路由：event 不参与置信度迁移（02 优先于 03 §7b）

03 §7(b) 写"preference/event 跌破阈值 → `status='expired'`"，与 02 FR-6.1 event 行"硬 TTL → expired"矛盾：按 30 天半衰期，confidence=1.0 的事件约 52 天未引用即跌破 0.3，远早于 180 天 TTL——若照 03 执行，Phase 3 的 180 天事件留存形同虚设。**裁定**（需求文档优先）：每日迁移只处理两类——profile 跌破阈值 → `pending`；preference 跌破阈值 → `expired`；event 仅走既有 TTL 软过期；flag/protected 免疫（SQL 条件 `protected=false AND type IN (...)` 双重保证）。每次变迁写审计含变迁前 effective_confidence（SQL 同式计算取值）。dev 在 T13 修订 03 §7b。

### R9 半衰期配置 dict → 三个独立标量

03 §配置写 `memory_decay_half_life_days: dict = {...}`。现实：本仓库配置全部经 `AGENT_` 前缀环境变量注入（pydantic-settings），dict 字段需 JSON 串解析，与 Phase 1-3 全标量惯例相悖，compose/README 可运维性差。**裁定**：拆为 `memory_decay_half_life_days_profile=365` / `memory_decay_half_life_days_preference=90` / `memory_decay_half_life_days_event=30`（各 `Field(ge=1, le=3650)`）；decay 模块内部组装半衰期表。event 标量保留的原因：排序衰减（store）用的是 `memory_search_half_life_days`（Phase 3 既有），而 R8 裁定 event 不参与置信度迁移——event 标量供容量淘汰的 effective_confidence 排序使用（FR-6.3 对全类型生效）。

### R10 "Phase 2 已记录 memory_injected"系误述：命中率记录点为本阶段新增

03 §8 称命中率统计基于"Phase 2 注入时在 run config 记录 `memory_injected=true` 标记"。现实核查：全仓库无 `memory_injected`；`runtime_runs` **没有** `customer_id` 列（customer 链接是 claim 时从 `runtime_conversations` 并入 run 字典的内存态，不落库）。**裁定**：Phase 4 新增两个 runtime event 记录点（`runtime_events` 既有表，零迁移）——

- `memory.injected`：run 完成时由 `repository.finish` 写入（沿用 `summary.queued`/`memory_extract.queued` 同事务先例），data 含 `{items, flags, events}` 三段条数；注入为空则不写。命中率 = 24h 内有 `memory.injected` 事件的 run 数 / 同期 customer 绑定 run 数（`runtime_runs JOIN runtime_conversations ON customer_id IS NOT NULL`）。
- `memory_search.completed`：`PlatformTools` 执行 memory_search 后经既有 `emit` 写入（`knowledge.retrieved` 先例），data 含 `{hits: n}`；调用次数与空结果率（hits=0）由此聚合。

dev 在 T13 修订 03 §8 措辞。

### R11 强化链路对接点：MemoryHit 增 id、run 字典作收集器、finish 同事务投递、投递侧采样

03 §7(c) 方向正确但缺三个落地细节。**裁定**：

- **L2 命中 id 获取**：`MemoryHit` 增 `id` 字段（store 两个实现的 SELECT 与 `rank_hits` 同步）；`memory_search` 工具对模型的输出**不变**（仍只给 content/key/updated_at/score），id 仅内部使用。
- **run 上下文收集器**：`RuntimeFactory.for_run(row, ...)` 把 worker 持有的同一个 `row` 字典传给 `PlatformTools`（已核实 platform_services.py:148 与 worker.py:70 共享同一对象）——收集器即为 `row.setdefault("_memory_reinforce", {})`（id→hits）；L2 在 `PlatformTools._execute` memory_search 分支追加；L3 在 `_customer_memory` 注入时计算（key/content 分词与当轮 `row["input"]` 重叠即收集，分词函数模块级可单测）。
- **落库**：`_memory_context` 读取收集器放入 memory dict → `repository.finish` 在既有完成事务内 INSERT `memory_tasks(kind='reinforce', payload={"hits": {id: n}})`（同步路径只允许 INSERT 的红线不变）；采样在**投递侧**（`random() < memory_reinforce_sample_rate` 才投递），执行侧不再采样；`sample_rate=0` 完全不落库（P4-IT-12/13）。
- Worker 执行器聚合同 id 后单条 `UPDATE memory_items SET last_referenced_at=now(), reference_count=reference_count + :hits WHERE id=:id`（逐 id 一行，payload 上限 200 id 截断）；只重置时钟不提升 confidence（FR-6.2）。

### R12 内置规则的触发源：`due_at` 当前无任何写入路径

02 FR-2 规则①"工单超过 `due_at` 未响应"。现实：`tickets.due_at` 列存在（0008，可空，TICKETING.md 注明"首版不是完整 SLA 引擎"），但**全代码库无任何写入**——`TicketUpdate` 无此字段，前端无入口；不补触发源则规则①在生产永远空转，AC-3 只能靠改库演示。**裁定**：`TicketUpdate` 增可选 `due_at: datetime | None`（operator 可设/可清空），`update_ticket` 落列并写 `detail_changed` 日志（既有字段变更先例）；规则扫描语义不变（`due_at<now() AND status IN ('open','in_progress') AND customer_id 非空` → upsert `complaint_risk`，`source_type='rule'`、`source_ref=ticket_id`）。这是让规则有合法数据来源的最小增量，不引入 SLA 引擎。测试仍可改库构造（04 §2 约定不变）。

### R13 黑名单自动转人工落点：`CustomerService.send`，不在 run worker 内

03 §配置只定义了 `memory_blacklist_auto_handoff`，未定落点。核查：`HandoffService.request` 会取消 queued/running runs 并置会话 `mode='waiting'`——若在 RuntimeWorker 执行 run 前触发，会取消自身 run，逻辑拧巴。现实存在干净先例：`CustomerService.send` 对"转人工"关键字在同事务内 `handoffs.request` 并直接返回（service.py:58-61），且 **Web 与渠道进线都经此入口**（channel/service.py:595 调用 `customer.send`）。**裁定**：`send` 在模式检查后增加——`memory_blacklist_auto_handoff=true` 且会话 `customer_id` 有 active blacklist flag → 落用户消息 + `handoffs.request(reason='黑名单客户自动转人工')` 后返回（不提交 run）；`CustomerService` 构造函数注入 `memory`（可选参数，platform_services 装配）。默认 false 不改变任何现有行为。

### R14 周期任务与现有 maintenance 的衔接：双计时器，强化走任务队列

03 §4/§7 把规则扫描（15 分钟）、每日衰退迁移、强化批量落库并列为"Worker 周期任务"。现实：`worker.py` 已有空闲周期 maintenance 机制（R11 先例：无调度器，空闲且距上次 ≥24h 执行 TTL + embedding 重试，启动即首轮）。**裁定**：

- **规则扫描**：独立计时器 `last_rule_scan`，间隔 `memory_rule_scan_interval_minutes`（默认 15，`0`=停用）；serve 循环空闲分支先判规则扫描 due 再判 maintenance due。扫描函数在 governance.py（T4），单租户两条 SQL（打标 upsert + 30 天无复发解除，仅作用 `source_type='rule'` 行）。
- **每日衰退迁移/容量淘汰**：并入现有 `maintenance()`（与 TTL、embedding 重试同车，启动即首轮），整体由 `memory_decay_enabled` 门控（false 时跳过，行为与 Phase 3 一致，AC-13）。
- **强化落库**：**不是**周期任务——`reinforce` 是 `memory_tasks` 新 kind，走既有 claim 循环（租约/重试/审计语义自动继承），dispatcher 加路由 + `FAILURE_AUDIT['reinforce']='memory.reinforce.failed'`。

### R15 注入落点：`agent_runtime/worker.py`，不是 engine.py（Phase 3 R8 同案）

03 §改动总览把"flag 小节注入"标在 `engine.py`。现实与 Phase 3 完全同构：注入取数渲染在 `agent_runtime/worker.py::_customer_memory`，`engine.py` 只把 `customer_memory` 追加为 SystemMessage。**裁定**：`_customer_memory` 增取 `active_flags`（独立 LIMIT `memory_flag_inject_max`，不占 `memory_inject_max_items` 与 token 预算），段落顺序 **【服务标记】→【客户画像】→【客户偏好】→【待跟进事项】**（FR-2"注入段最前"；会话摘要是独立消息不受影响）；`render_flag_injection` 放 `memory/service.py`（可单测，blacklist 存在时追加固定指令行"谨慎服务，涉承诺一律转人工确认"）；`engine.py` 零改动。dev 在 T13 修订 03 改动总览。

### R16 删除/合并的级联与工单展示：FK 级联已存在；工单"客户已删除"用视图标记落地

核查：`memory_items` / `memory_identities` 对 `memory_customers` 均为 `ON DELETE CASCADE`（0035）；`runtime_conversations.customer_id` 是无级联动作的普通 FK（必须先置 NULL）；`tickets.customer_id` 是 VARCHAR(100) 无 FK（天然保留）。**裁定**：

- 删除：事务内显式 `DELETE memory_items` / `DELETE memory_identities`（为审计取条数）→ `UPDATE runtime_conversations SET customer_id=NULL` → `DELETE memory_customers`（级联兜底）；embedding 列随行清除（P4-IT-18 双模式验证）；`MemoryStore.delete_customer` 接口先行、两实现 no-op（Milvus 预留）。审计 details 只含操作人/条数/时间，不含任何客户数据（合规 C2/C4）。同 external_key 再进线由 `resolve_customer` 唯一约束吸收自然新建（FR-4 后续行为零新增代码）。
- 合并：identities 改挂（唯一约束在 `(tenant,source,external_key)` 而非 customer，改挂不会冲突）；items 按 03 §6 冲突矩阵取舍（protected 优先，其次 `updated_at` 较新；被弃方物理删除并入审计明细）；仅归并 active/pending 行，deleted/expired 行随源 customer 级联清除。
- 工单"客户已删除"：不改 tickets 表（03 §5 结论成立）。`TICKET_VIEW` 增 `EXISTS(SELECT 1 FROM memory_customers ...)` 输出 `customer_active`，前端工单详情按此渲染"查看客户记忆"链接或"客户已删除"文本。

### R17 审计 action 命名沿用现有 `memory.*` 风格

03 §数据模型列了 `memory.delete_customer` / `memory.merge` / `memory.flag` / `memory.edit` / `memory.pii_masked`。现实既有命名：`memory.item.edited/deleted/restored/confirmed/rejected`、`memory.extract.applied`、`memory.summary.failed` 等。**裁定**（风格一致 + R5 已裁定 pii 不独立成行）：

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
| PII 命中 | 内嵌各事件 details `pii_masked=n`（R5） |

编辑/删除/恢复/确认/拒绝沿用既有 `memory.item.*` 不变。dev 在 T13 同步 03 §数据模型审计决策段。

### R18 `protected` 列与 `source_type='human'` 的冗余：双写维护，各司其职

03 §数据模型新增 `protected` 并称"等价于 source_type='human' 的冗余标记"。冗余列天然有发散风险，但设计理由成立（衰退/淘汰 SQL 的快速过滤 + FR-6.1 术语锚定），保留。**裁定**：不变式 `protected ⇔ source_type='human'`，由三个写入点维护——迁移回填（`UPDATE memory_items SET protected=true WHERE source_type='human'`）、`edit_item`（同时置 `source_type='human', protected=true`）、`release_item`（同时置 `source_type='extracted', protected=false`）；抽取/事件写入路径原有 `source_type<>'human'` 守卫不变（单一判据防覆盖），衰退/淘汰/合并冲突取舍用 `protected`。qa 增一致性核对用例（P4-CM 系列补一条：`protected <> (source_type='human')` 的行数恒为 0）。

### R19 expired 恢复与 pending 确认的时钟刷新（防"复活即再死"）

03 §7(d) 要求 expired 恢复"刷新 `last_referenced_at`（复活即满血）"，但现实：`restore_item` 当前仅允许 deleted→active；`confirm_item`（pending→active）不刷新时钟——profile 被衰退转 pending 后经人工确认，次日迁移会再次打回（AC-11"确认后恢复注入"无法满足）。**裁定**：`restore_item` allowed 扩为 `{deleted, expired}`，从 expired 恢复时 `last_referenced_at=now()`；`confirm_item` 统一刷新 `last_referenced_at=now()`（对抽取 pending 无害——原本就新）。两者审计沿用既有 action，details 增 `old_status`（`_set_status` 已含）。

### R20 观测"按日抽取量"口径：取审计聚合，非 memory_items.created_at

03 §8 写"按日抽取量：`source_type='extracted'` 的 created_at 分布 + status 分布"。现实：同键 upsert 会覆写行（created_at 不变、status 可变），"新增/更新/pending/丢弃"四类口径从行表无法还原。**裁定**：抽取量取自 `audit_records` 的 `memory.extract.applied` details（已含 added/updated/pending/dropped/noop 计数）按日聚合；积压（pending 计数 + 最老年龄）直查 memory_items；治理/衰退计数按 R17 action 聚合；强化刷新量取 `reinforce` 任务计数（done）与 payload 内 id 数。`memory_summary()` 全部聚合 SQL 直查，24h/7d 窗口与现有 observability 风格一致。

### R21 存量数据不回溯脱敏（新写入口径）

PII 脱敏自本阶段开关生效起约束新写入；Phase 2/3 存量行可能已含明文。成功指标"≥99% 覆盖率"的测量口径**裁定**为验收期构造数据 + 上线后新写入；存量数据如需合规清理另行立项评估（在验收报告遗留项中注明，不阻塞本阶段）。

### 其他核查结论（无不一致，确认设计可行）

- `memory_tasks.kind` VARCHAR(30) 无 CHECK → `reinforce` kind 零迁移（同 Phase 3 `event_write` 先例）；
- 迁移 0035 的 status CHECK 已含 `expired` → 衰退软过期、容量淘汰无需约束变更；`UNIQUE(tenant_id,customer_id,type,key)` 支撑 flag upsert 与 suggested 建议幂等；
- 抽取器输出侧 flag 过滤**已天然存在**（`validate_item` 只接受 profile/preference）——本阶段补入口抛错 + DB CHECK 两层纵深并固化测试（P4-UT-09）；
- 双模式探测（`build_memory_store`）、TTL maintenance、embedding 重试均为既有事实，衰退/治理逻辑与检索模式正交（仅 R16 的向量清除验证需 pgvector 容器）；
- 生产 compose 的 `memory-worker` 服务为既有部署形态，本阶段仅通过代码新增 kind/周期任务获得能力，**部署零变更**；新配置项全部有默认值，quickstart 无需改 compose。

## 3. 交接要点

### 3.1 给 dev（phase4-dev）

**工作范围**：T1~T11、T13（后端全部 + 前端 T11 + 文档修订）。所有提交在 `feat/agent-memory-phase-4`，提交规范与署名沿用仓库惯例。实现中遇到与本文件裁定冲突的现实，先找 lead 仲裁，不要擅自改口径。

**接口约定（qa 也以此为准）**：

```python
# modules/memory/pii.py（T3）
def mask(text: str) -> tuple[str, int]:
    """返回 (脱敏后文本, 命中次数)。纯函数，无外部依赖。
    模式（按序执行，均带 (?<!\\d)/(?!) 数字边界防误伤）：
    手机号 1[3-9]\\d{9} → 前3后2（138******78）；
    身份证 \\d{17}[\\dXx] → 前6后4；银行卡 \\d{16,19} → 后4；
    邮箱 → 本地名首字符 + ***@域名。身份证先于银行卡执行。"""

# modules/memory/decay.py（T6）
def effective_confidence(confidence, type, protected, last_referenced_at, now,
                         half_lives) -> float
    # protected 或 type='flag' → 原值（免疫）；否则 confidence * 2^(-age/半衰期)
def injection_filter_sql(...)  # active_items 的 WHERE 增项（R7，阈值等值保留）
async def daily_migration(engine, settings)  # profile→pending / preference→expired / 容量淘汰，逐变迁写审计（R8/R17）

# modules/memory/governance.py（T4）
async def upsert_flag(c, tenant, customer_id, key, content, source_type, source_ref)  # key 三值校验；extracted 直接 DomainError
async def remove_flag(tenant, actor, customer_id, key, source='human'|'rule')
async def delete_customer(tenant, actor, customer_id)      # R16 顺序 + 审计
async def merge_customers(tenant, actor, source_id, target_id)  # R16 冲突矩阵 + 审计
async def scan_overdue_tickets(engine, settings)           # R12 打标 + 30 天无复发解除（仅 source_type='rule'）

# modules/memory/service.py 增量（T5）
async def release_item(tenant, actor, item_id)   # protected=false + source_type='extracted'
async def active_flags(tenant, customer_id, limit)
def render_flag_injection(flags)                  # 【服务标记】段；blacklist 追加固定指令（R15）
# edit_item 置 protected=true；restore_item allowed+expired 并刷时钟；
# confirm_item 刷时钟 + :suggested 合并语义（R4/R19）；
# customer_detail items 增 protected/last_referenced_at/reference_count/effective_confidence

# reinforce 任务 payload（memory_tasks.kind='reinforce'，R11）
{"hits": {"<item_id>": <int>, ...}}   # 上限 200 id 截断；tenant 由 settings.tenant_id 认领

# runtime events（R10）
memory.injected            data={"items": n, "flags": n, "events": n}   # finish 同事务写
memory_search.completed    data={"hits": n}                             # PlatformTools emit
```

```python
# settings.py 新增（T2，env 前缀 AGENT_；默认值即 03 §配置 + R9）
memory_pii_masking_enabled: bool = True
memory_rule_scan_interval_minutes: int = Field(default=15, ge=0, le=1440)   # 0=停用
memory_flag_inject_max: int = Field(default=3, ge=1, le=10)
memory_blacklist_auto_handoff: bool = False
memory_decay_enabled: bool = True
memory_decay_half_life_days_profile: int = Field(default=365, ge=1, le=3650)
memory_decay_half_life_days_preference: int = Field(default=90, ge=1, le=3650)
memory_decay_half_life_days_event: int = Field(default=30, ge=1, le=3650)
memory_decay_min_confidence: float = Field(default=0.3, ge=0, le=1)
memory_max_items_per_customer: int = Field(default=200, ge=10, le=10000)
memory_reinforce_sample_rate: float = Field(default=1.0, ge=0, le=1)
```

**新增 API 端点（T10，授权沿用 Operator/Reader 先例）**：

| 端点 | 说明 |
|---|---|
| `DELETE /memory/customers/{cid}` | 删除客户（FR-4）；404 幂等语义：不存在即 404 |
| `POST /memory/customers/{cid}/merge` body `{target_customer_id}` | 合并（FR-5）；源=目标拒绝 400 |
| `PUT /memory/customers/{cid}/flags/{key}` body `{content}` | 打标/更新（key 三值，非法 400） |
| `DELETE /memory/customers/{cid}/flags/{key}` | 解除标记 |
| `POST /memory/items/{iid}/release` | 恢复自动管理（FR-1） |
| `GET /observability/memory` | `memory_summary()`（FR-7） |

**红线**：不得改动 Phase 1-3 既有行为（注入段顺序仅在尾部追加语义不变——flag 段在最前但无 flag 时渲染为 None，消息体与 Phase 3 逐字节一致；`memory_decay_enabled=false` 时全链路零衰退行为）；检索/注入/治理任何路径不得跨 tenant/customer；同步路径（工单钩子、run finish、send）只允许 INSERT memory_tasks/审计，不得同步调用模型；审计 payload 永不包含 PII 原值或记忆 content。

### 3.2 给 qa（phase4-qa）

**工作范围**：T12（测试实现与执行）、按 04 执行并产出 `06-test-report.md`（格式参照 phase-3 同名报告）；执行 T13 中 04 的 QA 侧修订（R1 依据行与 P4-CM-01 的 `0033`→`0037`、R7 的 P4-IT-10 措辞改写）。执行基线 HEAD 以 dev 完成提交为准；环境准备沿用 Phase 3 本机双容器（`postgres:17-alpine` 与 `pgvector/pgvector:pg17`），各自迁移至 head（0037）。

**验收红线（溯源编号）**：

- FR-1~FR-7 全部；AC-1~AC-13；04 的 P4-UT-01~13、P4-IT-01~18、P4-API-01~05、P4-CM-01~04；
- **P0 阻断（发现即停线报 lead）**：删除级联任何残留（含 embedding 列）；任何路径自动抽取覆盖人工条目；flag 经抽取路径写入；跨租户/跨客户数据泄露；审计 payload 出现 PII 原值；
- 衰退正确性：protected/flag 免疫有常驻断言（C6）；event 不参与置信度迁移与检索过滤（R7/R8）；阈值等值保留（P4-UT-11）；`memory_decay_enabled=false` 与 Phase 3 行为一致（AC-13/P4-IT-16）；
- 双模式：P4-IT-10 在 alpine/pgvector 两容器各跑一遍；P4-IT-18 向量清除在 pgvector 容器验证；
- 时间类用例一律改库构造（`last_referenced_at`/`expires_at`/`due_at`/`created_at`），手动触发单轮周期任务，不允许真实等待（04 §1 原则 4）；
- 回归：Phase 1-3 memory 全量（**含 P1-IT-15~21 租约/reaper、P3-UT-05 衰减只降权不隐藏、双模式 store 测试**）、工单状态机、前端三门槛 + Playwright 全绿；R18 一致性核对（protected 与 source_type 不发散）。

**与 dev 的接口对齐**：payload 结构、审计 action 命名（R17）、配置项默认值、端点清单、运行时事件名以本文件 §3.1 为准；发现实现与约定不符按缺陷提，不要私下协商改约定（约定变更须经 lead 记入本文件）。
