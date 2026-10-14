# Phase 3 · 事件记忆 + 语义检索 — 测试报告

> 状态：已完成（双模式矩阵本地全绿；D1/D2 修复回归关闭；部署替换/回退按 R16/R17 实测通过；出口准则逐条满足，CI pgvector 矩阵按 R13 为后续项）
> 创建：2026-10-15
> 依据：`04-test-plan.md`（P3-UT-01~11、P3-IT-01~12、P3-DP-01~05、P3-CM-01~04、§7 回归、§8 出口准则，含 R14/R16/R17 三处修订）、`02-requirements.md`（FR-1~FR-5、AC-1~AC-7、AC-4b）、`08-implementation-breakdown.md`（R1~R17 裁定）
> 执行：QA 测试代理（自动化 + 真实 Docker 卷替换/回退实验 + 代码复核），开发自报结果一律独立复跑验证

## 1. 测试范围与环境

**范围**：工单事件自动写入（创建/解决/关闭/承诺，`event_write` 异步任务）、`memory_search` 内置工具、pgvector/TSVECTOR 双模式检索与时间衰减排序、高优事件常驻注入（`【待跟进事项】`）、事件 TTL 清理、embedding 失败降级与重试、迁移 `0036_memory_events` 探测块、pgvector 镜像部署变更（新装/替换/回退）。

| 环境 | 配置 | 用途 |
|---|---|---|
| 本机（macOS，uv 管理 Python） | 无 PG 直接执行 | ruff 全量；非 postgres 单测（衰减函数、渲染、工具 schema、配置加载） |
| 本机 Docker PG A（TSVECTOR 模式） | 容器 `nexusdesk-test-pg`（postgres:17-alpine，127.0.0.1:55432），库 `nexusdesk_test` 迁移至 head（0036） | text 模式全量用例；P3-DP-02；全量后端回归 |
| 本机 Docker PG B（vector 模式） | 容器 `nexusdesk-test-pgvector`（pgvector/pgvector:pg17，127.0.0.1:55433），库 `nexusdesk_test` 迁移至 head（0036） | vector 模式全量用例；P3-DP-01/05；全量后端回归 |
| 本机 Docker 一次性实验容器 | 上述两镜像 + 独立命名数据卷 | P3-DP-03/04 真实卷替换与回退实验 |
| 模型 | demo/mock（`RecordingGateway`、可编程 `StubEmbedGateway`：正常/报错/维度不匹配） | 全部自动化用例 |

**过程备注**：① 时间类用例（TTL、衰减排序）一律直接 `UPDATE expires_at` / `last_referenced_at` 构造，无真实等待。② 全量回归首次跑批与 P3 套件并发竞争负载，出现 3 例负载敏感失败（含 Phase 2 R2 已记录的 main 侧 flake 用例），按 R2 处置改为独占机器重跑，见 §8。③ 执行期间 lead 两项裁定落地：R16（镜像替换后需手工幂等 DDL 才切 vector）与 R17（回退前必须在 pgvector 镜像上执行清理 DDL），04 相应条目由 QA 按裁定修订后执行。④ CI 仍为单个 `postgres:17` service；pgvector 矩阵 job 按 R13 裁定为不阻塞验收的后续项（§8）。

## 2. 执行总览

