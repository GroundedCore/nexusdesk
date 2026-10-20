# Phase 6 · 客户记忆可视化与注入可解释性 — 测试报告

> 执行日期：2026-10-19（后端/前端第一轮）；2026-10-19（验收轮）；2026-10-20（本报告复核复跑）
> 依据：`04-test-plan.md`（P6-UT / P6-IT / P6-API / P6-FE 用例集）、`02-requirements.md`（AC-1~AC-12）、`05-acceptance-plan.md`（C1~C5）
> 测试对象：工作区未提交的 Phase 6 实现（注入明细落库 + 三层可视化视图）
> 执行方式：独立子代理全流程执行（后端实现 → 前端实现 → 测试 → 验收），本报告忠实记录全流程数据；报告撰写阶段对关键数字做了复核复跑（标注"复核"），不含杜撰内容。
> **当前状态**：后端 PG 全量 568 passed / 非 PG 全量 273 passed / 前端在册 e2e 26/26 passed；AC-1~AC-12 与 C1~C5 全部通过；无 P0/P1 未关闭；**测试通过出口准则（有条件通过：2 项 P3 + 1 项 P2 遗留，见第 8 节）**。

---

## 1. 测试环境

| 环境 | 配置 | 用途 |
|---|---|---|
| 后端单测 | `backend/.venv/bin/pytest tests/ -q` | P6-UT 与全量非 PG 回归 |
| 后端 PG 集成 | 容器 `nexusdesk-p5-pg`（pgvector/pg17，alembic head 已确认 = `0039_memory_injection_log`），`TEST_DATABASE_URL=postgresql+asyncpg://agent:agent_dev@127.0.0.1:5433/agent_platform` | P6-IT / P6-API 与 Phase 1-5 PG 回归 |
| 迁移双向校验 | 同一容器 `alembic upgrade head` → `\d memory_injection_log`（表/3 索引/section CHECK 齐全、无外键，符合 D1）→ `downgrade 0038` → 再 `upgrade head` | 0039 迁移可升可降 |
| 前端类型检查 | `npx tsc --noEmit`（复核：零错误） | 静态类型 |
| 前端 i18n | `node scripts/check-i18n.mjs`（复核：Verified 1487 translations in zh-CN, zh-TW, en, hi） | 新增 47 key 四语同步 |
| 前端 e2e | Playwright，临时启动 `npm run dev`（5173 端口，HTTP 200 后执行，测完已停止） | P6-FE-01~07 与在册回归 |
| 基线对照 | 临时 git worktree（HEAD，符号链接 node_modules，5199 端口），对照完成后已删除 | 判定前端 28 条失败用例归属（历史既有失败） |
| 独立对抗探针 | `/tmp/p6_accept_verify.py`（非仓库文件，自建租户驱动真实 run 后直查 DB 与接口，数据已自清） | 验收轮 AC/C 项交叉验证 |

## 2. 执行轮次总览

共执行 **3 个测试轮次**（后端第一轮 → 前端第一轮 → 验收轮），另有本报告复核复跑。修复全部发生在轮次之间，见第 6 节。

