# Phase 3 · 事件记忆 + 语义检索 — 验收报告

> 状态：已完成（结论：**有条件通过**，见 §8）
> 创建：2026-10-15
> 依据：`05-acceptance-plan.md`（A1~A7、UAT、业务指标、合规 C1~C4）、`02-requirements.md`（AC-1~AC-7、AC-4b）、`06-test-report.md`（QA 测试执行结果）、`08-implementation-breakdown.md`（R1~R17 裁定，C3 按 R17 第 4 条口径判定）
> 执行：验收代理（自动化验收轮）；执行日期 2026-10-15；验收基线 HEAD `f4250d0`

## 1. 验收组织与范围说明

本轮为**自动化验收轮**：A1~A7 按验收方案在本机 Docker 双 PG（真实 PostgreSQL 17，alpine=text 模式 / pgvector=vector 模式）+ demo/mock 可编程模型环境逐条复现，执行手段为"QA 测试基建在验收 HEAD 上独立复跑（非引用 QA 结论）+ 代码级判据核对 + 真实 Docker 卷回退演练复验"。与测试报告的分工：`06-test-report.md` 回答"用例过了吗"，本报告回答"业务场景成立吗、指标是否达成、风险是否可控"。

以下各项超出本环境交付能力，按 Phase 1/2 验收报告先例如实标记"待生产验证 / 待人工走查"并给出测量方案（见 §5、§7）：

- **A1/A4 的"Agent 引用结论作答"质量**（真实模型推理）：本机 demo/mock 模型不具备真实检索理解与作答能力；检索与注入链路本身已双模式实测；
- **检索相关性与结局引用率两项质量指标**：方案定义为真实检索调用人工抽检与评估集人工判分；
- **UAT 坐席代表人工判定**：本轮以代码级核对 + QA 的 Playwright 证据代替，建议上线前真人走查一次（非阻断）。

**前置条件核对**：`04-test-plan.md` 双模式矩阵全部通过（`06-test-report.md` §10 出口准则逐条满足），无 P0/P1 未关闭（D1/D2/D4 均已修复并回归关闭）。验收数据使用测试专用租户（每用例独立 `test-<uuid>`），不与其他数据混用。

## 2. 验收环境

| 环境 | 配置 | 承担场景 |
|---|---|---|
| 本机 Docker PG A（text 模式） | 容器 `nexusdesk-test-pg`（postgres:17-alpine，127.0.0.1:55432），库 `nexusdesk_test` 迁移至 head（0036，本轮实测无 vector 扩展） | A1~A7 text 模式复现；合规 C1/C2/C4 |
| 本机 Docker PG B（vector 模式） | 容器 `nexusdesk-test-pgvector`（pgvector/pgvector:pg17，127.0.0.1:55433），库 `nexusdesk_test` 迁移至 head（0036，本轮实测 vector 扩展在） | A1~A7 vector 模式复现；检索性能基准；C1/C4 |
| 本机 Docker 一次性演练容器 | 独立命名卷（`acc-p3-*`，用后已清理），真实 alembic 迁移至 0036 | 合规 C3 镜像回退演练复验 |
| 模型 | demo/mock（`RecordingGateway`、可编程 StubEmbedGateway） | 全部自动化场景 |
| 类生产（真实模型）+ 上线后数据 | — | 本轮不可用：2 项质量指标与生产复核抽样留待（§5） |

**本轮独立复跑证据**（验收 HEAD `f4250d0`，与 QA 报告计数逐项吻合）：

| 项 | 结果 |
|---|---|
| P3 套件 · text 模式（alpine 容器） | **49 passed, 13 skipped, 0 failed**（六文件合并复跑，含迁移 0036 用例 2 例。对账 06：A-3 的 47 passed/13 skipped 为五个非迁移文件批次、A-4 的 2 passed 为迁移文件批次，49=47+2 为两批合计而非重复计数；skip 均为 vector 专属按设计门控） |
| P3 套件 · vector 模式（pgvector 容器） | **60 passed, 2 skipped, 0 failed**（六文件合并复跑，含迁移 0036 用例 2 例。对账 06：A-2 的 58 passed/2 skipped 为五个非迁移文件批次、A-4 的 2 passed 为迁移文件批次，60=58+2 为两批合计而非重复计数；skip 为无列探测变体按设计门控） |
| `uv run ruff check .`（backend 目录，与 CI Lint 步骤同口径） | All checks passed |
| 前端 `npm run typecheck` / `check:i18n` | 通过（1288 keys × 4 语言） |
| C3 镜像回退演练（真实迁移 + 真实卷替换，§6-C3） | 通过 |

