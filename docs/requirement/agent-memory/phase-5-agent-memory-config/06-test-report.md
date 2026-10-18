# Phase 5 · Agent 记忆配置开关 — 测试报告

> 执行日期：2026-10-18（第一轮）；2026-10-18（第二轮复跑，P0 修复后，见第 10 节）
> 依据：`04-test-plan.md`（P5-UT / P5-IT / P5-API / P5-FE 用例集）
> 测试对象：工作区未提交的 Phase 5 实现（Agent 记忆配置开关）
> 执行方式：独立子代理全流程执行（开发 → 缺口分析 → 缺口修复 → 测试 → 验收），本报告忠实记录全流程数据，不含杜撰内容。
> **当前状态**：第一轮 PG 层 11/16 失败的 P0 与测试缺陷均已修复，第二轮 PG 全量 542 passed / 非 PG 36 passed / 前端 e2e 8/8 passed，**测试通过出口准则**（详见第 10 节）。

---

## 1. 测试环境

| 环境 | 配置 | 用途 |
|---|---|---|
| 后端单测 | `backend/.venv/bin/pytest tests/ -q` | 全量套件（非 PG 用例直接执行） |
| 后端 PG 集成 | 容器 `nexusdesk-p5-pg`（pgvector/pg17，已迁移至 0038 head），`TEST_DATABASE_URL=postgresql+asyncpg://agent:agent_dev@127.0.0.1:5433/agent_platform` | P5-IT / P5-API 集成用例 |
| 前端类型检查 | `tsc --noEmit` | 静态类型 |
| 前端 e2e | Playwright，本地临时启动 `npm run dev`（5173 端口，pid 95664，测完已停止；5173/5174 均已确认关闭） | P5-FE-01~07 |
| 基线对照 | 临时 git worktree `/tmp/nexusdesk-baseline`（HEAD 7250aa0，符号链接 node_modules，5174 端口），对照完成后已删除 | 判定前端失败用例归属 |

## 2. 执行轮次总览

共执行 **2 轮**完整测试（测试修复历史为空，缺口修复发生在开发-验证闭环内，见第 5 节；第二轮为 P0 修复后的复跑，见第 10 节）。

### 第一轮（结论：未通过，已被第二轮取代）

| 维度 | 结果 | 备注 |
|---|---|---|
| 后端全量（非 PG 部分） | **通过**：265 passed, 297 skipped, 1 warning in 7.62s | 跳过项原因见第 7 节 |
| 后端 Phase 5 相关文件单独复跑 | 36 passed, 44 skipped | `test_memory_config.py` 全部通过 |
| 后端 PG 集成（验收阶段补跑） | **11/16 失败** | 见 3.2 节重要修正 |
| 前端 `tsc --noEmit` | **通过** | |
| 前端 Playwright（14 用例） | Phase 5 新增 8/8 通过；6 条失败经基线对照确认为**历史既有失败**，与本阶段无关 | 见第 4 节 |

### 第二轮（P0 修复后复跑，结论：通过）

| 维度 | 结果 | 备注 |
|---|---|---|
| 后端 PG 全量 | **通过**：542 passed, 20 skipped, 2 warnings in 418.78s (0:06:58) | 含 `test_memory_config_postgres.py` 16/16 与 Phase 1-4 PG 回归；见 10.2 |
| 后端非 PG 单测 | **通过**：36 passed in 0.66s | `test_memory_config.py` 全部通过 |
| 修复后定向自验 | **通过**：44 passed | `test_memory_config_postgres.py` + `test_memory_customers_postgres.py`（含存量回归用例 `test_p2_it_04`/`test_p2_it_09`） |
| 非 PG 全量回归 | **通过**：265 passed, 297 skipped，无回归 | |
| 前端 `tsc --noEmit` | **通过** | |
| 前端 Playwright（Phase 5 用例） | **通过**：8/8 passed in 26.4s | 5173 dev server 临时启动、测完已停 |