| 项 | 结果 | 证据 |
|---|---|---|
| 本机 `uv run ruff check .`（HEAD `7f18ac6`） | All checks passed | 附录 A-1 |
| P3 套件 · vector 模式（pgvector 容器，`7f18ac6`） | **58 passed, 2 skipped, 0 failed**（skip 为无列探测变体，按设计门控） | 附录 A-2 |
| P3 套件 · text 模式（alpine 容器，`7f18ac6`） | **47 passed, 13 skipped, 0 failed**（skip 为 vector 专属用例，按设计门控） | 附录 A-3 |
| 迁移 0036 用例（`test_memory_event_migrations.py`，双容器各一遍） | 2 passed ×2（探测块按镜像能力分支断言） | 附录 A-4 |
| 全量后端回归 · alpine 容器（`7f18ac6`，独占机器单次完整跑批） | **410 passed, 14 skipped, 1 deselected（R2 flake 单独计次）, 0 failed** | 附录 A-5 |
| 全量后端回归 · pgvector 容器（`7f18ac6`，独占机器单次完整跑批） | **421 passed, 3 skipped, 1 deselected（R2 flake 单独计次）, 0 failed** | 附录 A-6 |
| 前端 `npm run typecheck` / `check:i18n` / `build` | 全过（i18n 1288 keys × 4 语言；build 仅 chunk 体积警告） | 附录 B-1 |
| Playwright 相关子集（memory / tickets-antd / agents-workspace） | memory 4/4 通过；tickets-antd(2) + agents-workspace(2) 失败均为 Phase 2 已记录的预存类别（90s 超时等登录态/真实后端元素），零新增 | 附录 B-2 |
| P3-DP-03 卷替换实验（postgres→pgvector 同卷 + README 手工 DDL） | 通过（数据保留→探测 text→手工 DDL 后探测 vector、新条目生成 embedding） | 附录 C-1 |
| P3-DP-04 卷回退实验（清理 DDL 后 pgvector→postgres） | 通过（服务可用、探测 text、数据保留）；未清理直接回退的破坏性如实记录 | 附录 C-2 |
| quickstart/根 compose 静态核验 | 默认 `pgvector/pgvector:pg17`、`POSTGRES_IMAGE` 可覆盖、两容器形态不变、无新中间件 | 附录 C-3 |

P3 套件 = `test_memory_events.py`（QA）+ `test_memory_events_postgres.py`（QA）+ `test_memory_event_migrations.py`（QA）+ `test_memory_event_render.py`（dev）+ `test_memory_search_tool.py`（dev）+ `test_memory_store.py`（dev），双容器各执行一遍。注：附录 A-2/A-3 批次为五个非迁移文件（未含 `test_memory_event_migrations.py`），迁移文件批次见 A-4；两批合计 text 49 / vector 60 passed。

## 3. 单元测试（P3-UT）逐条结果

全部通过。DB 绑定用例在双容器各执行一遍（模式门控的变体按设计 skip）。

| 编号 | 用例 | 测试位置 | 执行环境 | 结果 |
|---|---|---|---|---|
| P3-UT-01 | Store 探测：有扩展 + 有 embedding profile → PgVectorStore | `test_memory_events_postgres.py::test_p3_ut_01_probe_vector_when_column_and_profile_ready` | pgvector 容器 | 通过（alpine 按设计 skip） |
| P3-UT-02 | Store 探测：缺任一条件 → TsVectorStore | `test_p3_ut_02_probe_text_without_vector_column` / `_when_profile_missing` / `_when_profile_not_embed`（3 例） | 双容器 | 通过（无列变体在 pgvector 容器 skip，另两例双容器均过） |
| P3-UT-03 | `memory_vector_enabled` 覆盖 | `test_p3_ut_03_vector_enabled_false_forces_text` / `_true_forces_vector_without_profile` / `_true_without_column_still_text`（3 例） | 双容器 | 通过（false 强制 text 双容器均过；true 系列按列存在性门控） |
| P3-UT-04 | 时间衰减函数 | `test_memory_events.py::test_p3_ut_04_*`（4 例：age=半衰期因子=0.5；naive 按 UTC；未来时间钳 0；永不归零） | 本机（无 PG） | 通过 |
| P3-UT-05 | 衰减只影响排序 | `test_memory_events.py::test_p3_ut_05_*`（3 例：超老条目仍返回仅排序靠后；limit 在排序后生效；相关度可压过衰减） | 本机（无 PG） | 通过 |
| P3-UT-06 | 四类事件内容渲染 | `test_memory_event_render.py`（5 例：summarize 折叠/截断；key 规则 `ticket:{no}:*` / `open_ticket:{no}` / `commitment:{log_id}`；content 模板；注入渲染排序与过滤） | 本机（无 PG） | 通过 |
| P3-UT-07 | resolved/closed 联动置 open_ticket expired | `test_memory_events_postgres.py::test_p3_it_01_ticket_lifecycle_writes_event_memory` 内断言（软过期非删除，行保留可审计） | 双容器 | 通过 |
| P3-UT-08 | `expires_at` 规则 | it_01 内断言事件类 ≈ now+`memory_event_ttl_days`(180d)；`test_p3_it_04_commitment_remark_entry` 内断言 commitment 类 NULL | 双容器 | 通过 |
| P3-UT-09 | memory_search 参数（默认 5、上限截断、空提示） | `test_memory_search_tool.py::test_schema_description_and_parameters` + `test_execute_empty_result_has_explicit_message`（本机）；`test_p3_ut_09_search_limit_clamped_to_configured_max`（>10 截断为 `memory_search_limit_max`=10） | 本机 + 双容器 | 通过 |
| P3-UT-10 | 工具注册四闸门（无 customer / memory_enabled=false / tool_names 移除 / 未注册拒绝执行） | `test_memory_search_tool.py::test_schema_is_registered_*` / `test_schema_hidden_*`（4 例）+ `test_execute_rejected_when_not_registered` | 本机（无 PG） | 通过 |
| P3-UT-11 | 双实现过滤一致性（tenant + customer + active + type=event，profile/preference 不进检索） | `test_p3_ut_11_text_store_filters_tenant_customer_status_type` / `test_p3_ut_11_vector_store_applies_same_filters`（含跨租户/跨客户/过期/profile 诱饵行） | 双容器 | 通过 |

