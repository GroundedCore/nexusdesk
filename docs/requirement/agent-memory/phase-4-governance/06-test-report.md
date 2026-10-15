# Phase 4 · 治理、衰退与标记记忆 — 测试报告

> 状态：已完成（双容器矩阵本地全绿；D1~D5 修复回归全部关闭；AC-1~13 复现通过；出口准则逐条满足）
> 创建：2026-10-15
> 依据：`04-test-plan.md`（P4-UT-01~13、P4-IT-01~18、P4-API-01~05、P4-CM-01~05、§8 出口准则，含 QA 按 R7/R8/R18 裁定修订）、`02-requirements.md`（FR-1~FR-7、AC-1~AC-13）、`08-implementation-breakdown.md`（R1~R23 裁定）
> 执行：QA 测试代理（自动化 + 直接改库时间构造 + 代码复核），开发自报结果一律独立复跑验证

## 1. 测试范围与环境

**范围**：三类服务标记（vip/complaint_risk/blacklist）CRUD 与注入优先、"人工 > 自动"保护与 `:suggested` 建议队列、PII 四写入口脱敏、客户删除/合并治理（含 R22 工单级联改写）、内置规则扫描（超时工单打标/解除）、置信度衰退（半衰期/effective_confidence/阈值迁移/容量淘汰）、引用强化（reinforce 任务）、观测指标（memory_summary + `/observability/memory`）、迁移 `0037_memory_governance`、治理控制台前端。

| 环境 | 配置 | 用途 |
|---|---|---|
| 本机（macOS，uv 管理 Python） | 无 PG 直接执行 | ruff 全量；非 postgres 单测（PII 纯函数、衰退公式、配置加载、抽取器过滤） |
| 本机 Docker PG A（text 模式） | 容器 `nexusdesk-test-pg`（postgres:17-alpine，127.0.0.1:55432），库 `nexusdesk_test` 迁移至 head（0037） | text 模式全量用例；全量后端回归 |
| 本机 Docker PG B（vector 模式） | 容器 `nexusdesk-test-pgvector`（pgvector/pgvector:pg17，127.0.0.1:55433），库 `nexusdesk_test` 迁移至 head（0037） | vector 模式全量用例；P4-IT-18 向量残留核验；全量后端回归 |
| 模型 | demo/mock（`_CaptureModel` 捕获注入文本、`RecordingGateway`） | 全部自动化用例 |

**过程备注**：① 时间类用例（衰退、TTL、规则扫描 30 天窗口、容量淘汰梯度）一律直接 `UPDATE last_referenced_at/expires_at/created_at/due_at` 构造，周期任务（daily_migration、规则扫描、maintenance）手工触发单轮，无真实等待（04 §1 原则 4）。② 与 dev 共享 worktree，首波基线跑批遭遇 dev 写入竞态（2 例 0036 迁移测试失败），隔离复跑双容器全绿，不计缺陷。③ P4-IT-11 初版构造 141 天（0.9 置信 preference 实际有效分 0.304 仍在阈值上），按衰退公式修正为 144 天（临界点 ≈142.65 天）后通过——属测试构造错误，非实现缺陷。④ 执行期间 lead 两项裁定落地（96efdcd）：R22（merge 必须级联改写 `tickets.customer_id`）与 R23（embedded worker 门控五开关扩展 + reinforce 投递挂 `memory_decay_enabled`），P4-IT-08/IT-16 相应增断言、P1-UT-12 由 dev 更新后 QA 复跑确认。⑤ 前端 e2e 首轮 9 例全失败为执行前提缺失（vite dev server 未启动，ERR_CONNECTION_REFUSED），启动后复跑 9/9，缓存/前提期失败不计入结果。

## 2. 执行总览