## 3. 后端测试结果

### 3.1 全量套件执行（测试轮次记录）

命令：`cd backend && .venv/bin/pytest tests/ -q`

- 结果：**265 passed, 297 skipped, 1 warning in 7.62s**
- Phase 5 相关文件单独复跑：**36 passed, 44 skipped**（`tests/test_memory_config.py` 全部通过，覆盖配置解析、门控逻辑等 36 个内存配置相关用例）
- 唯一 warning：无害的 starlette `DeprecationWarning`（`anyio.abc.BlockingPortal` 别名）
- 该轮未修改任何代码

### 3.2 重要修正：PG 用例"跳过"结论不成立（验收阶段发现）

测试轮次记录中"297 个跳过用例均为 Requires a migrated PostgreSQL test database，属预期跳过而非缺陷"的结论**需要修正**：验收阶段确认 `nexusdesk-p5-pg` 容器（pgvector/pg17，已迁移至 0038 head）就位于 5433 端口，PG 用例**实际可跑**。以 `TEST_DATABASE_URL=postgresql+asyncpg://agent:agent_dev@127.0.0.1:5433/agent_platform` 执行后：

- **`tests/test_memory_config_postgres.py` 16 条用例中 11 条失败**，测试轮次"后端 passed=true"的结论基于未覆盖 PG 的假象。
- 失败构成：
  - **10/16 因同一 P0 根因失败**（见 6.1）：`PUT /api/v1/agents/{id}` 对任何绑定模型档案（`model_profile_id`）或知识库（`knowledge_base_ids`）的 Agent 返回 500 —— `backend/src/agent_platform/modules/agent_config/service.py:240` 用 `model_dump()`（python 模式）构造审计 changes，`model_profile_id`/`knowledge_base_ids` 为 UUID 对象；`platform/persistence/store.py:44` `json.dumps(details)` 抛 TypeError。同时 string（旧 JSONB）vs UUID（新 dump）的比较使这两键永远被误记为"已变更"。
  - **1/16 为测试自身缺陷**（见 6.2）：`test_p5_api_02` 断言 `POST /agents` 返回 200，实际端点返回 201。
- **存量回归**：该 P0 同时破坏 2 条 Phase 2 存量 PG 用例（`tests/test_memory_customers_postgres.py::test_p2_it_04` / `test_p2_it_09`），属"默认值下既有测试必须原样通过"红线的违反。
- 已通过的 PG 用例（验收记录中明确给出证据）：`test_p5_it_06_search_limit_clamps`（检索上限真实库截断）、`test_p5_it_09_default_injection_is_byte_identical_to_phase2_fixture`（默认注入段与 Phase 2 fixture 逐位一致）、`test_p5_api_03_caps_endpoint_matches_settings`、`test_p5_api_05_platform_capabilities_never_leak`，以及类型过滤相关 `test_types_filter_drops_disallowed_extracted_types`、`test_customer_memory_filters_types_and_caps_items`。

### 3.3 后端单测明细（36/36 通过）

`backend/tests/test_memory_config.py`（内存配置求值与校验，全部通过）：