## 4. 集成测试（P3-IT）逐条结果

全部位于 `test_memory_events_postgres.py`，双容器各执行一遍，全部通过（vector 专属断言在 alpine 按设计 skip，反之亦然）。

| 编号 | 用例 | 测试位置 | 结果 |
|---|---|---|---|
| P3-IT-01 | 工单全生命周期（渠道进线→decide approve→resolve→close：3 条事件 active、open_ticket expired、source_type='ticket'、confidence=1.0、source_ref 正确） | `test_p3_it_01_ticket_lifecycle_writes_event_memory` | 通过（双容器） |
| P3-IT-02 | 事件写入异步性（工单接口同事务仅落 `event_write` 任务、同步返回无记忆写入；Worker 执行后可查，写入延迟在轮询周期内） | `test_p3_it_02_event_write_is_asynchronous` | 通过（双容器） |
| P3-IT-03 | 无 customer 工单不产生事件任务、流程不受影响 | `test_p3_it_03_customerless_ticket_produces_no_event` | 通过（双容器） |
| P3-IT-04 | commitment 标记入口（备注 `is_commitment=true` → 承诺记忆，`source_ref`=`ticket_id:log_id`、`expires_at` NULL；未标记不产生） | `test_p3_it_04_commitment_remark_entry` | 通过（双容器） |
| P3-IT-05 | 常驻注入（`【待跟进事项】` 出现在画像/偏好之后；resolved 后下一轮消失；>5 条截断为 5 且按 updated_at 倒序） | `test_p3_it_05_pending_events_injected_then_cleared_on_resolve` + `test_p3_it_05_injection_capped_at_five_newest_first` | 通过（双容器） |
| P3-IT-06 | 工具调用端到端（demo 模型对"上次的工单处理进展"发出 memory_search tool_call → 工具执行 → 结果含目标事件 content） | `test_p3_it_06_memory_search_tool_end_to_end` | 通过（双容器各一遍） |
| P3-IT-07 | 双模式检索等价性（同一 query 两种 Store 均命中目标条目） | `test_p3_it_07_dual_mode_retrieval_equivalence` | 通过（pgvector 容器双 Store 对比；alpine 侧由 text 全套覆盖） |
| P3-IT-08 | 时间衰减排序（3 天/150 天改库构造；3 天前排前；半衰期 30→365 调整后分差比值符合 `2^((150-3)/half_life)` 公式） | `test_p3_it_08_recency_orders_equal_relevance_hits` | 通过（双容器；公式断言用英文内容，见 §9 R1） |
| P3-IT-09 | TTL 清理（改库构造过期 → maintenance → status=expired；检索与注入均不可见；commitment 不过期；事件域之外条目不受影响） | `test_p3_it_09_ttl_pass_soft_expires_and_hides` | 通过（双容器） |
| P3-IT-10 | embedding 失败降级与重试（mock 报错：条目正常写入、`embedding_failed=true`、任务本身不失败；重试成功清标记；连续失败保留标记） | `test_p3_it_10_embedding_failure_marks_then_retry_clears` + `test_p3_it_10_event_writer_survives_embedding_outage` | 通过（pgvector 容器；text 模式无 embedding 路径按设计 skip） |
| P3-IT-11 | 检索过滤（仅当前 tenant + customer + active + type=event；profile/preference 不走检索） | 同 P3-UT-11 两例（PG 层端到端断言） | 通过（双容器） |
| P3-IT-12 | 检索性能（单客户 1000 条事件，pgvector 模式 P99 ≤ 300ms 本地基准） | `test_p3_it_12_vector_search_p99_under_300ms`（`generate_series` 造 1000 条 + 统一向量，20 次采样取 P99 断言 ≤300ms） | 通过（pgvector 容器；alpine 按设计 skip） |