P3 套件 = `test_memory_events.py` + `test_memory_events_postgres.py` + `test_memory_event_migrations.py` + `test_memory_event_render.py` + `test_memory_search_tool.py` + `test_memory_store.py`，双容器各执行一遍（`TEST_DATABASE_URL` / `MIGRATION_TEST_DATABASE_URL` 分别指向 55432 / 55433）。

## 3. 功能验收记录（A1~A7）

执行人：验收代理（自动化）；日期：2026-10-15；环境：上表双 PG + demo/mock 模型。按方案要求，A1/A4b/A6 在两种模式下各执行一遍（双容器复跑天然覆盖，其余条目亦双模式执行）。

| # | 判定 | 执行记录与实际结果 | 证据 |
|---|---|---|---|
| A1 | **通过（自动化层，双模式）**；真实模型作答质量待生产验证 | 前置：渠道会话绑定客户 → 工单 decide approve（创建事件）→ resolved（解决事件 + open_ticket 置 expired）。实际（双模式各一遍）：①3 条事件 active（created/resolved + open_ticket 软过期保留可审计），`source_type='ticket'`、`confidence=1.0`、`source_ref` 正确；②新一轮会话中 demo 模型对"上次的工单处理进展"发出 `memory_search` tool_call → 工具执行 → 返回含解决结论内容。"隔日"按既定约定改库构造时间字段，无真实等待。"Agent 引用解决结论作答"需真实模型推理——检索命中与内容进入模型输入已证，作答质量列入 §5 指标 1 | P3-IT-01、P3-IT-06（本轮双模式复跑通过） |
| A2 | **通过（双模式）** | 坐席侧 `PATCH /tickets/{tid}` 备注带 `is_commitment=true`：①`ticket_process_log` remark 行落 `is_commitment=true` 并投递 commitment 事件；②记忆条目 key=`commitment:{log_id}`、`expires_at=NULL`（不过期类）、`source_ref=ticket_id:log_id`；③注入含该承诺（【待跟进事项】段），工单当前状态经 open_ticket 条目可见；未标记备注不产生承诺条目。前端"标记为承诺"勾选入口代码级核对在（TicketsPage 处理表单 + i18n key 四语言齐全） | P3-IT-04、P3-IT-05（本轮复跑）；`TicketsPage.tsx:57` |
| A3 | **通过（双模式）** | 客户在途工单进线：注入体在画像/偏好段之后出现【待跟进事项】段（代码核对 `agent_runtime/worker.py::_customer_memory` 段顺序：摘要 → 画像/偏好 → 待跟进事项，Phase 1/2 红线未动）；>5 条截断为 5 且按 updated_at 倒序。"Agent 主动提及"为模型行为，注入内容完整进入模型输入已证 | P3-IT-05（两例，本轮复跑）；代码核对 |
| A4 | **通过（text 模式功能层）**；降级作答质量待生产验证（人工判定） | 在 alpine（无 pgvector）环境重复 A1：text 模式全套 49 passed（含迁移 2 例）——关键词检索（tsvector + R9 ILIKE 兜底）命中目标事件，工具端到端返回解决结论内容；探测降级 text 行为正确。"回答质量可接受"按方案为人工判定，留待类生产环境（§7 L6） | 本轮 text 模式全量复跑；P3-IT-06/07（text 腿） |
| A4b | **通过（双模式）** | 改库构造 3 天前 / 150 天前两条相关度相近事件：3 天前排前（双模式）；半衰期 30→365 调整后分差比值符合 `2^((150-3)/half_life)` 公式（公式断言用英文内容固化，中文平票弱化为已知限制，见 §7 L3） | P3-IT-08（本轮双模式复跑） |
| A5 | **通过（双模式）** | 改库构造 `expires_at` 过期事件条目 → 执行 maintenance 清理：条目置 `expired`（软过期非删除）；检索与注入均不可见；commitment 类不过期；事件域之外条目（profile/preference）不受影响 | P3-IT-09（本轮双模式复跑） |
| A6 | **通过（双模式）** | 在途工单 resolved 后：①`open_ticket:{no}` 置 expired（行保留），下一轮注入的待跟进事项中该工单消失；②解决结论条目 active 可被 memory_search 检索到 | P3-IT-01、P3-IT-05（本轮双模式复跑） |
| A7 | **通过** | 注册四闸门逐一实测：草稿 `tool_names` 移除 memory_search → 运行时工具列表无该工具；无 customer 绑定 / `memory_enabled=false` → 同样不出现；未注册时直接/幻觉调用返回 `memory_search_unavailable` 拒绝执行（不触发检索）。另代码核对：新建 Agent 草稿 `initialConfig.tool_names` 默认含 memory_search（R15 口径：新草稿默认、存量不变），存量 Agent 草稿不受影响 | P3-UT-10（4 例 + 拒绝执行例，本轮复跑）；`AgentsPage.tsx:21`、`platform_tools.py` |