- `test_ut_01_defaults_resolve_everything_on`（P5-UT-01）
- `test_ut_02_platform_off_wins_over_agent_on`（P5-UT-02，参数化全组合，含缺口修复新增 `memory_inject_enabled=False => search 关闭`组合，锁定 AC-6/D1）
- `test_ut_03_agent_tightens_with_platform_on`（P5-UT-03）
- `test_ut_04a_inject_items_capped_at_platform_max`（P5-UT-04 注入侧，含 999→20 截断用例）
- `test_ut_04b_search_limit_capped_at_platform_max`（P5-UT-04 检索侧，期望 20→5，锁定 D2 天花板）
- `test_ut_05_blacklist_handoff_is_or_semantics`（P5-UT-05）
- `test_ut_06_master_switch_kills_read_write_search`（P5-UT-06）
- `test_ut_07_search_depends_on_read`（P5-UT-07）
- `test_ut_08a/08b/08c`（P5-UT-08：空 types / 非法枚举 / 下界越界 422；999 允许保存，锁定 AC-7 新语义）
- `test_ut_09_legacy_three_key_snapshot_deserializes_with_defaults`（P5-UT-09，旧三键快照解析=全新默认值，resolve 结果逐位相等）
- `test_ut_10_review_routes_everything_pending`（P5-UT-10）
- `test_ut_11_types_are_normalised_and_flag_is_never_an_option`、`test_ut_11b_partial_snapshot_dict_coerces_like_a_full_config`（P5-UT-11）
- `test_memory_context_snapshots_write_mode_and_types`、`test_memory_context_master_off_dispatches_no_extract`（worker 上下文快照补充用例）
- 缺口修复新增：事件上限收紧用例（Agent inject=3 时 events_limit==3）并锁定默认值 5（FR-1）

## 4. 前端测试结果

### 4.1 类型检查

`tsc --noEmit` **通过**。

### 4.2 Playwright e2e

- 环境：本地无 5173 dev server，按任务要求后台启动 `npm run dev`（pid 95664），测完已停止；5173/5174 均已确认关闭。
- 总盘：14 用例中 **8 passed**（Phase 5 新增 `tests/agent-memory-config.spec.ts` 全部 8 条通过）、**6 failed**。
- Phase 5 用例单独复跑：**8 passed (25.1s)**，覆盖：
  - P5-FE-01 默认渲染（4 组展示 + 默认值）
  - P5-FE-02 总开关联动置灰
  - P5-FE-03 条件字段显隐
  - P5-FE-04 预设填充并保存
  - P5-FE-05 / P5-FE-05b 平台禁用 tooltip 与平台封顶
  - P5-FE-06 超上限保存前警告（含缺口修复新增回归腿：填入值恰等于平台上限 20 时不弹截断告警、可正常保存）
  - P5-FE-07 viewer 只读

### 4.3 六条失败用例的归属判定（与 Phase 5 无关的历史失败）

为判定失败归属，临时创建 git worktree `/tmp/nexusdesk-baseline`（HEAD 7250aa0，符号链接 node_modules）在 5174 端口跑基线对照，**6 条失败用例在基线上以相同方式失败**，确认与 Phase 5 工作区改动无关；对照完成后 worktree 已删除，工作区代码零改动（git status 与任务开始时一致）。

| 失败用例 | 错误摘要 | 基线对照 |
|---|---|---|
| `agents-workspace.spec.ts › agent workspace layout and interactions: admin` | Test timeout 90s：`page.goto('/')` 后停留登录页，等待导航按钮"Agent 管理"。`AuthBoundary`（`src/features/auth/AuthBoundary.tsx:18`）在 `location.hash` 为空时强制进入 LoginPage，spec 未带 hash 路由 | 基线同样失败 |
| `agents-workspace.spec.ts › ... : viewer` | 同上：登录页卡住，等待"Agent 管理"超时 | 基线同样失败 |
| `agent-model-required.spec.ts › unconfigured model guides setup...` | Test timeout 90s：等待"Agent 管理"，页面停留登录页（`goto('/')` 无 hash 触发 AuthBoundary 登录门） | 基线同样失败 |
| `agent-filters.spec.ts:3 › scope is exclusive and mine survives refresh...` | Test timeout 90s：等待"返回列表"按钮 | 基线同样失败（AgentsPage diff 未触及列表/筛选逻辑） |
| `agent-filters.spec.ts:39 › live industry filters combine...` | Test timeout 90s：等待"行业：游戏"按钮 | 基线同样失败 |
| `agent-filters.spec.ts:68 › classification editor persists industry tags...` | Test timeout 90s：`getByLabel('所属行业')` selectOption 时元素反复 detach | 基线同样失败 |