| 项 | 结果 | 证据 |
|---|---|---|
| 本机 `uv run ruff check .`（HEAD `50080dc`） | All checks passed | 附录 A-1 |
| P4 套件 · text 模式（alpine 容器，`50080dc`） | **56 passed, 1 skipped, 0 failed**（skip 为 P4-IT-18 vector 专属，按设计门控） | 附录 A-2 |
| P4 套件 · vector 模式（pgvector 容器，`50080dc`） | **57 passed, 0 skipped, 0 failed** | 附录 A-3 |
| 全量后端回归 · alpine 容器（`50080dc`，独占机器单次完整跑批） | **490 passed, 15 skipped, 1 deselected（预存 flake 单独计次）, 0 failed** | 附录 A-4 |
| 全量后端回归 · pgvector 容器（`50080dc`，独占机器单次完整跑批） | **502 passed, 3 skipped, 1 deselected（预存 flake 单独计次）, 0 failed** | 附录 A-5 |
| 前端 `npm run typecheck` / `check:i18n` / `build` | 全过（i18n 1344 keys × 4 语言，较 Phase 3 +56 即本阶段新增；build 仅 chunk 体积警告） | 附录 B-1 |
| Playwright memory.spec.ts | **9/9 通过**（含本阶段新增 5 例：治理标记/删除/合并/建议队列/观测页/客户已删除降级） | 附录 B-2 |
| Playwright tickets-antd.spec.ts（存量核对） | 失败特征与 Phase 2/3 已记录预存类别一致（登录态等待超时），零新增 | 附录 B-3 |

P4 套件 = `test_memory_governance.py`（本机单测）+ `test_memory_governance_postgres.py`（PG 集成）+ `test_memory_governance_api.py`（API）+ `test_memory_governance_migrations.py`（迁移，双容器各一遍），共 55 个测试函数、参数化展开 57 例；另含 Phase 2 既有文件 `test_memory_customers_postgres.py` 中按 R4 契约更新的 1 例回归用例。双容器各执行一遍。

## 3. 单元测试（P4-UT）逐条结果

全部通过。DB 绑定用例在双容器各执行一遍。

| 编号 | 用例 | 测试位置 | 执行环境 | 结果 |
|---|---|---|---|---|
| P4-UT-01 | 手机号脱敏（前 3 后 2） | `test_memory_governance.py::test_p4_ut_01_phone_masked_front3_back2` | 本机 | 通过 |
| P4-UT-02 | 身份证脱敏（前 6 后 4，含 X 后缀） | `test_p4_ut_02_id_card_masked_front6_back4_including_x_suffix` | 本机 | 通过 |
| P4-UT-03 | 银行卡脱敏（仅后 4；身份证优先于银行卡模式） | `test_p4_ut_03_bank_card_masked_back4_only` | 本机 | 通过 |
| P4-UT-04 | 邮箱脱敏（本地首字符 + ***@域名） | `test_p4_ut_04_email_masked_local_first_char` | 本机 | 通过 |
| P4-UT-05 | 误伤对照（非 PII 文本不动）+ 命中计数 | `test_p4_ut_05_false_positive_control_and_hit_count` | 本机 | 通过 |
| P4-UT-06 | `memory_pii_masking_enabled` 默认开 + `AGENT_` 覆盖 | `test_p4_ut_06_pii_switch_default_on_and_env_override` | 本机 | 通过 |
| P4-UT-07 | protected 条目被抽取更新 → `:suggested` pending 建议（base key 截断 89） | `test_memory_governance_postgres.py::test_p4_ut_07_protected_update_parks_suggested_pending` | 双容器 | 通过 |
| P4-UT-08 | release 恢复自动管理（protected=false + source_type='extracted'） | `test_p4_ut_08_release_restores_automatic_management` | 双容器 | 通过 |
| P4-UT-09 | flag 双闸门：抽取器输出侧 validate_item 拒绝 + 写路径拒绝 source_type='extracted' | `test_memory_governance.py::test_p4_ut_09_extractor_flag_output_filtered` + `test_p4_ut_09_flag_write_paths_reject_extracted` | 本机 + 双容器 | 通过 |
| P4-UT-10 | 衰退公式（age=0 不衰减；半衰期减半；flag/protected 免疫） | `test_p4_ut_10_age_zero_keeps_confidence` / `_half_life_halves_confidence` / `_flag_and_protected_immune`（3 例） | 本机 | 通过 |
| P4-UT-11 | 阈值边界（R8：等值保留；364 天 0.6 置信 profile≈0.3004 保留、366 天≈0.2994 过滤） | `test_p4_ut_11_threshold_boundary` | 本机 | 通过 |
| P4-UT-12 | 合并冲突矩阵（人工胜抽取；同源性后写胜；删除侧不参与） | `test_p4_ut_12_merge_conflict_matrix` | 双容器 | 通过 |
| P4-UT-13 | reinforce 上限 200 id 截断；刷新时钟与计数、永不动置信度 | `test_p4_ut_13_reinforce_payload_capped_at_200_ids` / `_reinforce_resets_clock_never_confidence` | 双容器 | 通过 |