| 轮次 | 维度 | 结果 | 备注 |
|---|---|---|---|
| 后端第一轮 | P6 单测 `test_memory_injection.py` | **通过**：8 passed | 无 PG |
| 后端第一轮 | P6 PG `test_memory_injection_postgres.py` | 首轮 17/18，**P6-IT-03 性能断言失败 1 次**（62.7ms > 50ms）；单测复跑 3 次 + 全套件 1 次 + 单测再 5 次共 9 次全部通过 | 测量抖动，记 P3 缺陷 D2，见 6.1 |
| 后端第一轮 | PG 全量回归 | **通过**：568 passed, 20 skipped | 20 skipped 经 `-rs` 确认全为环境性跳过（CREATEDB 角色迁移测试 / opt-in Milvus / 无 embedding 列模式），与 Phase 6 无关 |
| 前端第一轮 | P6-FE-01~07（`memory-visualization.spec.ts`） | **通过**：7/7 | mock API |
| 前端第一轮 | 在册回归 `memory.spec.ts` + `agent-memory-config.spec.ts` | **通过**：19/19 | 合并复跑 26/26 |
| 前端第一轮 | 更大范围 mock 套件（21 spec / 89 用例） | 61 passed, **28 failed——全部为历史既有失败**，基线 worktree（HEAD）上以相同方式复现 | 与 Phase 6 无关，见 6.2 |
| 验收轮 | AC-1~AC-12 / C1~C5 + 独立探针 | **全部通过**（verdict=conditional，遗留见第 8 节） | 证据见第 5、7 节 |
| 复核（2026-10-20） | 后端 PG 全量 | **通过**：568 passed, 20 skipped in 577.89s | 与后端第一轮数字一致 |
| 复核（2026-10-20） | 后端非 PG 全量 | **通过**：273 passed, 315 skipped | 见 3.1 口径修正 |
| 复核（2026-10-20） | 前端 3 个在册 spec | **通过**：26 passed (2.2m) | tsc / check-i18n 同步复核通过 |
| 复核（2026-10-20） | `ruff check .` | **通过**：All checks passed | |

## 3. 后端测试结果

### 3.1 全量套件

- 非 PG：`.venv/bin/pytest tests/ -q` → **273 passed, 315 skipped**（复核实测）。
  - **口径修正**：后端实现阶段的自验记录为 "265 passed, 297 skipped"，该数字产生于 Phase 6 测试文件（8 个非 PG 用例）入库之前；当前工作区实际收集 588 条用例（`--collect-only` 复核），非 PG 口径应为 273 passed / 315 skipped。报告以此为准。
- PG 全量：`TEST_DATABASE_URL=... .venv/bin/pytest tests/ -q` → **568 passed, 20 skipped**（后端第一轮与复核复跑两次一致；588 collected = 568 + 20，与收集数完全咬合）。
  - **口径修正**：验收轮证据中另有一处 "全量回归 550 passed, 20 skipped"（合计 570），与收集数 588 差 18 条、恰为 `test_memory_injection_postgres.py` 全文用例数，判定为该轮记录口径不含本文件或转写误差；以两次实测的 **568 passed** 为准。
- 2 个 warnings 为既有噪音（starlette anyio BlockingPortal DeprecationWarning；asyncpg 连接 GC 回收 SAWarning），不影响结果。

### 3.2 P6 单测明细（`tests/test_memory_injection.py`，8/8 通过）

| 计划编号 | 测试函数 | 结果 |
|---|---|---|
| P6-UT-01 | `test_p6_ut_01_memory_injection_text_unchanged_and_entries_listed`（含 profile/preference 渲染） | 通过 |
| P6-UT-01 | `test_p6_ut_01_flag_injection_text_unchanged_and_entries_listed`（blacklist 固定指令行不落入 injected） | 通过 |
| P6-UT-01 | `test_p6_ut_01_event_injection_text_unchanged_and_entries_listed` | 通过 |
| P6-UT-02 | `test_p6_ut_02_scores_follow_section_rules`（profile/preference 透传 effective_confidence、缺省降级 None、flag/event 恒 NULL） | 通过 |
| P6-UT-02 | `test_p6_ut_02_worker_collects_log_entries_and_switch_gate`（worker 层 `_memory_injection_log` 收集；开关关闭只丢明细、保留 injected 计数） | 通过 |
| P6-UT-05 | `test_p6_ut_05_empty_renderers_log_nothing`（空渲染 (None, [])） | 通过 |
| P6-UT-05 | `test_p6_ut_05_no_injection_leaves_no_log_context`（无 memory 服务/无 customer/读关闭路径） | 通过 |
| P6-UT-05 | `test_p6_ut_05_customer_without_items_collects_nothing` | 通过 |

P6-UT-03 / P6-UT-04 按其实现位置（需真实库）落在 PG 文件，见 3.3。

### 3.3 P6 PG 集成与 API 明细（`tests/test_memory_injection_postgres.py`，18/18 通过）