**建议（测试债，Phase 5 范围之外）**：6 条历史失败用例需适配 AuthBoundary 登录门（goto 带 hash 路由或注入 sessionStorage token）。

### 4.4 前端 e2e 的效力边界

前端 e2e 为 mock API，**不能证明真实保存链路可用**；真实链路的验收依赖后端 PG 集成用例（当前被 P0 阻断，见 6.1）。

## 5. 缺口发现与修复记录（开发-验证闭环）

测试修复历史为空（`[]`）；修复集中发生在测试轮次之前的缺口分析 → 缺口修复闭环。缺口分析共发现 11 项，全部处置（`skipped=[]`），无跳过项。

### 5.1 缺口清单

| # | 缺口 | 严重度 | 处置 |
|---|---|---|---|
| 1 | AC-7：注入上限 999 被后端 422 拒绝而非按 AC-7 截断，e2e 用 mock 掩盖真实契约（`service.py:53` le=100；P5-FE-06 mock 保存 999 成功） | P1 | 已修复：去掉 le 上限（仅 ge=1），超上限允许保存、运行时按平台天花板截断；e2e 断言与真实后端一致 |
| 2 | AC-11：agent.updated 审计记录不含变更前后值，测试用 /diff 端点替代未验证审计本身 | P2 | 已修复：update() 改用 `UPDATE...FROM(快照子查询)...RETURNING old_*`，audit details 增加 `changes={field:{from,to}}`；P5-API-04 补断言（**注意：此修复代码引入了 6.1 的 P0，见第 6 节**） |
| 3 | AC-6：平台开关全关时 memory_search 工具仍注册，与"运行时行为全关"字面冲突 | P3 | 已修复：search 门控 AND 入 `memory_inject_enabled`；UT-02 补组合 |
| 4 | D2：检索上限平台默认值/上限与设计不符（设计=5，实现=10） | P2 | 已修复：新增 `settings.memory_search_limit=5` 作平台默认与天花板；caps 端点键同步更名 |
| 5 | FR-1：事件条目注入条数不受 Agent 级 memory_inject_max_items 控制 | P2 | 已修复：事件段改为 `min(effective.inject_max_items, memory_event_inject_max)`，默认行为与 Phase 3 逐位一致；补 UT |
| 6 | D1：search 门控未纳入平台读侧总开关 memory_inject_enabled | P2 | 已修复（同 #3） |
| 7 | AC-7 前端：截断提示条件用 >=，值恰等于平台上限时误报 | P3 | 已修复：改为严格 >；P5-FE-06 补回归腿 |
| 8 | C-3：生效值求值入口位置与设计不一致（实现移至 agent_config/memory.py） | P3 | 已修复：回改 03 设计文档 |
| 9 | P5-IT-03：review 端到端缺"确认前不注入、确认后注入"两条腿 | P2 | 已修复：套用 P2-IT-07 写法补 confirm→inject 闭环断言 |
| 10 | P5-IT-01：总开关关闭端到端未断言"无 memory_search 工具" | P3 | 已修复：补"不注册且直接调用被拒"断言 |
| 11 | P5-API-02：非法配置 422 未断言错误信息明确性 | P3 | 已修复：断言 detail 含字段名 |

### 5.2 修复改动文件清单（12 项）