## 5. 部署/迁移测试（P3-DP）逐条结果

| 编号 | 用例 | 测试位置/方式 | 结果 |
|---|---|---|---|
| P3-DP-01 | pgvector 镜像全新部署 | `test_memory_event_migrations.py::test_0036_probe_block_matches_server_capability`（pgvector 容器）：迁移建 `embedding vector(1536)`（atttypmod=1536 实测）+ HNSW 索引 + `content_tsv` GIN + `embedding_failed` + `ticket_process_log.is_commitment`；P3-UT-01 探测 vector | 通过 |
| P3-DP-02 | 普通 postgres 镜像部署 | 同测试（alpine 容器）：0036 迁移正常通过、无 vector 列/索引；探测降级 text；text 模式全套 47 passed（检索/注入/TTL 全绿） | 通过 |
| P3-DP-03 | 镜像替换升级（postgres→pgvector 同卷，按 R16） | 真实 Docker 卷替换实验（附录 C-1）：数据保留、服务可用、替换后探测保持 text（DO 块只在迁移当时执行一次）；按 README 执行幂等 DDL（`CREATE EXTENSION IF NOT EXISTS vector` → `ADD COLUMN IF NOT EXISTS embedding vector(1536)` → `CREATE INDEX IF NOT EXISTS ... hnsw`）+ 配置 embedding profile 后探测切 vector、新条目生成 embedding。迁移级数据保留由 `test_0036_preserves_existing_rows_and_downgrades` 固化 | 通过 |
| P3-DP-04 | 镜像回退（pgvector→postgres，按 R17） | 真实 Docker 卷回退实验（附录 C-2）：在 pgvector 镜像上先执行清理 DDL（`DROP COLUMN embedding` → `DROP EXTENSION vector`）后切换——服务可用、探测 text、数据保留。**未清理直接回退的破坏性实测确认**：memory_items 全表不可读写（`could not access file "$libdir/vector"`），alpine 上 DROP COLUMN/INDEX 同样失败、无 SQL 级自救——README 已据此把清理 DDL 标为强制步骤 | 通过（风险如实记录） |
| P3-DP-05 | embedding 维度校验（按 R14：探测降级 + warning） | `test_p3_dp_05_probe_text_with_warning_on_dimension_mismatch`：绑定维度 ≠1536 的 embed profile 后探测降级 text 并输出明确 warning 日志（断言 `available()='text'` + caplog） | 通过（pgvector 容器） |

## 6. 通用核对项（P3-CM）逐条结果

| 编号 | 核对项 | 测试位置/方式 | 结果 |
|---|---|---|---|
| P3-CM-01 | 8 项新配置默认值与 `AGENT_` 覆盖（`memory_event_enabled`/`memory_event_ttl_days`/`memory_event_inject_max`/`memory_search_limit_max`/`memory_search_half_life_days`/`memory_vector_enabled` 等） | `test_memory_events.py::test_p3_cm_01_event_settings_defaults_and_env_override` | 通过（本机） |
| P3-CM-02 | embedding 调用走模型网关、落 `gateway_calls`（actor='memory'、operation='embed'、可归因租户） | `test_memory_events_postgres.py::test_p3_cm_02_embedding_usage_lands_in_gateway_calls` | 通过（pgvector 容器） |
| P3-CM-03 | 异步可靠性（event_write 任务遵循 memory_tasks 语义） | `test_p3_cm_03_event_task_failure_backoff_and_audit`：dispatcher 仅对 event_write 注入异常 → 退避重试 → attempts≥max_attempts(2) 置 failed + 审计 `memory.event.failed`；claim 并发安全（SKIP LOCKED）与停机重置由 Phase 1/2 worker 用例在全量回归中覆盖（§8） | 通过（双容器） |
| P3-CM-04 | deploy/README.md 降级差异文档 | 文档复核：模式差异表、手工启用 vector 幂等 DDL、回退强制清理 DDL、collation/REINDEX 双向说明齐全且与 QA 实验逐项吻合；复核发现的 2 处召回措辞瑕疵已由 dev `6305ffb` 修正后复确认 | 通过 |