## 4. 集成测试（P4-IT）逐条结果

全部位于 `test_memory_governance_postgres.py`，双容器各执行一遍，全部通过（P4-IT-18 在 alpine 按设计 skip）。

| 编号 | 用例 | 测试位置 | 结果 |
|---|---|---|---|
| P4-IT-01 | 人工编辑存续 + 建议队列闭环（编辑置 protected；抽取再更新不覆盖、产 `:suggested` pending；确认后合并到人工行、prev_content 留存、建议物理删除） | `test_p4_it_01_human_edit_survives_and_suggestion_reaches_console` | 通过（双容器） |
| P4-IT-02 | PII 四写入口（抽取/人工编辑/flag 备注/事件写入）全部落库脱敏；全库无明文（content+prev_content+审计载荷正则扫描）；命中数入审计 details；开关关闭时原文落库（R21 新写入口径） | `test_p4_it_02_pii_masked_at_every_write_point` + `test_p4_it_02_pii_switch_off_persists_original` | 通过（双容器） |
| P4-IT-03 | flag 注入段最前（`【服务标记】` 先于画像/偏好/待跟进事件）；不占 20 条画像预算；独立上限 `memory_flag_inject_max`；无 flag 时注入体与 Phase 3 逐字节一致 | `test_p4_it_03_flags_inject_first_and_have_their_own_budget` + `test_p4_it_03_no_flags_keeps_phase3_message_body` | 通过（双容器） |
| P4-IT-04 | blacklist 注入固定指令行"谨慎服务，涉承诺一律转人工确认"；默认开关不转人工（run 正常执行）；开启后消息落库 + 会话 waiting + handoff（原因"黑名单客户自动转人工"）、不提交 run | `test_p4_it_04_blacklist_injects_instruction_no_handoff_by_default` + `test_p4_it_04_blacklist_auto_handoff_when_enabled` | 通过（双容器） |
| P4-IT-05 | 规则扫描：超时工单 → rule 源 complaint_risk（source_ref=最早工单）；复扫全幂等（D3/D5 后：marked=0、revision=1、无新审计）；30 天干净自动解除；人工同 key 标记永不被规则触碰 | `test_p4_it_05_rule_scan_marks_clears_and_respects_human_flags` + `test_p4_it_05_rule_scan_never_touches_human_flag` | 通过（双容器） |
| P4-IT-06 | 删除级联：items/identities/customer 物理清除；会话 customer_id 置 NULL 历史保留；工单字符串引用不变（R16，与 R22 合并场景区分）；审计仅操作者与计数；同外部标识再进线重建为新客户 | `test_p4_it_06_delete_customer_cascades_and_reentry_rebuilds` | 通过（双容器） |
| P4-IT-07 | 删除性能：容量上限 200 条单客户删除 ≤2s（本地 3 样本实测 max 0.037s，余量约 54 倍） | `test_p4_it_07_delete_customer_performance` | 通过（双容器） |
| P4-IT-08 | 合并全量：identities/会话改挂、非冲突条目迁移、人工侧胜冲突、源客户消失、审计含取舍明细且不含内容；source==target 拒绝、目标缺失整事务回滚；**R22**：事务内级联改写 `tickets.customer_id`、审计 `tickets_relinked`、改写后 resolved 事件正常写入目标客户、规则扫描覆盖这些工单 | `test_p4_it_08_merge_repoints_deletes_source_and_audits` / `_rejects_same_customer_and_rolls_back` / `_relinks_tickets_and_keeps_memory_flowing`（3 例） | 通过（双容器） |
| P4-IT-09 | 每日迁移按类型路由（R8）：preference 200 天 → expired；profile 400 天低于阈值 → pending 再确认；protected 500 天与 flag 500 天免疫；event 不参与置信度迁移；每次迁移带迁移前有效分审计 | `test_p4_it_09_daily_migration_routes_by_type_and_audits` | 通过（双容器） |
| P4-IT-10 | 注入过滤即时生效（R7：不等每日任务，查询侧 SQL 过滤）；`memory_decay_enabled=false` 注入与 Phase 3 一致；双检索模式各自验证 | `test_p4_it_10_injection_filter_applies_before_any_daily_run` + `test_p4_it_10_decay_off_keeps_phase3_injection` | 通过（双容器） |
| P4-IT-11 | 同键 upsert 重置衰退时钟（L1）：144 天老龄条目被客户再提及后时钟刷新、恢复注入（构造口径见 §1 备注③） | `test_p4_it_11_same_key_upsert_resets_decay_clock` | 通过（双容器） |
| P4-IT-12 | 强化投递：L3 重叠（CJK bigram）在 run 完成事务内落 reinforce 任务；`memory.injected` 事件携带分区计数 `{items,flags,events}`；`memory_reinforce_sample_rate=0` 不投递；任务批量刷新时钟与引用计数 | `test_p4_it_12_l3_overlap_enqueues_reinforce_at_run_finish` + `test_p4_it_12_reinforce_task_batches_clock_and_count_updates` | 通过（双容器） |
| P4-IT-13 | 热路径零写入：run 执行期间条目行不变（时钟/计数原样）；刷新只发生在 memory worker 执行任务时 | `test_p4_it_13_reinforcement_never_writes_on_the_hot_path` | 通过（双容器） |
| P4-IT-14 | 容量淘汰：超上限按 effective_confidence 从低到高软过期（status='expired'，D2 修复口径）至上限内；protected 全部保留；审计含淘汰明细（有效分）；恢复后时钟刷新不再立即复汰（无复活-复死循环） | `test_p4_it_14_capacity_eviction_soft_expires_lowest_scores` | 通过（双容器） |
| P4-IT-15 | expired 恢复：restore 刷新时钟、保持 active、下一轮迁移不再迁移；deleted 恢复不刷新时钟（R19 refresh_on={"expired"}）；confirm_item 刷新时钟 | `test_p4_it_15_expired_restore_refreshes_clock_and_stays_active` | 通过（双容器） |
| P4-IT-16 | AC-13 开关 parity：worker 层 decay off 时 maintenance 与 Phase 3 一致（仅 TTL）；**R23(b)**：decay off 时投递侧无 reinforce 任务、L3 不刷新 last_referenced_at；L1 同键 upsert 刷新不受门控 | `test_p4_it_16_decay_disabled_skips_maintenance_migration` + `test_p4_it_16_decay_disabled_gates_reinforce_dispatch` | 通过（双容器） |
| P4-IT-17 | 观测聚合：构造已知数据集后 `memory_summary()` 各分区一致（抽取量按日/命中率/积压/治理计数/衰退计数/reinforce 队列/PII 命中）；HTTP 面 `GET /observability/memory` viewer 200、匿名 401、响应与服务层同形（D4 回归守卫） | `test_p4_it_17_memory_summary_aggregates_constructed_data` | 通过（双容器） |
| P4-IT-18 | 向量随行消亡：删除客户后全库无残留 embedding（pgvector 专属；text 模式无 embedding 列按设计 skip） | `test_p4_it_18_vector_column_cleared_with_customer_delete` | 通过（pgvector；alpine skip） |