- `backend/.../agent_config/service.py`：AC-7（去 le 上限）+ AC-11（审计 changes 前后值，409 语义不变，API 响应形状不变）
- `backend/.../agent_config/memory.py`：AC-6/D1（search 门控 AND 平台读开关）+ D2（search_limit 用新天花板）
- `backend/.../settings.py`：D2（新增 `memory_search_limit: int = Field(default=5, ge=1, le=50)`；既有 `memory_search_limit_max` 保留为 store 第二道钳制，Phase 3 测试不受影响）
- `backend/.../agent_runtime/worker.py`：FR-1（事件段 `min(effective.inject_max_items, memory_event_inject_max)`）
- `backend/.../apps/api/platform_routes.py`：D2 配套（caps 响应键 `memory_search_limit_max` → `memory_search_limit`）
- `backend/tests/test_memory_config.py`：UT-02 补组合、UT-04a 999→20、UT-04b 20→5、UT-08c 下界 422/999 可存、事件上限用例；36 passed
- `backend/tests/test_memory_config_postgres.py`：P5-IT-01 补 memory_search 断言、P5-IT-03 补 AC-4 闭环、P5-IT-06 改共享 helper、P5-API-02 改下界/枚举/类型 422 且断言 detail + 999 保存成功、P5-API-03 期望键更名、P5-API-04 断言 `details.changes.memory_write_mode=={from:auto,to:review}`；16 用例收集无误
- `frontend/.../AgentsPage.tsx`：截断提示 >= 改 >；检索提示改用 `caps.memory_search_limit`
- `frontend/.../shared/api/client.ts`：`MemoryConfigCaps` 键更名
- `frontend/tests/agent-memory-config.spec.ts`：CAPS mock 键同步；P5-FE-06 增"等于上限不告警"回归腿；e2e 8/8 通过
- `docs/.../03-technical-design.md`：校验语义改"仅下界 422，超上限运行时截断"；求值位置更正为 agent_config/memory.py（C-3）；search 伪代码补 `s.memory_inject_enabled`（D1）；search_limit 伪代码改用 `s.memory_search_limit`（D2）；新增事件条数上限归属说明（FR-1）
- `docs/.../04-test-plan.md`：P5-UT-08/P5-API-02 预期措辞修订（下界 422 指明字段；超上限允许保存运行时截断）

## 6. 失败与遗留（第一轮记录；6.1/6.2 已在第二轮修复关闭，见第 10 节）

### 6.1 P0（阻断级，一票否决级回归）

`PUT /api/v1/agents/{id}` 对任何绑定模型档案或知识库的 Agent 返回 500：`agent_config/service.py:240` `new_draft = body.config.model_dump()` 产生 UUID 对象，`platform/persistence/store.py:44` `json.dumps(details)` 抛 TypeError；审计记录写不入，且 `model_profile_id`/`knowledge_base_ids` 永远被误记为"已变更"。影响：10/16 条 Phase 5 PG 用例 + 2 条 Phase 2 存量 PG 用例（`test_p2_it_04`/`test_p2_it_09`）失败。修复方向：changes 的 from/to 用 JSON 安全值（`model_dump(mode='json')` 或 `json.loads(model_dump_json())`），同时消除两键误报。修复后须重跑 `tests/test_memory_config_postgres.py` 全量 + Phase 1-4 PG 回归。

### 6.2 测试缺陷（次要）

`test_memory_config_postgres.py::test_p5_api_02` 断言 `POST /agents` 返回 200，实际端点返回 201——测试自身必失败，需改断言为 201。

### 6.3 前端历史测试债

6 条 Playwright 用例在 HEAD 基线即失败（AuthBoundary 登录门未适配），与 Phase 5 无关，建议另行排期。

### 6.4 C4 回滚脚本缺失

03 设计文档承诺的"回滚时从新快照剔除新增键"运维脚本未提交到仓库（全仓 grep 未找到，backend/ 无 scripts 目录），且未演练。

## 7. 跳过项及原因

| 跳过范围 | 数量 | 原因 |
|---|---|---|
| 后端全量中 PG 集成用例（主要为 `test_memory_config_postgres.py` 与 `test_memory_customers_postgres.py`） | 297 skipped（全量口径）/ 44 skipped（Phase 5 文件口径） | 测试轮次执行时未设置 `TEST_DATABASE_URL`，pytest 标记 "Requires a migrated PostgreSQL test database" 自动跳过。**注意**：5433 容器实际就位，该跳过属环境变量未注入而非环境不可用，验收阶段已补跑并暴露 6.1/6.2（见 3.2 修正） |
| 缺口修复 skipped | 0 | 11 项缺口全部处置，无跳过 |
| 存量等价专项 §5 类生产 fixture 对比 | 未执行 | 需类生产环境升级前 N≥5 场景 fixture 基线，本地无升级前 fixture 可比对；转人工待办（见验收报告） |
| C4 回滚演练 | 未执行 | 脚本缺失（6.4） |

