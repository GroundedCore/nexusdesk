# Phase 1 · 会话摘要记忆 — 测试报告

> 状态：已完成（首轮全部用例通过；2026-10-10 修订轮见 §10：本地门槛全绿、postgres 用例已于 CI 全绿）
> 创建：2026-10-10
> 依据：`04-test-plan.md`（P1-UT-01~12、P1-IT-01~21、P1-CM-01~05，含 2026-10-10 增补）、`02-requirements.md`（FR-1~FR-5、AC-1~AC-6）
> 执行：验收代理（自动化），开发/QA 迭代结论已经对抗性复核

## 1. 测试范围与环境

**范围**：会话滚动摘要的生成、存储、注入；人工接管摘要改造；全局/Agent 级开关；`memory_tasks` 任务表与 Memory Worker；迁移 `0030`。

| 环境 | 配置 | 用途 |
|---|---|---|
| 本机（macOS，Python 3.11/3.12 venv） | 无 PostgreSQL、无 Docker | ruff 全量；非 postgres 单测与引擎层用例 |
| CI（GitHub Actions，ubuntu + postgres:17 service） | `TEST_DATABASE_URL` 指向迁移后的 PG 17 | 全量套件（含 P1-IT/P1-CM postgres 用例） |
| 模型 | demo/mock（`RecordingGateway` 可编程：固定文本、错误、超长、空串） | 全部自动化用例 |

**本地 skip 说明**：带 `pytest.mark.postgres` 的用例在本机无 `TEST_DATABASE_URL` 时按预期 skip（环境约束允许），同批用例在 CI postgres:17 上全量执行并通过。

## 2. 执行总览

| 项 | 结果 | 证据 |
|---|---|---|
| 本机 `uv run ruff check .` | All checks passed | 附录 A-1 |
| 本机 `uv run pytest -q` | 121 passed, 158 skipped（postgres 标记按预期 skip），0 失败 | 附录 A-2 |
| 本机 memory 定向用例 | 16 passed, 1 skipped（迁移用例需 PG） | 附录 A-3 |
| CI Backend job（ruff + migrate + pytest 全量） | 全部步骤 success；开发记录 pytest 278 passed / 1 skipped | §9 |
| CI Frontend job | success（本改动不涉及前端，作回归佐证） | §9 |

## 3. 单元测试（P1-UT）逐条结果

| 编号 | 用例 | 测试位置 | 执行环境 | 结果 |
|---|---|---|---|---|
| P1-UT-01 | prompt 组装：无旧摘要含全部丢弃消息 | `test_memory_summary.py::test_p1_ut_01_*` | 本机 | 通过 |
| P1-UT-02 | prompt 组装：旧摘要+新片段合并 | `test_memory_summary.py::test_p1_ut_02_*` | 本机 | 通过 |
| P1-UT-03 | 空串/纯空白输出拒绝 | `test_memory_summary.py::test_p1_ut_03_*` | 本机 | 通过 |
| P1-UT-04 | 超 `summary_max_output_chars` 取尾部截断 | `test_memory_summary.py::test_p1_ut_04_*` | 本机 | 通过（口径偏差见 §8-R1） |
| P1-UT-05 | pending 任务合并 payload | `test_memory_postgres.py::test_p1_ut_05_*` | CI（PG） | 通过 |
| P1-UT-06 | running 任务不干扰（固化实现决策） | `test_memory_postgres.py::test_p1_ut_06_*` | CI（PG） | 通过 |
| P1-UT-07 | dropped 截断至 40 条 | `test_memory_summary.py::test_p1_ut_07_*`（纯函数，本机）+ `test_memory_postgres.py::test_p1_ut_07_enqueue_*`（DB，CI） | 本机+CI | 通过 |
| P1-UT-08 | 注入 800 tokens 估算截断（尾部优先） | `test_memory_summary.py::test_p1_ut_08_*` | 本机 | 通过 |
| P1-UT-09 | profile 绑定选择 / `no_chat_profile` 不可重试 | `test_memory_summary.py::test_p1_ut_09_*`（本机）+ `test_memory_postgres.py::test_p1_it_05b_*`（DB，CI） | 本机+CI | 通过 |
| P1-UT-10 | 指数退避 30/60/120、封顶 300；首败回 pending | `test_memory_summary.py::test_p1_ut_10_*`（函数，本机）+ `test_p1_it_05_*` 内 DB 断言（attempts=1、`run_after` 为将来时刻，CI） | 本机+CI | 通过 |
| P1-UT-11 | 达 max_attempts=2 置 failed、记审计、不再重试 | `test_memory_postgres.py::test_p1_it_05_*` 内断言 | CI（PG） | 通过 |

