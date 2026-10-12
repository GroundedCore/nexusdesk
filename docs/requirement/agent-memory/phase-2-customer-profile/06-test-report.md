# Phase 2 · 客户实体 + 画像/偏好记忆 — 测试报告

> 状态：进行中（本机全量验证含真实 PG 已完成，发现 1 个 P2 缺陷 D1 修复中；CI 首轮失败根因已定位=D1，待修复回归）
> 创建：2026-10-12
> 依据：`04-test-plan.md`（P2-UT-01~12、P2-IT-01~13、P2-API-01~06、P2-CM-01~04、§6 前端、§8 回归、§9 出口准则）、`02-requirements.md`（FR-1~FR-6、AC-1~AC-8）
> 执行：QA 测试代理（自动化 + 对抗性代码复核），开发自报结果一律独立复跑验证

## 1. 测试范围与环境

**范围**：客户实体与身份绑定（`memory_customers` / `memory_identities`）、画像/偏好记忆的自动抽取/存储/注入（`memory_items`）、控制台记忆管理（API + 前端）、工单 `customer_id` 联动、Agent 级读写开关、迁移 `0035_memory_customers`（原 0031，因 main 原生渠道集成撞号重编号）。

| 环境 | 配置 | 用途 |
|---|---|---|
| 本机（macOS，Python 3.11/3.12 venv） | 初始无 PostgreSQL、无 Docker daemon | ruff 全量；非 postgres 单测与引擎层用例；前端三门槛与 Playwright |
| 本机 Docker PG（用户 2026-10-13 提供） | 容器 `nexusdesk-test-pg`（postgres:17-alpine，127.0.0.1:55432），库 `nexusdesk_test` 迁移至 head（0035） | 全量套件（含 P2-IT/P2-API/P2-CM postgres 用例），复刻 CI Backend job 环境变量 |
| CI（GitHub Actions，ubuntu + postgres:17 service） | `TEST_DATABASE_URL` 指向迁移后的 PG 17 | 全量套件首轮执行（失败=D1，见 §9） |
| 模型 | demo/mock（`RecordingGateway` 可编程：合法 JSON、非法 JSON、错误、各档 confidence） | 全部自动化用例 |

**过程备注**：① 首轮验证在提交 `3476554`（未合并 main）上完成；main 合入原生渠道集成后（迁移 0031~0034、本阶段迁移重编号 0031→0035），于合并提交 `fe12121` 上全部复跑。② 本机 Playwright 浏览器缓存两次被外部清空导致运行中断，重装后复跑，相关失败不计入结果。③ 并发高负载下全量 e2e 与 PG pytest 曾相互拖慢（一处 main 侧用例假挂起，单独复跑 3.69s 通过，证实为负载 flake，见 §11 R2）。

## 2. 执行总览

| 项 | 结果 | 证据 |
|---|---|---|
| 本机 `uv run ruff check .`（合并后树 `fe12121`） | All checks passed | 附录 A-1 |
| 本机 `uv run pytest -q`（无 PG，`fe12121`） | 155 passed, 208 skipped, 0 failed | 附录 A-2 |
| 本机 Docker PG memory 全套（Phase 1+2 memory PG + 迁移） | 53 passed, 2 failed（=D1 的 api_04/api_05） | 附录 A-4 |
| 本机 Docker PG 全量 `uv run pytest -q`（CI 复刻，空闲机器） | 进行中，结果回填 §9 | 附录 A-5 |
| 前端 `npm run typecheck` / `check:i18n` / `build`（`fe12121`） | 全过（i18n 1285 keys × 4 语言；build 仅 chunk 体积警告） | 附录 B-1 |
| Playwright `tests/memory.spec.ts` | 4 passed（§6 前端用例全绿） | 附录 B-2 |
| Playwright 全量 e2e（合并树） | 43 passed, 28 failed（失败均为登录墙/真实后端类预存项，baseline 对照见 §8） | 附录 B-3 |
| CI 首轮（run 38272790040，head `fe121215`） | Backend Test 步骤 failure（=D1）；Migrate 成功；Frontend success | §9 |

## 3. 单元测试（P2-UT）逐条结果

执行环境除标注外均为本机（无 PG 直接执行；DB 绑定用例经本机 Docker PG 执行，全部通过）。