## 8. 测试计划用例覆盖映射表

### 8.1 单元测试（`backend/tests/test_memory_config.py`）

| 计划编号 | 测试函数 | 结果 |
|---|---|---|
| P5-UT-01 | `test_ut_01_defaults_resolve_everything_on` | 通过 |
| P5-UT-02 | `test_ut_02_platform_off_wins_over_agent_on`（参数化，含 memory_inject_enabled→search 组合） | 通过 |
| P5-UT-03 | `test_ut_03_agent_tightens_with_platform_on` | 通过 |
| P5-UT-04 | `test_ut_04a_inject_items_capped_at_platform_max`（含 999→20）、`test_ut_04b_search_limit_capped_at_platform_max`（20→5） | 通过 |
| P5-UT-05 | `test_ut_05_blacklist_handoff_is_or_semantics` | 通过 |
| P5-UT-06 | `test_ut_06_master_switch_kills_read_write_search`；补充 `test_memory_context_master_off_dispatches_no_extract` | 通过 |
| P5-UT-07 | `test_ut_07_search_depends_on_read` | 通过 |
| P5-UT-08 | `test_ut_08a_empty_types_rejected` / `test_ut_08b_invalid_enum_values_rejected` / `test_ut_08c_out_of_range_numeric_limits_rejected`（999 允许保存） | 通过 |
| P5-UT-09 | `test_ut_09_legacy_three_key_snapshot_deserializes_with_defaults`；补充 `test_ut_11b_partial_snapshot_dict_coerces_like_a_full_config` | 通过 |
| P5-UT-10 | `test_ut_10_review_routes_everything_pending` | 通过 |
| P5-UT-11 | `test_ut_11_types_are_normalised_and_flag_is_never_an_option`；集成腿 `test_types_filter_drops_disallowed_extracted_types`（PG） | 通过 |
| （补充） | `test_memory_context_snapshots_write_mode_and_types`、事件上限收紧/默认值用例（FR-1） | 通过 |

### 8.2 集成与 API 测试（`backend/tests/test_memory_config_postgres.py`，5433 PG 容器）

> 下表为**第一轮**执行结果。第二轮（P0 修复后）该文件全量 **16/16 passed**，Phase 2 存量用例 `test_p2_it_04`/`test_p2_it_09` 亦复验通过，见 10.2 节。

| 计划编号 | 测试函数 | 结果 |
|---|---|---|
| P5-IT-01 | `test_p5_it_01_master_switch_off_disables_injection_and_extract` | **失败**（P0：PUT /agents 500） |
| P5-IT-02 | `test_p5_it_02_read_only_injects_but_never_writes` | **失败**（同一 P0） |
| P5-IT-03 | `test_p5_it_03_review_routes_high_confidence_to_pending`（含 confirm→inject 闭环） | **失败**（同一 P0） |
| P5-IT-04 | `test_p5_it_04_review_snapshot_survives_later_config_change` | **失败**（同一 P0） |
| P5-IT-05 | `test_p5_it_05_inject_cap_bounds_prompt_items` | **失败**（同一 P0） |
| P5-IT-06 | `test_p5_it_06_search_limit_clamps_and_switch_unregisters` | 通过（真实库截断验证） |
| P5-IT-07 | `test_p5_it_07_event_written_but_not_injected_without_event_type` | **失败**（同一 P0） |
| P5-IT-08 | `test_p5_it_08_inflight_run_keeps_submit_time_snapshot` | **失败**（同一 P0） |
| P5-IT-09 | `test_p5_it_09_default_injection_is_byte_identical_to_phase2_fixture` | 通过（真实 PG 逐位一致） |
| P5-IT-10 | `test_p5_it_10_blacklist_handoff_sinks_to_agent_switch` | **失败**（同一 P0） |
| P5-API-01 | `test_p5_api_01_config_save_read_roundtrip_and_rbac` | **失败**（同一 P0） |
| P5-API-02 | `test_p5_api_02_invalid_config_returns_422` | **失败**（测试自身缺陷：断言 200 vs 实际 201，见 6.2） |
| P5-API-03 | `test_p5_api_03_caps_endpoint_matches_settings` | 通过 |
| P5-API-04 | `test_p5_api_04_config_change_is_audited_and_diffable` | **失败**（同一 P0，审计写不入） |
| P5-API-05 | `test_p5_api_05_platform_capabilities_never_leak` | 通过（真实 PG） |
| P5-UT-11（集成腿） | `test_types_filter_drops_disallowed_extracted_types` | 通过 |