| 计划编号 | 测试函数 | 结果 | 证据要点 |
|---|---|---|---|
| P6-IT-01 | `test_p6_it_01_run_writes_one_row_per_injected_item` | 通过 | 明细行数 = run × item（含 flag）；score 经 365 天回拨验证 = SQL 算出的 effective_confidence ≈ 0.45；injected_at 落在 finish 事务窗口内 |
| P6-IT-02 | `test_p6_it_02_event_counts_and_item_ids_match_detail_rows` | 通过 | items/flags/events 计数与明细行数一致；item_ids 集合 = 明细 item_id 集合 |
| P6-IT-03 | `test_p6_it_03_batch_insert_is_single_statement_within_budget` | 通过（首轮抖动 1 次，见 6.1） | SQLAlchemy 事件探针：10 轮 × 28 条注入 = 每轮恰好 1 条 INSERT（`spy.count == runs`）；语句墙钟 P95 ≤ 50ms 计时断言 |
| P6-IT-04 | `test_p6_it_04_detail_insert_failure_rolls_back_whole_finish` | 通过 | `before_cursor_execute` 注入 INSERT 失败：run 状态 / memory.injected 事件 / 明细全部回滚，无半截状态 |
| P6-IT-05 | `test_p6_it_05_three_entry_points_agree_and_deleted_items_degrade` | 通过 | 三入口数据一致；物理删除条目降级 `item_deleted` 标记、不还原内容 |
| P6-IT-06 | `test_p6_it_06_ttl_pass_purges_only_expired_detail_rows` | 通过 | 91/100 天行清除，89 天与真实 run 行保留（手动单轮 maintenance） |
| P6-IT-07 | `test_p6_it_07_customer_delete_cascades_detail_rows` | 通过 | `injection_logs_deleted=2` 审计计数 + API 404 |
| P6-IT-08 | `test_p6_it_08_merge_repoints_detail_rows_to_target` | 通过 | 明细改挂目标客户、审计 `injection_logs_moved`、注入历史连续可查 |
| P6-IT-09 | `test_p6_it_09_item_timeline_replays_full_lifecycle` | 通过 | 确认→注入→衰退 expired→恢复→编辑全事件，id DESC 倒序、actor/resource 正确、injection_count=1 |
| P6-UT-03 | `test_p6_ut_03_customer_memory_stats_matches_direct_counts` | 通过 | 单条 GROUP BY 与逐库直查一致；deleted 不计入、flag 计入 total |
| P6-UT-04 | `test_p6_ut_04_timeline_payload_exposes_prev_content_for_diff` | 通过 | timeline 载荷 prev_content 双侧 diff 数据 |
| P6-IT-10 | `test_p6_it_10_audit_and_injection_payloads_carry_no_content_or_pii` | 通过 | 5 个端点/载荷明文标记 + 手机号正则扫描零命中（item_timeline 的 item 块按 FR-5 合法含明文，只扫描 timeline 子数组） |
| P6-IT-11 | `test_p6_it_11_switch_off_writes_counts_only_and_queries_degrade_empty` | 通过 | 关时零明细、计数事件照常、查询接口空列表 200；重新打开后恢复落库 |
| P6-API-01 | `test_p6_api_01_injection_endpoints_reader_ok_unauth_401_limit` | 通过 | Reader 可读 × 5 端点 / Bearer bad 401 / limit 生效 / 倒序 |
| P6-API-02 | `test_p6_api_02_with_stats_columns_and_constant_query_count` | 通过 | with_stats 统计列正确 + 语句计数恒为 2（page_size=100 与 1 相同，无 N+1）+ sort=pending + flag 筛选 |
| P6-API-03 | `test_p6_api_03_timeline_endpoints_return_ordered_payloads` | 通过 | timeline 数据与倒序 + limit |
| P6-API-04 | `test_p6_api_04_cross_tenant_injection_views_are_404` | 通过 | 跨租户全 404（构造完整他租户对象图）且本租户视图不夹带他租户行 |
| P6-API-05 | `test_p6_api_05_historical_run_without_detail_returns_empty` | 通过 | 无明细 run 空列表 200 + 未知 id 404 |

### 3.4 端到端冒烟（实现阶段一次性脚本，非仓库文件）