## 7. 前端与部署形态

- 前端三门槛（typecheck / check:i18n / build）全绿（i18n 1288 keys × 4 语言，附录 B-1）；本阶段前端改动（记忆控制台事件条目与承诺展示，`d4b3997`）的 i18n key 覆盖在检查内。
- Playwright 相关子集（memory.spec.ts / tickets-antd.spec.ts / agents-workspace.spec.ts）：memory 4/4 通过；其余 4 例失败为 Phase 2 已记录预存类别、零新增（附录 B-2）。
- compose 静态核验（附录 C-3）：quickstart 与根 `compose.yaml` 默认 `pgvector/pgvector:pg17`、`POSTGRES_IMAGE` 覆盖有效、两容器形态与"无新中间件"承诺不变（生产 memory-worker 复用后端镜像）。

## 8. 回归范围

- 全量后端回归 · alpine 容器（TSVECTOR 模式，`7f18ac6`，独占机器）：**410 passed / 14 skipped / 1 deselected / 0 failed**（附录 A-5）。
- 全量后端回归 · pgvector 容器（vector 模式，`7f18ac6`，独占机器）：**421 passed / 3 skipped / 1 deselected / 0 failed**（附录 A-6）。
- 回归覆盖要点：Phase 1/2 memory 套件（摘要注入、画像/偏好、worker claim/重试/停机重置）、工单状态机与 process_log 既有用例（`is_commitment` 默认 false 不改行为）、全部迁移用例（alembic 单头 `0036_memory_events`；三处 main 侧迁移测试的头断言 0035→0036 为迁移落地的测试维护项，QA 顺手闭环：`test_ticket_migrations.py`、`test_agent_deletion_migrations.py`、`test_gateway_migrations.py`，`e9ca8e5`）。
- 首次全量跑批（非独占、与 P3 套件并发）：4 failed / 406 passed；其后两次独占跑批均挂起于 `test_model_gateway.py::test_timeout_and_cancellation_release_slots`（R2 预存 flake，main 单独复跑同样挂起，见 §9 R2）。处置：全量回归 deselect 该用例跑批，该用例单独计时复跑计次（branch 2/5、main 3/4 通过，失败形态均为挂起而非断言失败）。
- CI pgvector 矩阵 job：按 R13 裁定为不阻塞验收的后续项（现状 ci.yml 单 `postgres:17` service，加 `matrix.image` 即可），PR 描述需说明。

## 9. 缺陷与残留风险

| # | 级别 | 缺陷 | 发现环节 | 修复 | 回归 |
|---|---|---|---|---|---|
| D1 | P1 | `add_event_item` 覆写清 embedding 的 UPDATE 少一个逗号 → PostgresSyntaxError，**vector 模式全部 event_write 任务失败**（text 模式无 embedding 路径不受影响） | vector 容器首跑 P3-IT 套件 | `c7b8641`（补前置逗号） | 已关闭：定向 3/3 复测通过后全套 vector 57 passed |
| D2 | P1 | 事件内容覆写后旧 embedding 残留（content 已变、向量未清），vector 召回按过期语义错配 | QA 代码复核 + 构造复现 | `97b74f4`（覆写时清 embedding） | 已关闭：`test_add_event_item_overwrite_clears_stale_embedding` 常驻，双容器全绿 |
| D3 | P2（可观测性） | 手工步骤半执行（扩展已装、列未建）时探测静默降级 text，无任何告警 | P3-DP-03 实验过程 | `c1d191b`（探测加固 + warning，R16 配套） | 已关闭：`test_memory_store.py::test_probe_warns_when_extension_present_but_column_missing` / `test_probe_stays_quiet_when_pgvector_entirely_absent` 双容器通过 |
| D4 | P1（可用性回归） | 启动期记忆 Store 探测不抗 DB 中断：database_url 不可达时 `build_memory_store` 的 `_has_vector_column` 探测查询抛 ConnectionRefusedError 穿透 lifespan，API 进程启动期崩溃——破坏 main 上"启动完成、ready/runs 回 503 且不泄露连接串"的行为契约 | 全量回归（alpine 独占跑批）`test_runtime_availability.py::test_database_outage_returns_503_*` 失败 | `7f18ac6`（探测段整体容错：异常记 warning 降级 TsVectorStore，lifespan 完成，503 语义归 ready 端点；新增 `test_probe_failure_degrades_instead_of_crashing`） | 已关闭：失败用例 + store 套件 12/12 复测通过；P3 套件双容器复跑全绿；终局全量回归双容器 0 failed |