## 5. API 测试（P4-API）逐条结果

全部位于 `test_memory_governance_api.py`，双容器各执行一遍，全部通过。

| 编号 | 用例 | 结果 |
|---|---|---|
| P4-API-01 | 删除客户：operator 200、viewer 403、匿名 401；响应仅计数不含客户数据；审计写入；重复删除 404 | 通过（双容器） |
| P4-API-02 | 合并客户：冲突取舍明细入审计；源=目标 400；viewer 403 | 通过（双容器） |
| P4-API-03 | flag CRUD：非法 key 400（仅 vip/complaint_risk/blacklist）；viewer 只读；备注 PII 脱敏；客户详情可读；DELETE 软删 + 审计 | 通过（双容器） |
| P4-API-04 | release / expired restore：状态与 protected 标志按契约翻转；恢复刷新时钟；viewer 403 | 通过（双容器） |
| P4-API-05 | 租户隔离：外部租户客户行直连 INSERT 后，全部治理动词 404（删除/合并/flag 写/flag 删/合并入外部目标）；服务层跨租户调用抛 DomainError 且零副作用 | 通过（双容器） |

## 6. 通用核对项（P4-CM）逐条结果

| 编号 | 核对项 | 测试位置/方式 | 结果 |
|---|---|---|---|
| P4-CM-01 | 迁移 0037：protected 列（NOT NULL DEFAULT false）+ 存量回填（source_type='human' → protected=true）+ flag key CHECK（三法定 key 放行、非法拒绝 CheckViolationError、非 flag key 自由）+ downgrade/re-upgrade 干净 | `test_memory_governance_migrations.py::test_0037_protected_column_backfill_and_flag_check` | 通过（双容器） |
| P4-CM-02 | 11 项新配置默认值与 `AGENT_` 覆盖 | `test_memory_governance.py::test_p4_cm_02_governance_settings_defaults_and_env_override` | 通过（本机） |
| P4-CM-03 | R17 审计覆盖扫描：customer.deleted/merged（IT-06/08）、flag.upserted/removed（IT-05/15）、item.released（IT-01/UT-08）、decay_pending/decay_expired（IT-09）、evicted（IT-14）各自用例内断言；reinforce.failed 由专项用例补齐（崩溃执行器 → 退避 → attempts 上限置 failed + kind 专属审计） | `test_p4_cm_03_reinforce_task_failure_audits_kind_specific_action` | 通过（双容器） |
| P4-CM-04 | 前端治理控制台（T11）：flag 卡片组、删除 Popconfirm 二次确认、合并警示对话框、条目表 effective_confidence/last_referenced_at/人工保护标识、PendingQueue `:suggested` 标识、工单"客户已删除"降级、观测页记忆卡片区 | 前端三门槛（附录 B-1）+ Playwright memory.spec.ts 9/9（附录 B-2）+ 代码复核（i18n 四语 56 新 key 全覆盖、client.ts 类型齐全） | 通过 |
| P4-CM-05 | protected ⇔ source_type='human' 不变量（R18）：编辑/release/flag 写入/衰退轮次/合并五个检查点零漂移行 | `test_p4_cm_05_protected_invariant_holds_across_governance_paths` | 通过（双容器） |