`/tmp/p6_smoke.py` 对 PG 容器验证：finish 同事务批量落 2 行明细（flag score NULL、preference score=0.85）、事件 payload 计数 + item_ids 正确、六个查询服务结果正确、物理删除条目返回 item_deleted 无内容、合并改挂 2 行、删除级联清零、91 天明细被 maintenance TTL 清除——全部 PASS，冒烟数据已清理。

## 4. 前端测试结果

### 4.1 静态检查

- `npx tsc --noEmit`：**通过**（复核零错误）。
- `node scripts/check-i18n.mjs`：**通过**（复核：1487 translations，zh-CN/zh-TW/en/hi 四语键集一致，新增 47 key）。
- `npm run build`：tsc + vite build 成功（chunk 体积告警为既有现象）。

### 4.2 Playwright e2e（复核：26 passed in 2.2m）

环境：5173 端口临时启动 `npm run dev`，HTTP 200 后执行，测完已停止。执行 `tests/memory-visualization.spec.ts tests/memory.spec.ts tests/agent-memory-config.spec.ts` 合并复跑 **26/26 通过**。

| 计划编号 | 用例（`memory-visualization.spec.ts`） | 结果 |
|---|---|---|
| P6-FE-01 | customer list stats columns render, sorting and flag filter work | 通过 |
| P6-FE-02 | detail eight tabs lazy-load; item edit/delete/restore regression | 通过 |
| P6-FE-03 | injections tab lists runs newest-first with expandable item detail | 通过 |
| P6-FE-04 | item detail drawer shows fields, diff, lifecycle timeline and injections | 通过 |
| P6-FE-05 | conversation workbench shows this conversation's run injections | 通过 |
| P6-FE-06 | empty states: pre-launch run, no runs, unbound customer, no injections | 通过 |
| P6-FE-07 | viewer role sees every visualization read-only | 通过 |

在册回归：`memory.spec.ts` 11/11（9 个既有用例适配 8 Tab 结构 + 2 个本阶段新增下钻/工作台用例）、`agent-memory-config.spec.ts` 8/8（Phase 5 叠加回归）、`hash-routing.spec.ts` + `i18n.spec.ts` 相关 6/6（hashRouter query 保留改动无副作用）。

### 4.3 28 条历史失败用例的归属判定（与 Phase 6 无关）

更大范围 mock 套件（21 spec / 89 用例）跑出 61 passed / 28 failed。为判定归属，测试代理临时创建 git worktree（HEAD，符号链接 node_modules，5199 端口）跑基线对照，**28 条失败在 HEAD 基线上以相同方式复现**（ tickets-antd + agent-filters 抽样 5/5 同败），对照完成后 worktree 已删除。根因两类：

1. 用例 `goto('/')` 无 hash，触发 `AuthBoundary` 登录门（LoginPage），而 spec mock 的 `/auth/local/status` 返回 `{}`（无 development_access）——AuthBoundary 自初始提交未变（涉及 tickets-antd、deployment、login 等）；
2. agent 工作区相关 spec 的 mock 未覆盖 Phase 5 已提交端点（`/agents/memory-config-caps`、evaluation-reports）（涉及 agent-filters、agents-workspace、hash-routing、i18n、model-gateway-ui 等）。

属 stale 的**历史测试债**（test_bug），非 Phase 6 回归，建议归口测试负责团队另行排期适配。

### 4.4 前端 e2e 的效力边界

前端 e2e 全部经 `page.route` mock API（6 个新端点 + with_stats 列表），mock 与后端 SQL 投影逐字段对齐；**不能替代真实后端联动的浏览器走查**，真实环境人工走查步骤已列入验收方案 05 §3（见第 9 节遗留）。

## 5. AC 执行结果总表（验收轮，含独立探针交叉验证）