| # | 级别 | 残留风险/观察 | 处置 |
|---|---|---|---|
| R1 | P3（已知限制） | R9 裁定限制：text 模式下中文内容纯 ILIKE 命中时 ts_rank=0，衰减因子无法对零分平票重排（中文场景衰减排序弱化）。P3-IT-08 的公式断言用英文内容构造以固化衰减公式；中文可检索性本身由双模式用例覆盖 | 记录，设计层面已知限制（08 R9） |
| R2 | P3（预存 flake） | main 侧 `test_model_gateway.py::test_timeout_and_cancellation_release_slots` 在本机高概率挂起（第二次 invoke 等待未释放的并发槽 → `entered.wait()` 无超时死等）：全量跑批 3 次 2 挂 1 失败，单独跑 branch 2/5 通过、main 3/4 通过——双侧同代码路径（本分支未触碰该用例及其 invoke/信号量路径，diff 复核确认），严重度本机环境相关，Phase 2 R2 同款。该用例为捕获"超时不放槽"缺陷而设计，挂起形态=被测缺陷的僵死表现，建议后续给 `entered.wait()` 加超时使其失败而非死等 | 记录；全量回归以 deselect 该用例 + 单独跑批计次的方式执行（§8），非本阶段缺陷 |
| R3 | P3（已知限制） | 存量无 embedding 条目不参与 vector 召回（`embedding IS NOT NULL` 硬条件；关键词兜底仅在查询 embedding 失败时整查询降级），直到被覆写补向量——README 已按实测措辞文档化（`6305ffb`） | 记录，已文档化 |
| R4 | P3（后续项） | CI 尚无 pgvector 矩阵 job（R13 裁定不阻塞验收）；本地双容器矩阵已全绿 | 后续 PR 增加 `matrix.image`，PR 描述说明 |

## 10. 出口准则核对（04 §8）

| 准则 | 状态 |
|---|---|
| 双模式矩阵用例全绿（CI 含 pgvector job） | 本地双容器矩阵全绿（vector 58 passed / text 47 passed，skip 均为模式门控）；CI pgvector job 按 R13 为后续项（§8/R4） |
| AC-1~AC-7（含 AC-4b）复现通过 | 满足：AC-1→P3-IT-06/01，AC-2→P3-IT-04/05，AC-3→P3-IT-05，AC-4→P3-IT-06/07（text 模式），AC-4b→P3-IT-08，AC-5→P3-IT-09，AC-6→P3-IT-01/05，AC-7→P3-UT-10，全部实测通过 |
| 部署/迁移测试 P3-DP-01~05 通过 | 满足（§5；DP-03/04 按 R16/R17 修订口径实测通过） |
| 无 P0/P1 未关闭 | 满足：未发现 P0（无跨租户/跨客户泄漏、工单主流程无破坏）；P1 的 D1/D2/D4 均已修复并回归关闭 |

## 附录 A 后端执行输出快照

A-1 `uv run ruff check .`（`7f18ac6`）

```text
All checks passed!
```

A-2 P3 套件 · vector 模式（pgvector 容器 55433，`7f18ac6`；D1 修复后首跑 57 passed，D4 修复新增 1 例探测容错用例）

```text
58 passed, 2 skipped in 37.68s
```

A-3 P3 套件 · text 模式（alpine 容器 55432，`7f18ac6`）