## 4. UAT 核对（坐席代表清单，自动化代验）

| 判定项 | 本轮核对结果 |
|---|---|
| 工单备注"标记为承诺"勾选入口可见、含义清晰 | 入口在 TicketsPage 处理表单（仅 note 非空时生效，`is_commitment && Boolean(note.trim())`），四语言 i18n key 齐全（zh-CN/zh-TW/en/hi，1288 keys 校验通过） |
| 承诺兑现/工单关闭后待跟进事项及时消失 | P3-IT-05 实测：resolved 后下一轮注入即不含该工单；commitment 在工单关闭联动下置 expired（R5 约定） |
| 事件记忆内容模板读得懂、可追溯 source_ref | 模板实测（P3-UT-06 五例）：`工单 {ticket_no} 已解决：{摘要}` / `承诺待兑现：…`；`source_ref` 为 ticket_id（承诺类 ticket_id:log_id），记忆控制台事件分组已加（CustomerDetail/Drawer/labels，标签"事件"）；QA Playwright memory.spec 4/4 通过 |

结论：操作清单逐条核对通过，无阻断问题；坐席代表真人走查列为 §7 L6 非阻断项（与 Phase 2 L7 同先例）。

## 5. 业务指标逐项判定

| 指标 | 目标 | 本轮判定 | 数据 / 验证方法与就绪度 |
|---|---|---|---|
| 历史工单结局正确引用率（评估集） | ≥ 80% | **待生产验证** | 需真实模型作答 + ≥15 个带历史工单结局的回头客 case 人工判分，本机 mock 不具备。就绪度：A1 已双模式证明解决结论经 memory_search/注入完整进入模型输入；评估集可经渠道入口构造，复用本阶段测试基建。责任方：研发负责人出具跑批数据，产品负责人判分 |
| memory_search top-3 相关性 | ≥ 85%（pgvector）/ ≥ 70%（TSVECTOR） | **待生产验证**（双模式分别统计） | 需 ≥50 次真实检索调用人工抽检。就绪度：双模式检索等价性（P3-IT-07）、过滤正确性（P3-IT-11）、排序语义（P3-IT-08）均已实测；检索调用可经 `gateway_calls`（actor='memory'）与运行时日志归因抽样。责任方同上 |
| 事件写入延迟 | ≤ 1 分钟 | **通过（机制层实测）**；上线后抽样复核 | P3-IT-02：工单接口同事务仅落 `event_write` 任务、同步返回零记忆写入；Worker 执行后可查，写入延迟在轮询周期内。代码核对：MemoryWorker `poll_seconds=0.5`，认领+执行远小于 1 分钟目标。生产复核方法（方案 §5）：工单状态变更时间戳 vs 记忆条目 `created_at` 抽 ≥20 例 |
| 检索性能 | P99 ≤ 300ms | **通过（本地基准，vector 模式）**；生产复核 | P3-IT-12：单客户 1000 条事件（`generate_series` 构造 + 统一向量），20 次采样取 P99 断言 ≤300ms，pgvector 容器实测通过（本轮复跑）。生产复核方法：类生产压测或运行时日志统计（单客户条目量级下） |
| TTL 任务有效性 | 过期条目不进入检索与注入 | **通过**；上线后审计抽查 | A5（P3-IT-09）双模式实测：过期→expired→检索/注入双不可见；worker 空闲 ≥24h 周期扫描机制代码核对在（`worker.py` scan_interval，启动后首轮立即执行）。上线后按方案审计抽查 |

