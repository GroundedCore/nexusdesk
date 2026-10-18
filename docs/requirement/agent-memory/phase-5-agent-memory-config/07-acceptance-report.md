# Phase 5 · Agent 记忆配置开关 — 验收报告

> 验收日期：2026-10-18（第一轮，结论"不通过"）；2026-10-18（第二轮复验，P0 修复后）
> 依据：`05-acceptance-plan.md`（A1~A11、§5 存量等价专项、C1~C4、§8 通过标准）
> 验收对象：工作区未提交的 Phase 5 实现
> 执行方式：独立子代理执行自动化验收项；manual-required 项如实标注，未写作通过。
> 测试数据详见：`06-test-report.md`
> **当前结论（第二轮）：有条件通过**——全部自动化验收项与 C1/C2/C3 均满足；C4 回滚脚本演练、§5 存量等价类生产 fixture 对比、§4 UAT 维持 manual-required，完成并签字前不得发布（见第 7 节）。

---

## 1. 验收组织与执行说明

| 角色 | 本次执行情况 |
|---|---|
| 产品负责人 | 未参与（UAT 与 P2 遗留处置签字待办） |
| QA | 自动化 AC 场景执行与记录已由子代理完成；存量等价专项类生产部分待办 |
| 研发负责人 | 环境保障（5433 PG 容器、前端 dev server）已由子代理完成；缺陷修复与回滚脚本待办 |
| 实施/运营代表 | 未参与（§4 UAT 待办） |

验收环境：后端 PG 集成使用容器 `nexusdesk-p5-pg`（pgvector/pg17，迁移至 0038 head），`TEST_DATABASE_URL=postgresql+asyncpg://agent:agent_dev@127.0.0.1:5433/agent_platform`；前端 e2e 使用本地临时 dev server（5173，测完已停）。类生产环境（真实模型）未执行。

## 2. AC 执行记录表（A1~A11）

> 下表为**第二轮复验（2026-10-18，P0 修复后）状态**。第一轮被 P0 阻断判 fail 的 A2/A3/A4/A5/A8/A10/A11 复验全部转为 pass（根因 `service.py:243` 已改用 `model_dump(mode="json")`，PUT /agents 500 消除）。

| # | 状态 | 证据 |
|---|---|---|
| A1（配置页 10 项 4 组展示、默认值、viewer 只读） | **pass** | Playwright `tests/agent-memory-config.spec.ts` 8/8 passed（25.2s/26.4s 两轮复跑），含 P5-FE-01（4 组 10 项+默认值）与 P5-FE-07（viewer 全只读）；本地起 5173 dev server 实测，测完已停；表单见 `frontend/src/features/agents/AgentsPage.tsx:347-359` |
| A2（总开关关闭：置灰、无注入、无 memory_search、无抽取任务） | **pass** | PG `test_p5_it_01_master_switch_off_disables_injection_and_extract` passed（5433 容器，7 passed in 16.78s 之一）；单元 `test_ut_06` 在 36/36 内；P5-FE-02 置灰通过。第一轮因 P0 阻断判 fail，修复后复验通过 |
| A3（只读不写） | **pass** | PG `test_p5_it_02_read_only_injects_but_never_writes` passed。第一轮因 P0 阻断判 fail，修复后复验通过 |
| A4（review 全量 pending、确认前不注入、确认后注入） | **pass** | PG `test_p5_it_03_review_routes_high_confidence_to_pending` + `test_p5_it_04_review_snapshot_survives_later_config_change` 均 passed；单元 `test_ut_10` review 语义通过。第一轮因 P0 阻断判 fail，修复后复验通过 |
| A5（记忆类型仅勾画像：preference/event 不注入不写入） | **pass** | PG `test_p5_it_07_event_written_but_not_injected_without_event_type` passed；全量 `test_memory_config_postgres.py` 16/16 passed 含 `test_types_filter_drops_disallowed_extracted_types`。第一轮因 P0 阻断判 fail，修复后复验通过 |
| A6（平台关闭注入，Agent 全开 → 运行时全关 + 表单禁用提示） | **pass** | 抽查：`tests/test_memory_config.py` 36/36 passed（含 `test_ut_02` 参数化平台关 kill switch）；PG `test_p5_api_03` passed；Playwright P5-FE-05/05b passed。第一轮已 pass，新代码下仍成立 |
| A7（注入上限 999：保存提示截断、实际注入不超平台上限） | **pass** | 抽查：36/36 单元含 `test_ut_04a/04b`；PG `test_p5_it_06_search_limit_clamps` passed；Playwright P5-FE-06 passed。第一轮已 pass，新代码下仍成立 |
| A8（逐套预设应用并保存，重点高合规=review） | **pass** | 高合规=review 端到端由 it_03/it_04 真实 PG 通过覆盖（P0 修复后 PUT 保存不再 500）；Playwright P5-FE-04 预设填充不锁定并保存 passed；预设值 `AgentsPage.tsx:30-36` 与 FR-5 一致。第一轮因 P0 阻断判 fail，修复后复验通过 |
| A9（存量 Agent 升级后行为等价） | **pass（自动化部分）** | 自动化部分：36/36 单元含 `test_ut_09`（旧三键快照解析等价）与 `test_customer_memory_event_gate_follows_types`；PG `test_p5_it_09` 默认注入段与 Phase 2 fixture 逐位一致 passed。§5 类生产 fixture 对比部分维持 manual-required（单列，见第 4 节） |
| A10（会话进行中改配置：当前会话不变、新会话按新配置） | **pass** | PG `test_p5_it_08_inflight_run_keeps_submit_time_snapshot` passed（会话进行中改配置，在途 run 保持提交时快照）。第一轮因 P0 阻断判 fail，修复后复验通过 |
| A11（配置变更审计：操作人+变更前后值） | **pass** | PG `test_p5_api_04_config_change_is_audited_and_diffable` passed；P0 根因已修复——`agent_config/service.py:243` 使用 `model_dump(mode="json")`，UUID 序列化 500 消除，审计操作人+前后值可落库。第一轮判 fail，修复后复验通过 |