## 4. 集成测试（P1-IT）逐条结果

全部在 CI（postgres:17）执行，通过；本机同批用例按 §1 约束 skip。

| 编号 | 用例 | 测试位置 | 结果 |
|---|---|---|---|
| P1-IT-01 | 截断→任务→worker 执行→summary 落库→下一 run 携带摘要 | `test_p1_it_01_truncation_dispatches_task_and_summary_is_written` | 通过（含 `summary.queued` 事件、审计、跨租户 claim 断言） |
| P1-IT-02 | 摘要滚动（第 2 次 prompt=第 1 次摘要+新 dropped） | `test_p1_it_02_summary_rolls_forward_with_previous_summary` | 通过 |
| P1-IT-03 | 注入位置与格式；空摘要/开关关闭不注入 | `test_memory_summary.py::test_p1_it_03_*`、`test_engine_skips_empty_or_disabled_summary`（本机可执行，亦已通过） | 通过 |
| P1-IT-04 | 摘要生成期间新一轮沿用旧摘要不阻塞 | `test_p1_it_04_pending_summary_never_blocks_a_run` | 通过（行为断言替代耗时对比，偏差已经 QA 接受） |
| P1-IT-05 | 模型连续报错→重试→failed+审计+旧摘要保留 | `test_p1_it_05_model_failure_retries_then_fails_keeping_old_summary` | 通过 |
| P1-IT-06 | 未截断无任务、无 memory 用量 | `test_p1_it_06_no_task_or_usage_before_first_truncation` | 通过 |
| P1-IT-07 | handoff 有摘要：两段式 | `test_p1_it_07_handoff_includes_summary_and_recent_sections` | 通过 |
| P1-IT-08 | handoff 无摘要：与旧格式一致 | `test_p1_it_08_handoff_without_summary_matches_legacy_format` | 通过 |
| P1-IT-09 | 全局开关关闭零任务 | `test_p1_it_09_global_switch_off_dispatches_nothing` | 通过 |
| P1-IT-10 | Agent 级开关随草稿/发布/回滚 | `test_p1_it_10_agent_switch_follows_draft_publish_rollback` | 通过 |
| P1-IT-11 | 会话关闭/删除跳过写库 | `test_p1_it_11_closed_or_deleted_conversation_is_skipped` | 通过 |
| P1-IT-12 | evaluation 不产生摘要任务 | `test_p1_it_12_evaluation_never_dispatches_summary_tasks` | 通过 |
| P1-IT-13 | 并发 claim 仅一个成功 | `test_p1_it_13_concurrent_claim_runs_a_task_exactly_once` | 通过 |
| P1-IT-14 | 优雅停机 running 重置 pending | `test_p1_it_14_graceful_shutdown_releases_running_tasks` | 通过 |

## 5. 通用核对项（P1-CM）逐条结果

| 编号 | 核对项 | 测试位置 | 执行环境 | 结果 |
|---|---|---|---|---|
| P1-CM-01 | 迁移 0030 只加列加表、存量保留、约束生效、可回滚 | `test_memory_migrations.py::test_summary_migration_preserves_existing_conversations` | CI（PG；本机 skip） | 通过 |
| P1-CM-02 | 三个 settings 默认值与 `AGENT_` 环境变量覆盖 | `test_memory_summary.py::test_p1_cm_02_*` | 本机 | 通过 |
| P1-CM-03 | 审计事件字段齐全、payload 无消息原文；run trace 有 `summary.queued` 标记 | `test_p1_it_01_*` 内嵌断言 | CI（PG） | 通过 |
| P1-CM-04 | 租户隔离：跨租户任务不可写他租户摘要、跨租户 claim 不可达 | `test_p1_cm_04_cross_tenant_summary_is_unreachable` + `test_p1_it_01_*` 尾部断言 | CI（PG） | 通过 |

## 6. 回归范围

- 本机非 postgres 全量 121 passed（含 conversation / runtime / handoff / evaluation / gateway 等既有套件），空 `summary` 行为不变。
- CI 全量 278 passed / 1 skipped（开发记录；§9 说明核验边界），覆盖全部既有 postgres 套件。
- 新增回归测试 `test_demo_model_never_calls_tools_the_caller_did_not_offer`（本机通过）固化 §7-D1 修复。

## 7. 迭代中发现并修复的缺陷