| 编号 | 用例 | 测试位置 | 执行环境 | 结果 |
|---|---|---|---|---|
| P2-UT-01 | `resolve_customer` 未命中同事务建 customer+identity | `test_memory_customers_postgres.py::test_p2_ut_01_*` | 本机 PG | 通过 |
| P2-UT-02 | 命中复用不新建；同来源不同键=不同客户（保守） | `test_p2_ut_02_*` | 本机 PG | 通过 |
| P2-UT-03 | 并发唯一冲突败者重查返回胜者、孤儿 customer 清理 | `test_p2_ut_03_*` | 本机 PG | 通过 |
| P2-UT-04 | 三来源 external_key 规则；渠道与会话 external_id 同源哈希（公共函数两处一致） | `test_memory_customers.py::test_p2_ut_04_*`（2 例） | 本机 | 通过 |
| P2-UT-05 | 非法 JSON 整批丢弃（含非数组、截断）；code fence 容忍 | `test_p2_ut_05_*`（2 例） | 本机 | 通过 |
| P2-UT-06 | 单条缺字段/类型非法按条跳过、合法保留；noop 允许空 content（固化实现决策） | `test_p2_ut_06_*`（2 例） | 本机 | 通过 |
| P2-UT-07 | confidence 路由边界 0.8→active / 0.5→pending / 0.49→丢弃 | `test_p2_ut_07_*` | 本机 | 通过 |
| P2-UT-08 | 同键 update：content 覆盖、prev_content=旧值、revision+1、last_referenced_at 刷新 | `test_memory_customers_postgres.py::test_p2_ut_08_*` | 本机 PG | 通过 |
| P2-UT-09 | op=add 撞键 ON CONFLICT DO NOTHING 不堆叠（UNIQUE 兜底） | `test_p2_ut_09_*` | 本机 PG | 通过 |
| P2-UT-10 | 受控词表 + `custom_*` 扩展 + 非法 key 拒绝 | `test_memory_customers.py::test_p2_ut_10_*` | 本机 | 通过 |
| P2-UT-11 | 条数封顶 20、token 预算裁剪、首条保底 | `test_p2_ut_11_*`（2 例） | 本机 | 通过 |
| P2-UT-12 | 画像/偏好分节渲染、空分组不渲染、空列表不注入 | `test_p2_ut_12_*` | 本机 | 通过 |
| （增补）人工编辑保护 | 抽取 update 永不覆盖 `source_type='human'`（实现决策②专测） | `test_extracted_update_never_clobbers_human_edit` | 本机 PG | 通过 |

## 4. 集成测试（P2-IT）逐条结果

全部位于 `test_memory_customers_postgres.py`，本机 Docker PG 执行，全部通过。

| 编号 | 用例 | 测试位置 | 结果 |
|---|---|---|---|
| P2-IT-01 | 渠道回头客：身份解析→抽取落库→再进线注入画像+偏好；external_id 与 identity 同源；trace 含 `memory_extract.queued` | `test_p2_it_01_*` | 通过 |
| P2-IT-02 | 开放平台身份独立建档，与渠道互不串扰 | `test_p2_it_02_*` | 通过 |
| P2-IT-03 | 身份解析异常不阻断会话，customer_id 置空 + warning，零抽取任务 | `test_p2_it_03_*` | 通过 |
| P2-IT-04 | playground / 示例 Agent 零解析、零任务、零写入（常驻断言） | `test_p2_it_04_*` | 通过 |
| P2-IT-05 | 抽取去抖：窗口内 1 个任务；改库老化后可再投递（不等待） | `test_p2_it_05_*` | 通过 |
| P2-IT-06 | 改口场景同键 update 不堆叠，revision=2、prev_content 正确 | `test_p2_it_06_*` | 通过 |
| P2-IT-07 | confidence=0.6→pending 不注入；控制台确认后注入出现 | `test_p2_it_07_*` | 通过 |
| P2-IT-08 | 注入位置：摘要之后、历史之前，与 Phase 1 摘要共存 | `test_p2_it_08_*` | 通过 |
| P2-IT-09 | Agent 开关分离：memory_write=false 不投递；memory_enabled=false 不注入但抽取照常 | `test_p2_it_09_*` | 通过 |
| P2-IT-10 | 全局 memory_extract_enabled=false 零任务（身份解析不受影响） | `test_p2_it_10_*` | 通过 |
| P2-IT-11 | 绑定客户的会话工单联动：payload/approve/列表/详情均含 customer_id（str(UUID) 兼容 VARCHAR(100)） | `test_p2_it_11_*` | 通过 |
| P2-IT-12 | 无客户会话工单 customer_id 为空、流程不受影响 | `test_p2_it_12_*` | 通过 |
| P2-IT-13 | 存量会话（customer_id=NULL）正常对话、不解析、不报错 | `test_p2_it_13_*` | 通过 |
| （增补）抽取重试 | 模型报错→重试→failed，审计 action 按 kind 区分 | `test_extraction_failure_retries_then_fails_with_kind_audit` | 通过 |