执行记录要素说明：本次为自动化子代理执行，执行环境见第 1 节；前置数据为各 PG fixture；截图/日志未单独归档（Playwright 与 pytest 输出即记录）。

## 3. 合规核对（C1~C4）

> 下表为**第二轮复验（2026-10-18）状态**。C3 第一轮因 P0 判 fail，修复后复验转为 pass；C4 维持 manual-required。

| # | 核对项 | 状态 | 证据 |
|---|---|---|---|
| C1（一票否决） | 平台级能力不出现在 Agent 配置表单与 API 响应 | **pass** | PG `test_p5_api_05_platform_capabilities_never_leak` passed（平台级能力不出现在 Agent 配置表单与 API 响应）；第一轮 grep 核对结论（`AgentConfig` 仅含 10 项新键+3 旧键、`memory_types` 不含 flag）在新代码下仍成立 |
| C2（一票否决） | 全局关闭时无任何 Agent 组合可生效 | **pass** | `tests/test_memory_config.py` 36/36 passed（`test_ut_02` 平台关全组合 + `test_ut_03/05/06/07`）；`resolve_memory_config` 单一事实源结论在新代码下仍成立 |
| C3（必须通过） | 配置变更有审计（操作人、前后值） | **pass** | PG `test_p5_api_04_config_change_is_audited_and_diffable` passed；绑定模型档案/知识库的 Agent PUT /agents 不再 500（`service.py:243` `model_dump(mode="json")`），审计（操作人+变更前后值）可写入。第一轮判 fail，修复后复验通过 |
| C4（必须通过） | 回滚路径明确、处置脚本已准备并演练 | **manual-required** | 维持 manual-required：`03-technical-design.md:138` 承诺的回滚处置脚本（从含新字段快照剔除新增键）全仓仍未找到（backend/ 无 scripts 目录），需研发负责人补齐并在类生产演练，产品/QA 见证签字 |

## 4. 存量等价专项（§5，一票否决）

状态：**manual-required（自动化部分已通过，类生产部分待人工执行）**

- 自动化等价证据已 pass（见 A9）：`test_ut_09` 旧快照解析等价 + `test_p5_it_09` 真实 PG 上默认注入段与 Phase 2 fixture 逐位一致 + `test_customer_memory_event_gate_follows_types`（events_limit=5 与 Phase 3 一致）。
- **第一轮警示事项已关闭**：P0 破坏的 Phase 2 存量回归用例 `test_p2_it_04`/`test_p2_it_09` 在 P0 修复后复验通过（2 passed in 7.18s），且第二轮 PG 全量 542 passed 覆盖 Phase 1-4 回归——§8 要求的"修复后重验受影响项 + Phase 1-4 全量回归"已完成。
- 但验收方案 §5 要求的"升级前 N≥5 场景 fixture 归档 → 升级后同脚本重跑逐位对比 + 存量 Agent 直查（旧三键不变/新字段默认）"须在类生产环境由 QA+研发人工执行；本地无升级前 fixture 基线可比对。该部分维持 manual-required，未写作通过。

## 5. UAT 与人工待办清单（明确 owner 角色）

> M1/M2/M3 已在第二轮（2026-10-18）完成；M4~M7 为仍开放的人工环节。

| # | 待办 | owner | 状态 |
|---|---|---|---|
| M1 | 修复 P0：`service.py` UUID JSON 序列化（`mode='json'`），消除 `model_profile_id`/`knowledge_base_ids` 误报变更 | 研发负责人 | **已完成**：`service.py:243` 改用 `model_dump(mode="json")`，PUT /agents 500 消除 |
| M2 | 修正 `test_p5_api_02` 断言 200 → 201 | 研发负责人 | **已完成**：断言更正为 201，与端点实际创建响应码一致 |
| M3 | 用 5433 容器重跑 `tests/test_memory_config_postgres.py` 全量 + Phase 1-4 全部 PG 套件回归（含 `test_p2_it_04`/`test_p2_it_09`） | QA（执行）+ 研发负责人（环境） | **已完成**：PG 全量 542 passed（含 Phase 5 16/16 与 `test_p2_it_04`/`test_p2_it_09` 复验通过）；非 PG 单测 36 passed；前端 e2e 8/8 passed |
| M4 | 补齐 C4 回滚处置脚本（从含新字段快照中剔除新增键）并在类生产演练 | 研发负责人；产品负责人 + QA 见证签字 | **未执行**（manual-required） |
| M5 | §5 存量等价专项：升级前 N≥5 场景 fixture 归档 → 升级后同脚本重跑逐位对比 + 存量 Agent 直查 | QA + 研发负责人 | **未执行**（manual-required，类生产环境） |
| M6 | §4 UAT：五个真实场景（普通客服/高合规/知识问答/短期问答/演示）预设+微调全流程无研发介入；高合规走"对话→待确认→确认→下次生效"；配置文案可理解性打分 | 实施/运营代表 | **未执行**（manual-required） |
| M7 | P2/P3 遗留处置签字（含前端 6 条历史失败用例的排期决策） | 产品负责人 | **未执行**（验收结论复核时） |