## 7. 前端

- 三门槛全绿：tsc 无错误；`Verified 1344 translations in zh-CN, zh-TW, en, hi`（Phase 3 为 1288，+56 即本阶段 T11 新增 key，四语齐）；build 成功（仅 chunk 体积警告）（附录 B-1）。
- Playwright memory.spec.ts **9/9**（附录 B-2）：既有 4 例（控制台 admin/viewer、注入、US-3 抽屉）+ 本阶段新增 5 例（治理 flag/保护解除/expired 恢复/删除客户；合并确认对话框；`:suggested` 建议标识；工单"客户已删除"降级；观测页记忆治理区）。
- tickets-antd.spec.ts 2 例失败为 Phase 2 起已记录的预存类别（登录态 90s 超时特征），dev 在未改代码 HEAD 复现一致，QA 独立复跑确认同特征、零新增（附录 B-3）。

## 8. 回归范围

- 全量后端回归 · alpine 容器（text 模式，`50080dc`，独占机器）：**490 passed / 15 skipped / 1 deselected / 0 failed**（附录 A-4）。
- 全量后端回归 · pgvector 容器（vector 模式，`50080dc`，独占机器）：**502 passed / 3 skipped / 1 deselected / 0 failed**（附录 A-5）。
- 回归覆盖要点：Phase 1-3 memory 全套（摘要注入、画像/偏好、worker claim/租约/停机重置、事件生命周期、memory_search 双模式）、P1-UT-12 门控扩展更新版（R23(a)：五开关任一启动；全关场景显式置 decay=false + rule_scan=0，dev 更新后 QA 复跑通过）、全部迁移用例（alembic 单头 `0037_memory_governance`）、Decay D1 修复后 Phase 2 注入路径回归（`test_p2_it_08` 等）。
- deselected 为 main 侧预存 flake `test_model_gateway.py::test_timeout_and_cancellation_release_slots`（Phase 2 R2 / Phase 3 R2 同款，本分支未触碰其代码路径），按先例 deselect 跑批。
- 共享 worktree 竞态说明：首波基线跑批与 dev 写入并发出现 2 例 0036 迁移测试瞬态失败，隔离复跑双容器全绿，不计入结果（§1 备注②）。

## 9. 缺陷与残留风险