## 5. API 测试（P2-API）逐条结果

全部位于 `test_memory_customers_postgres.py`，本机 Docker PG 执行。

| 编号 | 用例 | 测试位置 | 结果 |
|---|---|---|---|
| P2-API-01 | 8 端点鉴权矩阵：匿名 401、viewer 写操作 403、operator 全通 | `test_p2_api_01_*` | 通过 |
| P2-API-02 | 客户列表 q= 搜索（external_key 匹配）、分页、条数与最近活跃 | `test_p2_api_02_*` | 通过 |
| P2-API-03 | 编辑：source_type='human'、confidence=1.0、prev_content 保留、审计无内容 | `test_p2_api_03_*` | 通过 |
| P2-API-04 | 软删除注入消失、幂等重复删、restore 恢复注入 | `test_p2_api_04_*` | **失败（D1）**：主体断言全过，仅末尾"非删除态 restore 应 409"失败（实现按幂等返回 200） |
| P2-API-05 | pending 队列 confirm→active / reject→deleted / 列表仅 pending | `test_p2_api_05_*` | **失败（D1）**：主体断言全过，仅末尾"已决策条目二次决策应 409"失败（同上） |
| P2-API-06 | 租户隔离：跨租户 customer/item 404 与 service 层 DomainError，不泄露存在性 | `test_p2_api_06_*` | 通过 |

## 6. 前端测试（§6）逐条结果

`frontend/tests/memory.spec.ts`，本机 vite dev server（5173）+ chromium headless，API 全 mock。4/4 通过。

| 用例 | 覆盖点 | 结果 |
|---|---|---|
| memory console（admin） | 列表/搜索/详情/编辑保存/软删除/恢复/pending 确认/`source_ref` 来源会话链接 | 通过 |
| memory console（viewer） | 只读：编辑/删除/恢复/确认/拒绝按钮均不渲染 | 通过 |
| ticket detail links to customer memory | 工单详情"查看客户记忆"链接跳转客户详情 | 通过 |
| conversation workbench customer drawer（US-3） | 画像抽屉仅展示 active 条目（deleted 不出现）、跳转记忆页 | 通过 |

## 7. 通用核对项（P2-CM）逐条结果

| 编号 | 核对项 | 测试位置 | 执行环境 | 结果 |
|---|---|---|---|---|
| P2-CM-01 | 迁移 0035 纯增量、存量保留、可回滚；alembic 单头 | `test_memory_customer_migrations.py` + `uv run alembic heads` | 本机 PG | 通过（单头 `0035_memory_customers` 确认；CI Migrate 步骤亦成功） |
| P2-CM-02 | `memory_extract_*` / `memory_confidence_*` / `memory_inject_max_items` 默认值与 `AGENT_` 覆盖 | `test_memory_customers.py::test_p2_cm_02_*` | 本机 | 通过 |
| P2-CM-03 | 抽取落库/编辑/删除/恢复均留审计且 payload 无记忆内容 | `test_memory_customers_postgres.py::test_p2_cm_03_*` | 本机 PG | 通过 |
| P2-CM-04 | 抽取调用落 `gateway_calls`（actor='memory'，可归因租户）；非法 JSON 批丢弃仍计量 | `test_p2_cm_04_*` | 本机 PG | 通过 |

## 8. 回归范围