注：8.2 结果基于验收阶段在 5433 容器的执行记录；16 条收集无误，11 条失败（10 条 P0 + 1 条测试自身缺陷），通过项含上表 4 条及类型过滤相关用例。P0 同时连带 `tests/test_memory_customers_postgres.py::test_p2_it_04` / `test_p2_it_09` 两条 Phase 2 存量用例失败。

### 8.3 前端测试（`frontend/tests/agent-memory-config.spec.ts`）

| 计划编号 | 用例 | 结果 |
|---|---|---|
| P5-FE-01 | memory group renders 4 groups with correct defaults | 通过 |
| P5-FE-02 | master switch greys out every child control | 通过 |
| P5-FE-03 | conditional fields: write policy and search limit | 通过 |
| P5-FE-04 | presets fill the form without locking, then save | 通过 |
| P5-FE-05 | platform-disabled switch shows "平台已禁用" tooltip | 通过 |
| P5-FE-05b | platform caps: summary disabled and blacklist forced on | 通过 |
| P5-FE-06 | injection limit above the platform cap warns before saving（含"等于上限不告警"回归腿） | 通过 |
| P5-FE-07 | viewer sees every control read-only | 通过 |

## 9. 第一轮结论（2026-10-18，已被第 10 节第二轮结论取代）

- 后端单元层（36/36）与前端 Phase 5 e2e（8/8）全部通过，配置求值、门控组合、截断语义、快照兼容、平台能力不外泄等单元级与界面级目标达成。
- **但 PG 真实库集成层 11/16 失败**，其中 P0（PUT /agents 500）属阻断级存量回归，违反测试计划"存量行为等价是红线"与出口准则"无 P0/P1 未关闭"；另有 1 条测试自身缺陷（201 断言）。
- 测试轮次"后端 passed"结论须按 3.2 节修正理解：该结论未覆盖 PG 用例。
- **总体判定：测试未通过出口准则**。处置顺序：修复 6.1 → 修正 6.2 → 用 5433 容器重跑 `test_memory_config_postgres.py` 及全部 PG 套件（含 Phase 1-4 回归）→ 补齐 6.4 回滚脚本并演练。

## 10. 第二轮：P0 修复与全量复跑（2026-10-18）

### 10.1 P0 修复记录

| # | 文件 | 修复内容 |
|---|---|---|
| 1 | `backend/src/agent_platform/modules/agent_config/service.py`（`AgentService.update()`，约 240 行） | AC-11 审计 changes dict 改用 `body.config.model_dump(mode="json")`（原 `model_dump()` python 模式）。UUID 字段（`model_profile_id`、`knowledge_base_ids`）序列化为字符串，`platform/persistence/store.py:44` `json.dumps(details)` 不再抛 TypeError（PUT /api/v1/agents/{id} 500 修复）；旧 JSONB 值（字符串）与新值在未变更时比较相等，消除了审计 `details.changes` 中的误报"已变更"条目 |
| 2 | `backend/tests/test_memory_config_postgres.py` | `test_p5_api_02`：AC-7 超上限 POST /api/v1/agents 断言由 200 更正为 201，与端点实际创建响应码一致 |