| # | 级别 | 缺陷 | 发现环节 | 修复 | 回归 |
|---|---|---|---|---|---|
| D1 | **P0** | 衰退 SQL 在 asyncpg 下 CASE 分支绑定参数被推断为 text → `operator does not exist: text * numeric`：默认配置下 `active_items` 注入查询崩溃（Phase 2 `test_p2_it_08` 回归失败）且 `daily_migration` 全灭 | 首波 P4 套件 + Phase 2 注入回归；psql PREPARE 隔离复现（无类型失败、numeric 显式类型通过） | `125f3db`（显式 `CAST AS numeric`） | 已关闭：注入回归 + 衰退 SQL 全路径复测通过，终局双容器 0 failed |
| D2 | P1 | `_evict_over_capacity` 置 status='deleted'（FR-6.3 应为 'expired'），且 deleted 恢复不刷新时钟 → 恢复后立即复汰的"复活-复死"循环 | P4-IT-14 构造复现（5 protected + 205 条 0-204 天梯度） | `125f3db`（软过期口径修正） | 已关闭：10 条最老淘汰为 expired、审计含有效分、protected 全保留、恢复后时钟刷新不复汰（IT-14 常驻） |
| D3 | P2 | 规则扫描每轮对未变化 flag 重复 upsert：revision+1 且新增 `memory.flag.upserted` 审计（15 分钟默认间隔 ≈ 96 行/天/客户），扭曲 FR-7 治理计数 | P4-IT-05 三轮扫描构造 | `779cca9`（内容/source_type/source_ref 全一致的 upsert 为 no-op：不 UPDATE、不审计） | 已关闭：复扫 revision=1、无新审计（IT-05 常驻） |
| D4 | P1 | `memory_summary()` reinforce 聚合误用 SQLite 函数 `jsonb_object_length`（PG 不存在）→ `GET /observability/memory` 每次调用 500 | P4-IT-17（alpine 容器 asyncpg UndefinedFunctionError）；psql 复现 + `jsonb_object_keys` 替代验证 | `ec504d3`（改为 `jsonb_object_keys` 子查询计数，对 legacy NULL payload 安全） | 已关闭：IT-17 服务层 + HTTP 面双断言常驻，双容器通过 |
| D5 | P3 | D3 修复后扫描返回计数 `marked` 仍把 no-op 尝试计为 1（语义残留；当时唯一消费者为 worker 日志，无生产影响） | P4-IT-05 幂等断言复测 | `afe2d8f`（upsert_flag 幂等命中返回 None，marked 只计真实新打标；控制台包装层回读既有行，HTTP 契约不变） | 已关闭：复扫 marked==0（IT-05 常驻） |

| # | 级别 | 残留风险/观察 | 处置 |
|---|---|---|---|
| R1 | P3（预存） | tickets-antd.spec.ts 2 例 e2e 失败为 Phase 2 起记录的登录态超时类别，本分支零新增（dev 未改代码 HEAD 复现 + QA 独立复跑同特征） | 记录，非本阶段缺陷 |
| R2 | P3（预存 flake） | main 侧 `test_model_gateway.py::test_timeout_and_cancellation_release_slots` 挂起形态同 Phase 2/3 记录；建议后续给 `entered.wait()` 加超时（Phase 3 报告已提案） | 全量回归 deselect 计次（§8），非本阶段缺陷 |
| R3 | P3（已知限制） | R21 裁定：存量数据不回溯脱敏，PII"≥99% 覆盖率"口径为验收期构造数据 + 上线后新写入；存量明文如需合规清理另行立项 | 记录（08 R21 裁定遗留项，不阻塞本阶段） |
| R4 | P3（后续项） | CI 尚无 pgvector 矩阵 job（Phase 3 R13 裁定不阻塞验收，仍待补）；本地双容器矩阵已全绿 | 后续 PR 增加 `matrix.image`，PR 描述说明 |

## 10. AC 复现映射与出口准则核对

AC-1~13 全部实测复现通过：