| # | 结果 | 证据 |
|---|---|---|
| AC-1 | 通过 | P6-IT-01/02 PG 真实执行；独立探针直查 DB：明细 4 行 = run × item（flag/profile/preference/event 各 1），score 规则正确；memory.injected payload 计数不变且 item_ids 与明细一致 |
| AC-2 | 通过 | P6-UT-03（统计与库直查一致）+ P6-API-02（恒定查询数无 N+1）+ FE-01（统计列/高亮深链/筛选/排序渲染） |
| AC-3 | 通过 | CustomerDetail 8 Tab（tabKeys）+ FE-02 懒加载与编辑/删除/恢复回归 |
| AC-4 | 通过 | list_injections 按 max(injected_at) DESC；InjectionLogPanel 展开明细（key/类型/小节/score）+ 跳会话；FE-03、P6-IT-05 |
| AC-5 | 通过 | 独立探针验证 run 明细 4 条全量返回；CustomerMemoryDrawer「本次对话注入」分 run 展示、点击开详情抽屉；FE-05 |
| AC-6 | 通过 | item_timeline 完整字段 + 查询时计算 effective_confidence；P6-IT-09 全生命周期时间线；P6-UT-04 prev_content diff；抽屉 LCS 行级 diff + 来源跳转；FE-04 |
| AC-7 | 通过 | items/{iid}/injections 按 injected_at DESC 含 conversation_id；独立探针与 P6-IT-05；未注入条目空列表、跨租户/未知 id 404 |
| AC-8 | 通过 | 独立探针构造无明细历史 run：200 且 items=[]；P6-API-05；前端空态文案「该会话无注入明细记录（功能上线前数据）」；FE-06 |
| AC-9 | 通过 | SQL spy 铁证单条批量 INSERT（10 次 run 仅 10 条 INSERT，unnest）；P95≤50ms 十取九过，首轮 1 次 62.7ms 抖动记 P3 缺陷 D2；无注入零写入由 P6-UT-05 验证 |
| AC-10 | 通过 | P6-IT-06：91 天前明细手动触发单轮 maintenance 清除、保留期内不受影响；retention 取 settings（默认 90 天），单条 DELETE 与事件 TTL 同事务同车 |
| AC-11 | 通过 | P6-API-04 五端点跨租户 404；独立探针：未认证 401、viewer 读 200、viewer DELETE 403；FE-07 viewer 全视图只读 |
| AC-12 | 通过 | 独立探针直查 DB：删除客户后明细 0 残留、审计 details.injection_logs_deleted=4；级联 DELETE 在 engine.begin() 同一事务内；P6-IT-07、P6-IT-04 |

合规核对：C1（明文/PII 零泄露，三层证据）/ C2（删除级联入事务）/ C3（已删条目不还原内容）/ C4（跨租户 404）/ C5（开关整体降级）**全部通过**，无一票否决项触发。

## 6. 多轮修复记录

### 6.1 后端：P6-IT-03 性能断言首轮失败（已关闭，记 P3 缺陷 D2）

- **现象**：后端第一轮 PG 全套件负载下，`test_p6_it_03` 的批量 INSERT（28 行）语句墙钟测得 **62.7ms > 50ms** 预算，失败 1 次。
- **排查**：SQL spy 证明始终为单条 unnest 批量 INSERT（`spy.count == runs`），无逐条写入——超标非产品缺陷；断言方法学脆弱：10 个样本的 P95 取 `ordered[min(9, int(10*0.95))]` = 最大值，单点抖动即超标。
- **处置**：不修改产品代码与测试代码；单测复跑 3 次 + 全套件 1 次 + 单测再 5 次共 **9 次全部通过**；复核轮 PG 全量（含本用例）亦通过。判定为共享 dev PG 容器在全套件并发下的测量环境抖动，遗留为 P3 缺陷 D2（建议增大样本量或分层断言，见 8.2）。
- **同期另一修复**（实现-验证闭环内）：PG 首轮中 `delete_customer` 返回值精确断言失败 1 次，按新契约（返回值增加 `injection_logs_deleted`）修正既有测试断言后复跑通过——属测试适配新契约，非产品缺陷。

### 6.2 前端：无 Phase 6 产品缺陷；28 条历史失败归属判定

前端第一轮 P6-FE-01~07 全部一次通过，未发现 Phase 6 产品 bug。大范围套件 28 条失败经 HEAD 基线 worktree 对照确认为历史既有失败（根因见 4.3），不修改、归口历史测试债。

### 6.3 测试新增交付（零产品代码改动）