修复自验（未提交 git，按指令执行）：`TEST_DATABASE_URL=postgresql+asyncpg://agent:agent_dev@127.0.0.1:5433/agent_platform .venv/bin/pytest tests/test_memory_config_postgres.py tests/test_memory_customers_postgres.py -q` → **44 passed**；非 PG 全量 `.venv/bin/pytest tests/ -q` → **265 passed, 297 skipped**，无回归。

### 10.2 第二轮 PG 全量执行结果

命令：`cd backend && TEST_DATABASE_URL=postgresql+asyncpg://agent:agent_dev@127.0.0.1:5433/agent_platform .venv/bin/pytest tests/ -q`

- 结果：**542 passed, 20 skipped, 2 warnings in 418.78s (0:06:58)**，零失败。
- 第一轮失败的 11 条 Phase 5 PG 用例（10 条 P0 + 1 条 201 断言缺陷）全部转为通过；`tests/test_memory_config_postgres.py` 全量 **16/16 passed**。
- 被 P0 破坏的 Phase 2 存量 PG 用例 `test_memory_customers_postgres.py::test_p2_it_04` / `test_p2_it_09` 复验通过（2 passed in 7.18s），**存量回归已消除**。
- 2 个 warnings 为既有噪音：starlette TestClient 的 anyio BlockingPortal DeprecationWarning；`tests/test_channel_native.py::test_feishu_message_runs_agent_and_delivers_reply` 中 asyncpg 连接被 GC 回收的 SAWarning（连接未显式归还连接池）。均不影响测试结果，未修改任何代码。
- 20 skipped 为 PG 环境下按标记预期跳过的非 PG 用例。

**对第一轮"PG 跳过"说法的最终更正**：第一轮 3.2 节已指出"297 skipped 属 TEST_DATABASE_URL 未注入而非环境不可用"；第二轮在同一 5433 容器上注入环境变量后 PG 全量实际执行完成（542 passed），证实 PG 用例全程可跑，第一轮"后端 passed=true"口径不覆盖 PG 的问题已彻底消除。

### 10.3 第二轮前端复测

- `tsc --noEmit` 无错误。
- Playwright 运行 `frontend/tests/agent-memory-config.spec.ts` 的 8 条 Phase 5 用例（P5-FE-01 至 P5-FE-07，含 P5-FE-05b）**全部通过，用时 26.4s**。
- 环境：5173 端口原本无 dev server，后台启动 `npm run dev`（HTTP 200 后执行测试），测完已停止。未修改任何代码。
- 通过用例：P5-FE-01 记忆分组渲染 4 组及默认值；P5-FE-02 总开关禁用所有子控件；P5-FE-03 条件字段（写入策略、检索上限）；P5-FE-04 预设填充表单且不锁定、可保存；P5-FE-05 平台禁用时显示「平台已禁用」tooltip；P5-FE-05b 平台封顶（摘要禁用、黑名单强制开启）；P5-FE-06 注入上限超平台封顶保存前警告；P5-FE-07 viewer 所有控件只读。

### 10.4 第二轮结论

- 6.1（P0，PUT /agents UUID 序列化 500）与 6.2（test_p5_api_02 断言缺陷）均已修复关闭。
- 后端 PG 全量 542 passed（含 Phase 5 16/16 与 Phase 1-4 回归）、非 PG 单测 36 passed、前端 Phase 5 e2e 8/8 passed，两条 pytest 命令均无失败用例。
- **总体判定：测试通过出口准则（无 P0/P1 未关闭；存量行为等价红线在 PG 全量回归下成立）。**
- 仍开放项（不属自动化测试失败）：6.3 前端 6 条历史测试债（Phase 5 范围外，HEAD 基线即失败）；6.4 C4 回滚处置脚本缺失（验收要求的人工/运维环节，见 `07-acceptance-report.md`）。