- 本机无 PG 全量 155 passed / 208 skipped / 0 failed（合并后树；含 conversation / runtime / channel / ticket / gateway / knowledge 等既有套件）。
- 本机 Docker PG：Phase 1 memory PG 全套（`test_memory_postgres.py` + `test_memory_migrations.py`）随 memory 定向跑批全绿（53 passed 中含）；全量 PG 回归进行中，结果回填。
- Phase 1 摘要链路：注入顺序共存由 P2-IT-08 覆盖，通过。
- 渠道合并影响面：合并后身份解析位于 `_accept_envelope()` 单点建会话处（原生渠道与 webhook 入口均覆盖），`_external_id` 委托 `session_id_hash()[:32]` 与记忆 identity key 同源（代码复核确认；P2-UT-04 固化该同源关系；P2-IT-01 在 PG 上验证端到端一致）。
- 前端全量 e2e（合并树）：43 passed / 28 failed。28 个失败逐一复核失败特征（90s 超时等待登录态/真实后端数据的元素），均属任务启动时已确认的预存类别（登录墙/真实后端依赖），与本阶段改动无关；memory/hash-routing/channel-navigation 等本阶段相关用例全绿。预存声明为 26：该数字来自合并前树的对照实验，main 原生渠道集成自身可能带来 +2。已按协调者指示在 `c3ba708`（main tip，含渠道集成）建 worktree 跑 baseline 对照，结论回填。

## 9. CI 证据与首轮失败定位

- 首轮 run：<https://github.com/GroundedCore/nexusdesk/actions/runs/38272790040>（head `fe121215`，event=pull_request，conclusion=failure）
  - Backend (ruff + pytest) job：Lint / Migrate the test database 成功，**Test 步骤 failure**
  - Frontend job：success
- 失败定位：job 日志下载 403（需 admin，同 Phase 1 边界），check-runs 无注解。用户 2026-10-13 提供本机 Docker PG 后，QA 复刻 CI 环境（同镜像 postgres:17、同环境变量、alembic upgrade head 后全量 pytest），**稳定复现且仅复现 2 个失败**——`test_p2_api_04` / `test_p2_api_05`（`assert 200 == 409`），即缺陷 D1（§10/§11）。CI Test 步骤失败与本机复现一致。
- D1 修复推送后 CI 自动复跑，结果回填。

## 10. 关键实现决策对抗性复核（开发自报 5 项）

| # | 决策 | 复核方式 | 结论 |
|---|---|---|---|
| ① | `tenant_id` 用 TEXT（设计文档误写 UUID） | 迁移 0035 与存量全部表一致；CI Migrate 步骤与本机 PG 迁移均成功 | 正确 |
| ② | 抽取 update 永不覆盖 `source_type='human'` 条目 | `MemoryService.apply_extracted` 的 UPDATE 带 `source_type<>'human' AND status<>'deleted'` 守卫；专测在 PG 上断言 update 被吞为 noop、人工内容不变 | 正确，测试覆盖到位 |
| ③ | 身份解析失败 savepoint 隔离不阻断会话 | `conversation/service.py` 以 `c.begin_nested()` 包裹 `resolve_customer`，异常降级 `customer_id=NULL` + warning；playground/示例 Agent 在解析点与抽取执行点双重排除；P2-IT-03/04 PG 实测通过 | 正确 |
| ④ | 审计只记键不记内容 | service 层全部 audit 调用仅含 customer_id/type/key/counts/usage；P2-CM-03 在 PG 上断言 details 无内容字符 | 正确 |
| ⑤ | 防抖窗口内任意状态抽取任务都阻止新任务 | `enqueue_extract_task` 窗口查询无 status 谓词（代码语义确认）；P2-IT-05 覆盖 pending 态阻止 | 正确；轻微测试缺口见 §11 R1 |

## 11. 缺陷与偏差记录

| # | 级别 | 缺陷 | 发现环节 | 修复 | 回归 |
|---|---|---|---|---|---|
| D1 | P2 | `_set_status` 幂等语义不一致：restore/confirm/reject 对"当前状态==目标状态"的重复决策返回 200（一律幂等），而测试与设计错误码契约（`memory_item_not_deleted`/`memory_item_not_pending`）要求 409——决策类操作不可重复，仅 delete 幂等（安全重试）。数据无损坏、无泄漏；api_04/05 主体功能（删除/恢复/确认/拒绝/队列）全部正确，仅重复决策边界不符 | CI 首轮 Backend Test 失败；本机 Docker PG 复刻环境稳定复现（`assert 200 == 409` ×2）。根因：PG 用例此前从未在任何环境执行过（开发本机无 PG 全 skip，首跑即 CI） | 协调者裁决改实现（QA 建议方案 a）：决策类重复返回 409，delete 保持幂等。已派 dev-backend | 待修复推送后 QA 回归（本机 PG + CI 复跑） |