- 后端新增：`backend/tests/test_memory_injection.py`（8 用例）、`backend/tests/test_memory_injection_postgres.py`（18 用例）。
- 前端新增：`frontend/tests/memory-visualization.spec.ts`（7 用例）；`frontend/tests/memory.spec.ts` 适配 8 Tab 结构并新增 2 用例。
- 实现阶段既有测试同步更新（渲染函数返回值解包、memory.injected payload 断言补 item_ids、delete_customer 返回值断言补 injection_logs_deleted）。

## 7. 回归结论（Phase 1-5 套件）

- **PG 全量回归两次实测通过**：568 passed, 20 skipped（后端第一轮 + 复核轮，含 Phase 1-5 全部 PG 套件：governance / decay / customers / config / runtime / observability 等）。20 skipped 经 `-rs` 确认全为环境性跳过（CREATEDB 角色迁移测试 / opt-in Milvus / 无 embedding 列模式），与 Phase 6 无关。
- **非 PG 全量回归通过**：273 passed, 315 skipped（复核实测）。
- **事件兼容红线成立**：memory.injected 消费方未受影响——Phase 4 hit_rate 聚合用 EXISTS 语义与 payload 无关（observability/service.py:149-160），`test_p4_it_17` 观测聚合与 `test_p4_it_12` 事件 payload 精确匹配（含 item_ids）均绿。
- **Phase 5 叠加回归**：`agent-memory-config.spec.ts` 8/8 通过；Phase 5 配置门控与注入改造叠加后行为正确（read 关闭时零明细，P6-UT-02/P6-IT-11）。
- **前端既有能力回归**：MemoryItemTable 编辑/删除/恢复、PendingQueue 确认/拒绝、治理（打标/合并/删除客户）、viewer 只读均绿（memory.spec.ts 11/11）。

## 8. 性能数据与缺陷清单

### 8.1 性能数据

| 指标 | 预算 | 实测 | 判定 |
|---|---|---|---|
| 注入明细批量写入对 run 完成路径额外耗时（AC-9，一票否决相关） | P95 ≤ 50ms | SQL spy 语句墙钟：10 轮 × 28 行，9 次 ≤50ms，1 次 62.7ms（全套件并发负载下首轮，判定测量抖动，见 6.1）；单条批量 INSERT 经 `spy.count == runs` 铁证 | 达标（附 P3 方法学遗留 D2） |
| 无注入场景开销 | 零写入 | P6-UT-05 四条路径（空渲染/无服务/无客户/读关闭/无条目）均不产生 log 上下文 | 达标 |
| 客户列表 with_stats 查询 | 无 N+1 | P6-API-02 语句计数恒为 2（page_size=100 与 1 相同） | 达标 |
| 注入记录查询 P95 ≤ 2s | — | **无计时证据**（数据量小，本地无法有效测量）；索引已按 customer/item/run 三键建立 | 遗留人工实测 |
| 客户详情首屏 P95 ≤ 1.5s（万级条目） | — | **无计时证据，且实现存在超标风险**（见 P2 缺陷 D1） | 遗留，需产品签字 |

### 8.2 缺陷清单与分级

| # | 级别 | 标题 | 状态与处置 |
|---|---|---|---|
| D1 | **P2** | 万级条目首屏性能无证据且实现存在超标风险 | **遗留，需产品签字**。GET /memory/customers/{cid}（service.py:679-722）仍全量 SELECT 该客户全部 memory_items（无 LIMIT/分页）并逐行 Python 计算 effective_confidence；详情各 Tab 数据均来自这一次全量拉取。万级条目时响应体约 10MB 级 + 万次 decay 计算，首屏大概率超 1.5s。属 Phase 2 遗留实现，Phase 6 将首屏 P95 写入本阶段 NFR 而未闭环。需类生产环境累积数据实测；若超标改为概览用聚合 + 条目分 Tab 分页拉取 |
| D2 | P3 | P6-IT-03 性能断言方法学脆弱（曾一轮超标 62.7ms） | **遗留**。10 样本 P95 取最大值，CI 抖动易误报。建议增大样本量（如 30 次取 idx=28）或对语句计时取中位+上限分层断言 |
| D3 | P3 | PG 测试 fixture 清场遗漏 memory_injection_log，共享库残留孤儿明细 | **遗留**。tests/test_platform_postgres.py 的 platform fixture 清场列表不含本表（D1 无外键不级联）。复核实测共享库已累积 **5182 行 / 206 个测试租户**孤儿明细（验收轮实测为 3520 行/150 租户——持续增长进一步佐证）。不影响产品正确性，但污染"直查 DB 对账"类验收口径。建议 fixture 清场加 `DELETE FROM memory_injection_log WHERE tenant_id=:t` |
| D4 | P3 | 前端 28 条历史 mock 测试债 | **遗留（Phase 6 范围外）**。HEAD 基线即失败（4.3），建议归口测试团队适配 AuthBoundary 登录门与 Phase 5 端点 mock |
| — | — | 前端 fullstack 类 spec 未执行 | 环境限制：agents-fullstack / gateway-fullstack / model-gateway / platform 等需真实后端（无路由拦截），不在 mock-only 范围内 |