| # | 级别 | 缺陷 | 发现环节 | 修复 | 回归 |
|---|---|---|---|---|---|
| D1 | P1 | demo 模型在请求未提供工具时仍对"查询演示"文本发出工具调用；摘要 prompt 引用历史演示回复时模型返回 tool_calls，摘要任务判 `invalid_model_response` | CI run 38024854835（sha `5181abe`）Backend Test 失败 | `0d0b6b3`：demo 模型仅当工具被提供时才发出工具调用 | 新增 `test_demo_model_never_calls_tools_the_caller_did_not_offer`；复跑 run 38025292718 全绿 |
| D2 | P2（测试缺陷） | P1-IT-02 stub 网关接线错误导致用例失败（非产品代码缺陷） | 同上 CI run（开发自报；job 日志下载需 admin 权限，未能逐行核对失败明细） | `0d0b6b3` 一并修正 | 复跑全绿 |

本地开发轮（ruff/单测）未发现其他失败；QA 对抗性复核未发现 P0/P1。

## 8. 残留风险

| # | 级别 | 描述 | 处置 |
|---|---|---|---|
| R1 | P2 | FR-1 要求摘要输出 ≤500 字；实现（prompt 指令与硬截断）按 `summary_max_output_chars=1000` 字符执行。注入侧 800 tokens 预算兜底，成本与上下文安全不受影响，仅摘要可能偏长 | 待产品确认：收紧默认值至 500 或修订需求口径并签字（详见 `07-acceptance-report.md` §6） |
| R2 | P3 | 无 Agent 绑定的 legacy 会话每次截断产生一个不可重试的 `failed(no_chat_profile)` 任务，`memory_tasks` 失败行随轮次累积 | 建议 Phase 2 决策：投递时跳过无绑定会话或引入租户默认 profile |
| R3 | P3 | 硬杀（非优雅停机）时 MemoryWorker 持有的 running 任务无 lease/reaper，将卡在 running；优雅停机路径已被 P1-IT-14 覆盖 | **已关闭（2026-10-10）**：部署形态代码对齐落地——quickstart 内嵌 API 进程 / 生产独立 `memory-worker` 容器（`apps/worker/memory.py` + production compose 服务）；lease/reaper 按 Phase 4 设计 §4 提前实施（claim 写 owner + `lease_expires_at=now()+120s`、心跳续约、claim 前置 reaper，attempts 达上限者直接置 failed），覆盖用例 P1-IT-15~21 |

## 9. CI 证据

- PR：<https://github.com/GroundedCore/nexusdesk/pull/1>（draft，head `0d0b6b3`，与本机 HEAD 一致）
- 通过 run：<https://github.com/GroundedCore/nexusdesk/actions/runs/38025292718>（2026-10-10T04:47:35Z，conclusion=success）
  - Backend (ruff + pytest) job：Lint / Migrate the test database / Test 等全部步骤 conclusion=success（job 114134745820）
  - Frontend job：success（job 114134745579）
- 前序失败 run：<https://github.com/GroundedCore/nexusdesk/actions/runs/38024854835>（sha `5181abe`，failure，即 §7 缺陷发现轮）
- 核验方式与边界：本机无 `gh` CLI，经 GitHub REST API 核验 run/job/step conclusion；job 日志下载返回 403（需 admin），**278 passed / 1 skipped 的计数采用开发记录、未能逐行复核**，但 Test 步骤 conclusion=success 等价于 pytest 退出码为 0（套件全绿）。

## 10. 修订轮：部署形态对齐（2026-10-10，`ae82a65` 文档修订触发）

**触发原因**：`ae82a65` 修订 Memory Worker 部署形态（quickstart 内嵌 API 进程、生产独立 `memory-worker` 常驻容器），并将 Phase 4 设计 §4 的租约/reaper 提前落地；代码按修订文档对齐（提交 `d43a02b`..`e863e34`；回填前分支 rebase 至 main 最新基线，等价 HEAD 为 `6329518c`，阶段 1 代码内容不变——`git diff e863e34 6329518c` 对 memory 相关路径为空）。本轮为其测试执行与验收复核记录。

### 10.1 新增/更新用例执行结果