| AC | 场景 | 覆盖用例 | 结果 |
|---|---|---|---|
| AC-1 | 人工改 name 后客户再自报不同名 → 不覆盖 + pending 建议 | P4-IT-01（+ Phase 2 R4 契约回归用例） | 通过 |
| AC-2 | vip 标记进线 → 注入段最前 | P4-IT-03 | 通过 |
| AC-3 | 工单超期 → 自动 complaint_risk；30 天无复发自动解除 | P4-IT-05 | 通过 |
| AC-4 | 抽取含手机号 → 138\*\*\*\*\*\*78 形态，全库无明文 | P4-UT-01~06 + P4-IT-02 | 通过 |
| AC-5 | 客户删除 → 无残留、会话置 NULL、审计完整、再进线为新客户 | P4-IT-06 + P4-API-01 + P4-IT-18（向量残留） | 通过 |
| AC-6 | 合并（人工 vs 抽取同键冲突）→ 人工胜、源消失、审计含取舍 | P4-IT-08×3 + P4-UT-12 + P4-API-02 | 通过 |
| AC-7 | 观测页记忆指标正常展示 | P4-IT-17（服务+HTTP）+ P4-CM-04（e2e 观测区） | 通过 |
| AC-8 | preference 200 天（半衰期 90）→ 跌破阈值不注入、expired 可恢复 | P4-IT-09/10 + P4-UT-10 | 通过 |
| AC-9 | 期间同键再提及 → 时钟重置继续注入 | P4-IT-11 | 通过 |
| AC-10 | protected 500 天 → 不衰减不淘汰正常注入 | P4-IT-09 + P4-UT-10 + P4-CM-05 | 通过 |
| AC-11 | profile 跌破阈值 → pending 再确认而非 expired | P4-IT-09（R8 路由） | 通过 |
| AC-12 | 超硬上限 → 按有效分从低到高淘汰至上限内、审计含明细 | P4-IT-14 | 通过 |
| AC-13 | `memory_decay_enabled=false` → 行为与 Phase 3 一致 | P4-IT-10 + P4-IT-16×2（含 R23(b) 投递门控） | 通过 |

出口准则（04 §8）逐条：

| 准则 | 状态 |
|---|---|
| 全部用例通过 | 满足：P4 套件 text 56/vector 57 passed，0 failed（skip 均为模式门控） |
| AC-1~AC-13 复现通过 | 满足（上表） |
| "人工 > 自动" 阻断项 | 满足：P4-IT-01/UT-07/CM-05 + R4 契约回归，抽取永不覆盖人工条目 |
| 删除级联完整性 阻断项 | 满足：P4-IT-06/IT-18，含 embedding 残留核验（pgvector）与审计计数 |
| PII 不可逆 阻断项 | 满足：P4-IT-02 全库正则扫描（content+prev_content+审计载荷）零明文；四写入口全覆盖 |
| Phase 1-3 回归套件全绿 | 满足：全量回归双容器 0 failed（§8） |
| 无 P0/P1 未关闭 | 满足：P0 的 D1 与 P1 的 D2/D4 均修复并回归关闭（§9） |

## 附录 A 后端执行输出快照

A-1 `uv run ruff check .`（`50080dc`）

```text
All checks passed!
```

A-2 P4 套件 · text 模式（alpine 容器 55432，`50080dc`，含 MIGRATION_TEST_DATABASE_URL）

```text
56 passed, 1 skipped in 47.03s
（skip：P4-IT-18 text 模式无 embedding 列，按设计门控）
```

A-3 P4 套件 · vector 模式（pgvector 容器 55433，`50080dc`，含 MIGRATION_TEST_DATABASE_URL）

```text
57 passed in 45.22s
```

A-4 全量后端回归 · alpine 容器（`50080dc`，独占机器；deselect = R2 预存 flake 用例）

```text
490 passed, 15 skipped, 1 deselected, 2 warnings in 484.74s (0:08:04)
```

A-5 全量后端回归 · pgvector 容器（`50080dc`，独占机器；deselect = R2 预存 flake 用例）

```text
502 passed, 3 skipped, 1 deselected, 2 warnings in 452.07s (0:07:32)
```

## 附录 B 前端执行输出快照

B-1 `npm run typecheck && npm run check:i18n && npm run build`（`50080dc`）：tsc 无错误；`Verified 1344 translations in zh-CN, zh-TW, en, hi`；build 成功（仅 chunk 体积警告）。

B-2 `npx playwright test memory.spec.ts`（vite dev server 5173 + chromium headless）：

```text
9 passed (32.1s)
```

含本阶段新增：governance flag/保护解除/expired 恢复/删除客户、合并确认对话框、pending 队列 `:suggested` 标识、工单详情"客户已删除"降级、观测页记忆治理卡片区。过程备注：首轮 9 例全失败为 vite dev server 未启动（ERR_CONNECTION_REFUSED，执行前提缺失），启动后复跑 9/9，前提期失败不计入结果。

B-3 `npx playwright test tickets-antd.spec.ts`：2 例失败，特征为登录态等待超时（与 Phase 2 报告 §8 / Phase 3 报告附录 B-2 记录的预存类别同用例同特征；dev 在未改代码 HEAD 复现一致），本分支零新增 e2e 失败。