## 6. 遗留清单

| # | 级别 | 项 | 状态与说明 |
|---|---|---|---|
| L1 | ~~P0（阻断/一票否决级回归）~~ | PUT /api/v1/agents/{id} 对绑定模型档案或知识库的 Agent 必现 500 | **已关闭（第二轮）**：`service.py:243` 改用 `model_dump(mode="json")`，UUID 序列化 500 消除；被阻断的 A2/A3/A4/A5/A8/A10/A11/C3 复验全部通过；2 条 Phase 2 存量 PG 用例复验通过，存量回归已消除 |
| L2 | ~~测试缺陷（次要）~~ | `test_p5_api_02` 断言 POST /agents 返回 200，实际 201 | **已关闭（第二轮）**：断言更正为 201 |
| L3 | P2（必须通过项未满足） | C4 回滚脚本缺失 | **仍开放**：03 文档承诺的旧代码处置脚本未提交仓库，且未演练；需研发负责人补齐并在类生产演练、产品/QA 见证签字后方可发布 |
| L4 | 既有测试债（P3，Phase 5 范围外） | 6 条前端 Playwright 用例在 HEAD 基线即失败 | **仍开放**（范围外）：AuthBoundary 登录门未适配；经基线 worktree 对照确认与本阶段无关，待 M7 排期决策 |
| L5 | ~~报告修正项~~ | 前轮测试报告"PG 用例因缺 TEST_DATABASE_URL 跳过、后端 passed=true"结论不成立 | **已关闭（第二轮）**：第二轮在同一 5433 容器注入 TEST_DATABASE_URL 后 PG 全量实际执行完成（542 passed），`06-test-report.md` 3.2/10.2 节已修正 |

## 7. 结论

### 第一轮结论（2026-10-18）

**不通过。** 理由：存在 P0 级存量回归（L1）——审计 diff 代码使 PUT /agents 对绑定模型档案的 Agent 必现 500，击穿 A11 与 C3 并连带阻断 A2/A3/A4/A5/A8/A10；破坏 2 条 Phase 2 存量 PG 用例。C1/C2 通过，存量等价自动化部分通过，但 §5 类生产对比与 C4 回滚演练待人工执行。

### 第二轮结论（2026-10-18，当前生效）

**有条件通过。**

理由：

1. 第一轮 P0（PUT /agents UUID 序列化 500）已修复（`service.py:243` `model_dump(mode="json")`），被阻断的 A2/A3/A4/A5/A8/A10/A11/C3 复验全部通过——目标 PG 用例 `test_p5_it_01/02/03/04/07/08` + `test_p5_api_04` 七条全绿，`test_memory_config_postgres.py` 全量 16/16 passed。
2. P0 破坏的 Phase 2 存量回归 `test_p2_it_04`/`test_p2_it_09` 复验通过，存量回归已消除；第二轮 PG 全量 542 passed, 20 skipped，覆盖 Phase 1-4 回归。
3. 第一轮已 pass 的 A1（Playwright 8/8 复跑通过）、A6/A7/A9、C1/C2 抽查在新代码下仍成立（单元 36/36、PG api_03/it_06/it_09/api_05 均 pass）。
4. 所有自动化验收项与两条一票否决项 C1/C2 及必须通过项 C3 均满足。

**"有条件"之处**（发布前必须完成的人工环节，未写作通过）：

- **C4 回滚处置脚本缺失（L3/P2）**：`03-technical-design.md:138` 承诺的运维脚本未提交仓库，需研发负责人补齐并在类生产演练后方可签字。
- **§5 存量等价专项类生产部分（M5）**：升级前 N≥5 场景 fixture 归档 → 升级后同脚本重跑逐位对比 + 存量 Agent 直查，需 QA+研发在类生产环境完成。
- **§4 UAT（M6）**（五个真实场景预设+微调全流程）与 **P2/P3 遗留处置签字（M7）** 尚未执行，属验收方案规定的人工环节。

上述 manual-required 项完成并签字前不得发布。

## 8. 签字

| 角色 | 签字 | 日期 |
|---|---|---|
| 产品负责人 | 待签 | |
| QA | 待签 | |
| 研发负责人 | 待签 | |
| 实施/运营代表 | 待签 | |