## 6. 合规核对（C1~C4）

| # | 核对项 | 判定 | 证据（本轮独立核验） |
|---|---|---|---|
| C1 | 检索与注入严格限定当前 tenant + customer，无跨客户泄露（一票否决） | **通过** | ①用例层：P3-UT-11/P3-IT-11 双模式复跑通过——含跨租户/跨客户/过期/profile-preference 诱饵行，双实现（PgVector/TsVector）过滤一致（tenant + customer + active + type=event）；②代码层：工具执行取数只用 run 行上下文 `tenant_id`/`customer_id`（模型仅控制 query 文本，无法指定他人身份，`platform_tools.py` memory_search 分支）；store 两实现 SQL 与 `pending_event_items` 注入查询均为硬过滤；③探测/嵌入写回亦带 tenant 谓词（store.py:179/188） |
| C2 | 快速体验部署承诺：两容器、无新中间件，降级形态功能完整（一票否决） | **通过** | ①静态核验：`deploy/quickstart/compose.yaml` 仍仅 postgres + app 两服务、无新增服务/中间件，默认镜像 `pgvector/pgvector:pg17`、`POSTGRES_IMAGE` 覆盖口在；生产 compose 零变更（R2：memory-worker 复用后端镜像为 Phase 1 既定拓扑）；②功能层：降级形态 = text 模式全套 49 passed（含迁移 2 例；检索/注入/TTL/事件写入全绿，A4）；根 compose 同步换镜像（非阻断项，R3） |
| C3 | pgvector 镜像变更可独立回滚，回退后不影响服务（按 R17 第 4 条口径：**按 README 文档化流程回退后服务可用、数据保留即通过**） | **通过** | 本轮独立演练（一次性命名卷，真实 `alembic upgrade head` 至 0036，含 vector 扩展 + `embedding vector(1536)` + HNSW 索引 + 带 embedding 的事件数据）：①**破坏性确认**——未清理直接换 alpine：`SELECT` 报 `could not access file "$libdir/vector"`，`DROP COLUMN` 同错、无 SQL 级自救（与 README 警示一致）；②**文档化流程**——pgvector 镜像上执行 `ALTER TABLE memory_items DROP COLUMN embedding; DROP EXTENSION vector;`（HNSW 随列级联删除）后切回 alpine：数据完整保留、新事件行可写、ILIKE 兜底检索命中、探测降级 text（无扩展无列，静默 text 为正确分支）；③双向切换均实测出现 collation version warning，README 的 `REINDEX DATABASE` 注意事项与之一致。升级方向（R16 手工幂等 DDL）由 QA P3-DP-03 真实卷实验覆盖（`06-test-report.md` 附录 C-1），本轮复核 README 步骤与实验逐项吻合。演练容器与卷用后已清理 |
| C4 | embedding 失败不丢记忆（条目正常写入可检索、可重试） | **通过** | P3-IT-10（vector 模式本轮复跑）：mock embed 持续报错 → 事件条目正常写入、`embedding_failed=true`、event_write 任务本身不失败；重试成功清标记、连续失败保留标记等下轮（每轮限量 50）；事件写入器在 embedding 全停时存活。另 P3-DP-05（R14 形态）：维度不匹配 profile 配置后探测降级 text + 明确 warning 日志 |

## 7. 遗留问题清单