```text
47 passed, 13 skipped in 32.73s
```

A-4 迁移 0036 用例（`test_memory_event_migrations.py`，pgvector 容器 / alpine 容器各一遍）

```text
2 passed（pgvector 容器：断言 embedding vector(1536) + HNSW 分支）
2 passed（alpine 容器：断言无 vector 列分支）
```

A-5 全量后端回归 · alpine 容器（`7f18ac6`，独占机器；deselect = R2 flake 用例）

```text
410 passed, 14 skipped, 1 deselected, 2 warnings in 446.68s (0:07:26)
```

A-6 全量后端回归 · pgvector 容器（`7f18ac6`，独占机器；deselect = R2 flake 用例）

```text
421 passed, 3 skipped, 1 deselected, 1 warning in 392.97s (0:06:32)
```

A-7 首次全量跑批（并发负载，过程证据）：`4 failed, 406 passed, 14 skipped in 752.43s`。4 例失败事后全部定位：2 例为 main 侧迁移测试头断言未同步（0035→0036，QA 维护闭环 `e9ca8e5`）、1 例为 D4（可用性回归，`7f18ac6` 修复）、1 例为 R2 预存 flake。

## 附录 B 前端执行输出快照

B-1 `npm run typecheck && npm run check:i18n && npm run build`：tsc 无错误；`Verified 1288 translations in zh-CN, zh-TW, en, hi`；build 成功（仅 chunk 体积警告）。

B-2 Playwright 相关子集（vite dev server 5173 + chromium headless，API mock 按各 spec 约定）：`4 failed, 4 passed (6.4m)`——memory.spec.ts 4/4 通过（含本阶段控制台事件组展示的相关页面）；tickets-antd(2) 与 agents-workspace(2) 失败特征均为 90s 超时等待登录态/真实后端元素（与 Phase 2 报告 §8/B-3 记录的预存类别同用例同特征），本分支零新增 e2e 失败。过程备注：执行前本机 Playwright 浏览器缓存被外部清空（Phase 2 同款），`npx playwright install chromium` 重装后复跑，缓存缺失期的失败不计入结果。

## 附录 C 部署实验证据

C-1 P3-DP-03 卷替换（postgres:17-alpine → pgvector/pgvector:pg17，同一命名卷）：

1. 替换后容器正常启动，存量 memory_items/工单数据完整；后端探测保持 text（迁移 DO 块只在迁移执行当时运行一次，不会随镜像替换重跑）——与 R16 预期一致。
2. 按 README「手工启用 vector（存量库）」执行幂等 DDL：`CREATE EXTENSION IF NOT EXISTS vector` → `ALTER TABLE memory_items ADD COLUMN IF NOT EXISTS embedding vector(1536)` → `CREATE INDEX IF NOT EXISTS memory_items_embedding ON memory_items USING hnsw (embedding vector_cosine_ops)`，并配置维度 1536 的 embedding profile。
3. 重启后端后探测切换 vector；新写入事件条目生成 embedding，vector 检索可用。步骤幂等（重复执行无副作用）。

C-2 P3-DP-04 卷回退（pgvector/pgvector:pg17 → postgres:17-alpine，同一命名卷）：

1. **未清理直接回退（破坏性确认）**：切到 alpine 后 memory_items 全表不可读写，报错 `could not access file "$libdir/vector": No such file or directory`；在该库内 `DROP COLUMN embedding` / 删索引同样因类型解析加载 vector 库而失败，alpine 镜像无 SQL 级自救手段（只能换 pgvector 镜像挂回清理）。
2. **按 R17 正确路径**：先在 pgvector 镜像上执行清理 DDL（`ALTER TABLE memory_items DROP COLUMN embedding; DROP EXTENSION vector;`，HNSW 索引随列级联删除），再切换 alpine——服务可用、探测 text、全量数据（含事件条目）保留，text 检索/注入正常。

C-3 compose 静态核验：`deploy/quickstart/compose.yaml` 与根 `compose.yaml` 默认 `POSTGRES_IMAGE:-pgvector/pgvector:pg17`，覆盖为 `postgres:17-alpine` 后两容器形态不变、无新增服务；`deploy/README.md` 记忆检索章节与 C-1/C-2 实测步骤一致。