**无 P0/P1 缺陷**（热路径逐条写库、明文/PII 泄露、跨租户可见、删除残留、明细错记漏记、事件计数语义破坏、合并丢注入历史——逐项验证均未发生）。

## 9. 出口准则核对（04-test-plan §8）

| 准则 | 核对结果 |
|---|---|
| 全部用例通过 | 满足：P6-UT 8/8、P6-IT/API 18/18、P6-FE 7/7；首轮 P6-IT-03 抖动已复跑 9 次通过并记 D2 |
| AC-1~AC-12 复现通过 | 满足：验收轮 12/12 通过（第 5 节） |
| 热路径性能为阻断项 | 满足：单条批量 INSERT 铁证 + 50ms 预算 9/10 达标（唯一超标判定环境抖动） |
| 泄露面为阻断项 | 满足：C1 三层证据零命中（P6-IT-10 + 独立探针正则扫描 + 实码核对） |
| 级联完整性为阻断项 | 满足：C2 删除级联入事务零残留、P6-IT-04 同事务同生共死、P6-IT-06 TTL、P6-IT-08 合并改挂 |
| Phase 1-5 回归套件全绿 | 满足：PG 568 passed / 非 PG 273 passed / 前端在册 26 passed（第 7 节） |
| 无 P0/P1 未关闭 | 满足：P0/P1 为零；P2 ×1（D1，按 04 §8 可带问题验收，需产品签字）、P3 ×3（D2/D3/D4） |

**遗留人工项（转验收报告，非自动化测试失败）**：

1. 排障情景 UAT 计时（05 §4：定位 run→注入明细→条目详情→编辑纠正→反查影响面，全程 ≤5 分钟）必须人工在真实环境执行；
2. A2~A7 界面场景的真实后端联动浏览器走查（05 §3 步骤）；
3. 注入记录查询 P95 ≤ 2s 与首屏 P95 ≤ 1.5s 的类生产环境实测（关联 D1）；
4. quickstart 双容器 TSVECTOR 降级形态验收（05 §2）。

## 10. 结论

- Phase 6 全部计划用例（P6-UT ×8、P6-IT/API ×18、P6-FE ×7）通过；AC-1~AC-12 与 C1~C5 全部通过，一票否决项（热路径性能 / 泄露面 / 级联完整性 / 租户隔离）逐项有自动化或探针证据，未触发。
- Phase 1-5 回归全绿：后端 PG 全量 568 passed（两次实测一致）、非 PG 273 passed、前端在册 e2e 26/26 passed；memory.injected 事件兼容红线成立，Phase 4 观测聚合不受影响。
- 无 P0/P1 缺陷。遗留 1 项 P2（D1 万级条目首屏性能，需产品签字 + 类生产实测）与 3 项 P3（D2 性能断言方法学、D3 测试 fixture 清场遗漏、D4 前端历史测试债）。
- **总体判定：测试通过出口准则（有条件通过）**，可进入验收签字环节；遗留人工项（排障 UAT 计时、真实环境走查、类生产性能实测、quickstart 降级验收）随《07-acceptance-report.md》闭环。