| # | 级别 | 问题 | owner 建议 | 期限建议 |
|---|---|---|---|---|
| L1 | 待生产验证 | 指标 1（结局引用率 ≥80%）与指标 2（双模式 top-3 相关性 ≥85%/≥70%）需真实模型与真实检索调用人工判分；指标 3/4 的生产抽样复核（写入延迟 ≥20 例、P99 日志统计）随上线执行；测量方案与数据源已就绪（§5） | 研发负责人出具测量数据，产品负责人判定 | 上线后 2 周内 |
| L2 | P3（后续项） | CI 尚无 pgvector 矩阵 job（06 R4 / R13 裁定不阻塞验收）；本地双容器矩阵已全绿，现状 ci.yml 单 `postgres:17` service 加 `matrix.image` 即可 | QA/研发：后续 PR 增加并在 PR 描述说明 | 下一迭代 |
| L3 | P3（已知限制） | text 模式下中文内容纯 ILIKE 命中时 ts_rank=0，衰减因子无法对零分平票重排（中文场景衰减排序弱化；06 R1 / 08 R9 设计层面已知限制）；中文可检索性本身已由双模式用例覆盖 | 产品负责人 + 研发负责人：按上线后 text 模式实际使用面评估是否引入分词改进 | 后续迭代评估 |
| L4 | P3（已知限制，已文档化） | 存量无 embedding 条目不参与 vector 召回（`embedding IS NOT NULL` 硬条件），直到被覆写/重试补向量；README 已按实测措辞文档化（06 R3）；`embedding_failed` 限量重试机制在 | 研发负责人：上线后如存量回填需求显现，评估批量补向量手段 | 后续迭代评估 |
| L5 | P3（预存 flake，非本阶段） | main 侧 `test_model_gateway.py::test_timeout_and_cancellation_release_slots` 本机高概率挂起（双侧同代码路径，Phase 2 R2 同款）；全量回归以 deselect + 单独跑批计次执行（06 §8）；建议给 `entered.wait()` 加超时使其失败而非死等 | QA/研发：改造该用例超时形态 | 下一迭代 |
| L6 | pending_human | UAT 坐席代表真人走查（承诺勾选入口、待跟进消失及时性、事件模板可读性）：本轮以代码核对 + Playwright 证据代验通过（§4），建议上线前走查一次（非阻断） | 产品负责人 + 坐席代表 | 上线前 |

另：前端 Playwright 的 tickets-antd / agents-workspace 预存失败为 Phase 2 已记录类别、本分支零新增（`06-test-report.md` 附录 B-2），沿用 Phase 2 验收 L4 跟踪，不重复立项。

## 8. 结论与签字

**结论建议：有条件通过。**

- A1~A7 在自动化层全部通过（A1/A4b/A6 双模式各一遍；A1/A4 的真实模型作答质量部分按判据拆分为"检索/注入侧已通过 + 作答质量随指标 1/2 待生产验证"，与 Phase 1/2 处理先例一致）；
- 合规核对 C1~C4 全部通过，一票否决项零触发（C3 按 R17 第 4 条口径，本轮以真实迁移 + 真实卷替换独立复验 README 文档化回退流程：服务可用、数据保留）；
- 本机可测量硬指标实测通过：事件写入延迟（机制层，轮询周期内可查，远优于 1 分钟目标）、检索 P99 ≤ 300ms（pgvector 本地基准）、TTL 任务有效性（双模式）；结局引用率与检索相关性两项质量指标按方案定义需真实模型与真实调用抽检，判定"待生产验证"且测量方案与数据源就绪（L1）——最终"通过"结论须在硬指标测量回填后由产品负责人确认升级；
- 无 P0/P1 遗留（D1/D2/D4 均已修复并回归关闭）；遗留 4×P3（L2/L3/L4/L5）+ 待生产验证（L1）+ 人工走查（L6），均有 owner 与期限建议，符合验收方案 §7"有条件通过"标准；
- UAT 操作清单（§4）逐条核对通过，无阻断问题；人工走查列为 L6 非阻断项。

| 角色 | 签字 | 日期 |
|---|---|---|
| 产品负责人（含硬指标测量回填后的结论升级确认、L6 走查组织） | 待签 | |
| QA | 验收代理（本轮自动化执行 + C3 演练复验）：通过 | 2026-10-15 |
| 研发负责人（含 L1 测量数据出具） | 待签 | |
| 运维负责人（C3 镜像变更/回退演练复核） | 待签 | |