| 编号 | 用例 | 执行环境 | 结果 |
|---|---|---|---|
| P1-UT-12 | 内嵌装配开关 `embedded_worker × summary_enabled` 四组合 | 本机 | 通过（2026-10-10 验收代理复跑） |
| P1-IT-13（扩展） | 多副本并发 claim：SKIP LOCKED 恰好一方获胜且持有 owner | CI（PG） | 通过（CI run 38043500062） |
| P1-IT-14（扩展） | 优雅停机重置 running 并清空 owner/lease | CI（PG） | 通过（CI run 38043500062） |
| P1-IT-15 | 内嵌形态（`embedded_memory_worker` 装配）优雅停机重置 running | CI（PG） | 通过（CI run 38043500062） |
| P1-IT-16 | 独立入口 `python -m agent_platform.apps.worker.memory` 端到端执行并干净停机 | CI（PG） | 通过（CI run 38043500062） |
| P1-IT-17 | claim 写 owner 且 lease≈now()+120s | CI（PG） | 通过（CI run 38043500062） |
| P1-IT-18 | reaper 重置租约过期 running、健康租约不动 | CI（PG） | 通过（CI run 38043500062） |
| P1-IT-19 | 心跳续约前移租约、另一副本 reaper 不误收割 | CI（PG） | 通过（CI run 38043500062） |
| P1-IT-20 | 被收割任务重认领并执行至 done、摘要落库 | CI（PG） | 通过（CI run 38043500062） |
| P1-IT-21 | reaper 对 attempts 达上限任务直接置 `failed(memory_worker_lost)` | CI（PG） | 通过（CI run 38043500062） |
| P1-CM-05 | 迁移 0030 含 `lease_expires_at`（列存在、可空、默认 NULL） | CI（PG） | 通过（CI run 38043500062） |
| `test_deployment.py` 拓扑断言 | 生产 compose `memory-worker` 服务存在、入口 command 正确、`AGENT_EMBEDDED_WORKER=false`、quickstart 仍仅 postgres+app 两容器 | 本机 | 通过（29 passed 含全部部署用例） |

说明：本机无 PG/Docker 属既定环境约束，上表全部 postgres 用例于 CI（Backend job，postgres:17 服务）首次执行并全绿，证据见 §10.4。

### 10.2 本地门槛复跑（验收代理亲自执行，2026-10-10，工作目录 `backend/`）

- `uv run ruff check .` → `All checks passed!`
- `uv run pytest -q` → `122 passed, 165 skipped`（postgres 标记按 §1 约束 skip，skip 数含本轮新增的 7 个 IT 用例与 CM-05）
- `uv run pytest tests/test_memory_summary.py tests/test_deployment.py -q` → `29 passed`
- `uv run pytest tests/test_memory_postgres.py tests/test_memory_migrations.py --collect-only -q` → 26 个用例全部收集

### 10.3 本轮迭代缺陷记录

| # | 级别 | 缺陷 | 发现环节 | 修复 | 回归 |
|---|---|---|---|---|---|
| D3 | P3（测试缺口） | QA 对抗性复核第 1 轮：租约与部署形态用例断言不充分（claim 租约窗口、心跳防误收割、部署拓扑断言等缺实质判别） | QA 评审轮 1 | `287fe05` 补强 P1-IT-17~19 与 `test_deployment.py` 断言 | 纳入本轮用例集，CI 通过（run 38043500062） |
| D4 | P2 | reaper 原实现把租约过期的 running 一律重置 pending：已耗尽 attempts 的任务会被无限重认领（硬杀即复活循环） | QA 对抗性复核第 2 轮 | `e863e34`：reaper 按 `attempts>=max_attempts` 分流置 `failed(memory_worker_lost)`（对齐 knowledge worker `attempts>=3→failed` 先例），并为 `_finish` 加 owner 守卫防租约被收割后误写 | 新增 P1-IT-21；CI 通过（run 38043500062） |

QA 第 3 轮复核未发现新缺陷；回归面确认 `summary.py`/`engine.py`/handoff 本轮零改动（`git diff ae82a65..HEAD` 为空），P1-IT-01~12 既有用例全部保留。

### 10.4 CI 证据（已解除阻塞，全绿）

- 解除经过：PR #1 于 2026-10-10T08:14:50Z 关闭（未合并）后，分支 rebase 至 main 最新基线（阶段 1 代码内容不变，见 §10 触发原因），随后手动新建 PR #2（<https://github.com/GroundedCore/nexusdesk/pull/2>，head `6329518c`），`pull_request` 事件触发 CI。
- 通过 run：<https://github.com/GroundedCore/nexusdesk/actions/runs/38043500062>（head `6329518c`，event=pull_request，conclusion=success，2026-10-10）
  - Backend (ruff + pytest) job：Lint / Migrate the test database / Test 全部步骤 conclusion=success——Test 步骤在 postgres:17 服务上执行全量套件，P1-IT-13~21、P1-CM-05 及迁移用例**首次在真实 PG 执行并通过**（Test success 等价于 pytest 退出码 0，套件全绿）
  - Frontend job：success
- 核验方式与边界：本机无 `gh` CLI，经未认证 GitHub REST API 核验 run/job/step conclusion（2026-10-10）；job 日志下载返回 403（需 admin），逐步骤用例计数未能逐行复核。
- PR 状态：PR #2 保持 open，待人工 code review；本回填仅登记测试证据，不代表合并。