| # | 级别 | 残留风险/观察 | 处置 |
|---|---|---|---|
| R1 | P3（测试缺口） | 决策⑤"任意状态阻止新投递"仅有 pending 态专测断言（P2-IT-05）；done/failed 态在窗口内阻止新投递无专测，行为经代码阅读确认正确（窗口查询无 status 谓词） | 记录，建议后续增补构造用例 |
| R2 | P3（环境观察） | 高负载并发下（全量 e2e + PG pytest 同跑）全量 pytest 在 main 侧用例 `test_model_gateway.py::test_timeout_and_cancellation_release_slots` 处两次假挂起；机器空闲后单独复跑 3.69s 通过，证实为负载敏感 flake（该用例使用 0.08s 超时 profile），非本阶段缺陷 | 记录；执行侧规避：PG 全量与 e2e 不并发 |
| R3 | P3（过程改进） | 28 个 PG 用例在开发自报阶段从未真实执行（本机无 PG 全 skip），D1 因此直到 CI 才暴露 | 建议后续阶段：含 PG 用例的分支在自报"全绿"前至少在本机 Docker PG 或等价环境跑一遍目标套件（本机 `nexusdesk-test-pg` 容器已证明可行） |

## 12. 出口准则核对（§9）

| 准则 | 状态 |
|---|---|
| 全部用例通过 | 暂不满足：D1（2 用例）修复回归前 CI 红 |
| AC-1~AC-8 复现通过 | AC 溯源用例（P2-IT-01/02/04/05/06/07/09/11、P2-UT-02、P2-API-03/04/05 主体）本机 PG 实测通过；AC-6/FR-4 的重复决策边界随 D1 回归收尾 |
| 防污染与租户隔离断言常驻 | P2-IT-04 / P2-API-06 在套件内常驻，本机 PG 实测通过 |
| 无 P0/P1 未关闭 | 满足：全部验证未发现 P0/P1（无跨租户泄漏、无身份错绑、主流程无破坏；人工编辑保护实测有效） |

## 附录 A 后端执行输出快照

A-1 `uv run ruff check .`（`fe12121`）

```text
All checks passed!
```

A-2 `uv run pytest -q`（无 PG，`fe12121`）

```text
155 passed, 208 skipped, 1 warning in 9.70s
```

A-3 memory + 迁移定向（无 PG，PG/迁移用例 skip）

```text
34 passed, 34 skipped in 3.25s
```

A-4 本机 Docker PG memory 全套（`test_memory_customers_postgres.py` + `test_memory_customer_migrations.py` + `test_memory_postgres.py` + `test_memory_migrations.py`，CI 同环境变量）

```text
2 failed, 53 passed in 167.21s
FAILED test_memory_customers_postgres.py::test_p2_api_04_soft_delete_and_restore_control_injection - assert 200 == 409
FAILED test_memory_customers_postgres.py::test_p2_api_05_pending_queue_confirm_and_reject - assert 200 == 409
```

A-5 本机 Docker PG 全量（CI 复刻，空闲机器）：进行中，结果回填。

A-6 挂起用例单独复跑：`tests/test_model_gateway.py::test_timeout_and_cancellation_release_slots` → `1 passed in 3.69s`（负载 flake 排除，见 §11 R2）。

## 附录 B 前端执行输出快照

B-1 `npm run typecheck && npm run check:i18n && npm run build`（`fe12121`）：tsc 无错误；`Verified 1285 translations in zh-CN, zh-TW, en, hi`；`✓ built in 9.45s`。

B-2 `npx playwright test tests/memory.spec.ts`（dev server 5173，chromium）：`4 passed (22.4s)`。

B-3 全量 e2e（合并树，71 用例）：`28 failed, 43 passed (34.6m)`。失败清单（均为登录墙/真实后端依赖的预存类别）：agent-filters、agent-model-required、agents-delete(3)、agents-fullstack、agents-workspace(2)、deployment banner、gateway-fullstack、i18n(4)、model-gateway-ui(9)、model-gateway、platform、tickets-antd(2)。baseline（c3ba708）对照进行中，结论回填 §8。