### 10.5 R3 关闭的实现方式与测试证据（验收侧复核）

§8 R3 行已登记关闭，此处补验收复核记录。实现方式：claim 单事务内「reaper 前置 + `FOR UPDATE SKIP LOCKED` 子查询」，写 owner + `lease_expires_at=now()+120s`；执行期间 30s 心跳续约（相对 120s 租约 4 倍冗余）；`_finish` 以 id+owner 为守卫防止租约被收割后误写状态/审计；`reset_running` 按 owner 无条件重置，独立/内嵌两种形态的优雅停机均覆盖；生产侧独立容器 `apps/worker/memory.py` + production compose `memory-worker` 服务（复用后端镜像锚点、无新中间件）。测试证据：P1-IT-15~21、P1-CM-05、P1-UT-12、`test_deployment.py` 拓扑断言（§10.1）。验收代理代码复核结论与开发自述一致，SQL 语义对照 `knowledge/ingestion.py` 先例（`docs/modules-delivery.md` §知识库 Worker：SKIP LOCKED + 120s 租约 + reap + 三次失联置 failed）逐点相符。

## 附录 A 本地执行输出快照（2026-10-10，工作目录 `backend/`）

A-1 `uv run ruff check .`

```text
All checks passed!
```

A-2 `uv run pytest -q`

```text
121 passed, 158 skipped, 1 warning in 6.92s
```

A-3 `uv run pytest tests/test_memory_summary.py tests/test_memory_migrations.py -v`（摘要）

```text
test_p1_ut_01_prompt_without_old_summary_contains_all_dropped_messages PASSED
test_p1_ut_02_prompt_with_old_summary_merges_both_sections PASSED
test_p1_ut_03_blank_model_output_is_rejected PASSED
test_p1_ut_04_oversized_output_keeps_tail PASSED
test_p1_ut_07_dropped_messages_trimmed_to_max_input PASSED
test_p1_ut_08_injection_truncates_to_token_budget_keeping_tail PASSED
test_p1_ut_09_profile_binding_selection PASSED
test_p1_ut_10_backoff_grows_exponentially_and_is_capped PASSED
test_p1_cm_02_summary_settings_defaults_and_env_override PASSED
test_agent_config_summary_enabled_defaults_on_and_validates_legacy_payloads PASSED
test_p1_it_03_engine_injects_summary_between_prompt_and_history PASSED
test_engine_skips_empty_or_disabled_summary PASSED
test_engine_injection_obeys_token_budget PASSED
test_engine_reports_dropped_messages_when_window_overflows PASSED
test_engine_reports_no_dropped_messages_inside_window PASSED
test_demo_model_never_calls_tools_the_caller_did_not_offer PASSED
test_summary_migration_preserves_existing_conversations SKIPPED（需 PG，CI 执行通过）
======================== 16 passed, 1 skipped in 1.01s =========================
```

A-4 `uv run pytest tests/test_memory_postgres.py --collect-only -q`：18 个用例全部收集（本机 skip、CI 全量执行）。

## 附录 B 引擎层验收证据脚本输出快照

验收脚本（mock 录音模型，无需数据库）复现 A1/A3/A5/A6 的引擎层语义，命令：`cd backend && uv run python <脚本>`，完整输出：

```text
=== A1（引擎层）：25 轮对话，早期事实经摘要进入模型输入 ===
总历史 50 条；滑动窗口保留 20 条；被丢弃 30 条
早期事实是否已从窗口丢失: True
模型输入消息序列角色: ['SystemMessage', 'SystemMessage', 'HumanMessage', 'AIMessage', ... 'HumanMessage']
注入消息头: 【此前对话摘要】
注入消息含订单号: True
注入消息含过敏信息: True
run 正常完成: True
A1（引擎层）PASS

=== A3（引擎层）：开关关闭不注入，开启后恢复 ===
开关关闭时 system 消息数（应=1，仅 prompt）: 1
开关开启时 system 消息数（应=2，prompt+摘要）: 2
A3（引擎层）PASS

=== A5（引擎层）：窗口内对话不产生被丢弃消息 ===
5 轮历史 10 条，dropped=0（投递谓词 dropped>0 不成立）
A5（引擎层）PASS

=== A6（引擎层）：超长摘要注入被截断在 800 tokens 预算内 ===
原始摘要 4016 字符 → 注入正文 1600 字符（预算 1600）
尾部（近期信息）保留: True
模型调用正常完成: True
A6（引擎层）PASS

ALL ENGINE-LEVEL ACCEPTANCE SCENARIOS PASS
```
