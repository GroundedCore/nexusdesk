# Phase 6 · 客户记忆可视化与注入可解释性 — 验收报告

> 验收日期：2026-10-19（第一轮）
> 依据：`05-acceptance-plan.md`（A1~A12、§4 排障 UAT、§5 业务指标、C1~C5、§7 通过标准）
> 验收对象：工作区未提交的 Phase 6 实现（git 分支 feat/memory-visualization）
> 执行方式：独立子代理执行自动化验收项；manual-required 项如实标注，未写作通过。
> 测试数据详见：测试执行记录（`backend/tests/test_memory_injection.py`、`backend/tests/test_memory_injection_postgres.py`、`frontend/tests/memory-visualization.spec.ts` 及既有回归套件；`06-test-report.md` 由测试报告阶段另行汇总）
> **当前结论（第一轮）：有条件通过**——A1~A12 自动化部分与 C1~C5（含三条一票否决项）全部满足，无 P0/P1 遗留；排障 UAT 计时、类生产性能实测、quickstart TSVECTOR 形态验证、界面真实环境走查等 manual-required 项及 P2 缺陷 D1 处置签字完成前不得发布（见第 6、7 节）。

---

## 1. 验收组织与执行说明

| 角色 | 本次执行情况 |
|---|---|
| 产品负责人 | 未参与（P2 遗留处置签字待办） |
| QA | 自动化 AC 场景执行与记录已由子代理完成；覆盖率对账与类生产实测待办 |
| 研发负责人 | 环境保障（5433 PG 容器、前端 dev server）已由子代理完成；D1 性能整改评估待办 |
| 客服主管/运营 | 未参与（§4 排障情景 UAT 待办） |

验收环境：后端 PG 集成使用容器 `nexusdesk-p5-pg`（pgvector/pg17，迁移 head 已确认为 `0041_memory_injection_log`，`\d memory_injection_log` 核对表结构、3 索引、section 四值 CHECK、无外键（D1 决策）齐全；upgrade/downgrade 双向可迁移），`TEST_DATABASE_URL=postgresql+asyncpg://agent:agent_dev@127.0.0.1:5433/agent_platform`；前端 e2e 使用本地临时 dev server（5173，测完已停）。类生产环境（真实模型 + 累积数据）与 quickstart 双容器形态未执行。

除测试套件外，验收使用了独立对抗探针 `/tmp/p6_accept_verify.py`（非仓库文件，自建租户驱动真实 run 后直查 DB 与接口，探针数据已自清），对落库正确性、事件兼容、泄露面、权限、级联清理做了独立于产品测试的复核。

## 2. AC 执行记录表（A1~A12，对应 02-requirements.md AC-1~AC-12）

| # | 场景 | 状态 | 证据 |
|---|---|---|---|
| A1 | 绑定客户完成一轮对话（含 flag）：明细按 run×item 落库；事件计数不变且 item_ids 一致 | **pass** | 双层证据：(1) P6-IT-01/02 在 PG 容器真实执行通过（非 skip），score 经 365 天回拨验证 = SQL 算出的 effective_confidence；(2) 独立探针驱动真实对话后直查 DB：`memory_injection_log` 4 行 = run×item（flag/profile/preference/event 各 1），score 规则正确（profile/preference=0.9、flag/event=NULL）；`memory.injected` payload=`{"flags":1,"items":2,"events":1,"item_ids":[4 ids]}`，计数语义不变且 item_ids 集合与明细 item_id 完全一致 |
| A2 | 客户列表：统计列与库直查一致；待确认 >0 高亮可点击直达；按标记筛选生效 | **pass**（界面真实环境走查待办） | P6-UT-03（PG）断言 `customer_memory_stats` 单条批量 SQL 与逐库直查一致；P6-API-02 断言 with_stats 列正确 + 语句计数恒为 2（page_size=100 与 1 相同，无 N+1）+ sort=pending + flag 筛选；P6-FE-01 Playwright 验证统计列渲染/待确认高亮点击直达 `?tab=pending`/标记筛选/排序。界面部分为 mock API 验证，真实环境走查见 M2 |
| A3 | 遍历客户详情 8 个 Tab：数据正确；画像/偏好/事件编辑/删除/恢复回归 | **pass**（界面真实环境走查待办） | `CustomerDetail.tsx` 实现 8 Tab（overview/profile/preference/event/flags/pending/injections/audit，tabKeys 第 22 行），visited 集合保证懒加载懒请求；P6-FE-02 验证各 Tab 懒加载及画像/偏好/事件分 Tab 编辑/删除/恢复回归；既有 `memory.spec.ts` 9 用例适配 8 Tab 结构后全绿。真实环境走查见 M2 |
| A4 | 注入记录 Tab：倒序正确；展开见明细（key/类型/小节/score）；run 可跳会话 | **pass** | `list_injections` 按 `max(injected_at) DESC` 排序（service.py list_injections，GROUP BY run_id）；`InjectionLogPanel` 展开行渲染 key/类型/小节/score 并有会话链接；P6-IT-05 三入口一致性通过；P6-FE-03 通过 |
| A5 | 会话工作台查看本次对话注入：列出各 run 条目；点击跳详情 | **pass**（界面真实环境走查待办） | `GET /memory/runs/{run_id}/injections` 返回当次全部条目（独立探针验证 4 条全量返回）；`CustomerMemoryDrawer`「本次对话注入」按 run 分节（最近 10 个 run，最新默认展开，展开时才逐 run 拉取）、点击条目开 `MemoryItemDetailDrawer`；P6-FE-05 通过。真实环境走查见 M2 |
| A6 | 单条记忆详情：字段齐全；diff 正确；时间线含全事件；来源可跳 | **pass** | `item_timeline` 返回完整字段（SELECT * + 查询时计算的 effective_confidence + prev_content）；P6-IT-09 构造全生命周期（确认→注入→衰退 expired→恢复→编辑）时间线齐全、id DESC 倒序、actor/resource 正确；P6-UT-04 prev_content diff payload；抽屉 LCS 行级 diff 双栏 + SourceRef 来源跳转；P6-FE-04 通过 |
| A7 | 从条目反查注入：最近注入 run 列表正确，链接可跳 | **pass** | `GET /memory/items/{iid}/injections` 按 `injected_at DESC` 返回 run 列表含 conversation_id（service.py item_injections）；独立探针与 P6-IT-05 验证；未注入过的已存在条目返回空列表、跨租户/未知 id 404；抽屉渲染查看会话链接 |
| A8 | 上线前的历史会话：空态文案正确，无报错 | **pass** | 独立探针构造无明细的历史 run：接口 200 且 `items=[]`（非报错）；P6-API-05 通过；前端空态文案「该会话无注入明细记录（功能上线前数据）」（InjectionLogPanel.tsx:8）；P6-FE-06 覆盖历史 run/未绑定客户/无注入空态 |
| A9 | 高频对话压测：P95 额外 ≤50ms；SQL 计数确认单条批量 INSERT | **pass**（附 P3 观察 D2） | 单条批量 INSERT 铁证：SQL 事件探针计数 == run 数（10 次 run 仅 10 条 INSERT 语句，unnest 批量，repository.py:332-355），无逐条写；P95≤50ms 在 10 次执行中 9 次达标，首轮全套件并发负载下测得 62.7ms 超标 1 次（样本仅 10 个、P95 取最大值，判定为共享 dev PG 容器测量抖动而非产品缺陷，记为 P3 缺陷 D2）；无注入场景零写入由 P6-UT-05 验证 |
| A10 | 91 天前明细随 TTL 清理，界面不再展示 | **pass** | P6-IT-06 通过：构造 91/100 天前明细手动触发单轮 maintenance，超期行删除、89 天与真实 run 行保留；实码核对 memory/worker.py maintenance：retention 取 `settings.memory_injection_log_retention_days`（默认 90，settings.py:161），单条 DELETE 与事件 TTL 同事务同车 |
| A11 | viewer 全部视图只读；跨租户 URL 直访 404 | **pass** | P6-API-04 对全部 5 个带 id 新端点跨租户 404（含反向断言本租户视图不夹带他租户行）；独立探针验证未认证 401、viewer 读 200、viewer DELETE 403；P6-FE-07 viewer 全视图只读无操作按钮 |
| A12 | 客户删除：明细级联清除；审计含删除计数；界面无残留 | **pass** | 独立探针直查 DB：删除客户后 `memory_injection_log` 剩余 0 行，审计 `details.injection_logs_deleted=4`；实码核对 governance.py delete_customer：级联 DELETE 在 `engine.begin()` 同一事务内；P6-IT-07 通过（API 404 + 审计计数）；P6-IT-04 证明明细 INSERT 失败时整个 finish 回滚无半截状态。合并改挂由 P6-IT-08 覆盖（明细改挂目标、审计 injection_logs_moved、注入历史连续可查） |

执行记录要素说明：本次为自动化子代理执行，执行环境见第 1 节；前置数据为各 PG fixture 与独立探针自建租户；截图/日志未单独归档（Playwright 与 pytest 输出即记录）。

## 3. 合规核对（C1~C5）

| # | 核对项 | 状态 | 证据 |
|---|---|---|---|
| C1（一票否决） | 审计时间线与注入明细不含记忆明文与 PII（含接口响应正则扫描） | **pass** | 三层证据：(1) P6-IT-10 对 5 个端点 payload 做明文标记（张三/张四/3 个手机号/重点客户）+ 手机号正则扫描，均无命中（item_timeline 的 item 块按 FR-5 合法含明文，只扫描其 timeline 子数组）；(2) 独立探针对 `/memory/runs/{id}/injections` 与 `/memory/customers/{id}/audit` 响应做内容标记 + PII 正则（手机/身份证/邮箱）扫描，均无命中；(3) 实码核对 injection_detail 只 SELECT key/type/status（无 content），审计渲染自 audit_records（Phase 4 C4 本无明文），明细表只存 id/section/score |
| C2（一票否决） | 客户删除后注入明细无残留（级联删除入事务） | **pass** | governance.delete_customer 在同一 `engine.begin()` 事务内 DELETE memory_injection_log 并写审计 injection_logs_deleted；独立探针直查 DB 确认零残留 + 审计计数准确；P6-IT-04 证明事件与明细同事务同生共死 |
| C3（必须通过） | 条目物理删除后不试图还原内容（仅「已删除」标识） | **pass** | item 物理删除后 injection_detail 以 LEFT JOIN + `(m.id IS NULL) AS item_deleted` 降级，不返回 key/type、绝不还原内容（service.py injection_detail）；item_timeline 对已删条目返回 item=None 但保留时间线（不 404）；前端渲染「已删除」Tag 与「条目已删除，内容不可还原」；P6-IT-05 通过 |
| C4（一票否决） | 租户隔离：全部新端点跨租户 404 | **pass** | 全部新端点经 Reader 依赖注入取 user.tenant，服务层每条 SQL 带 tenant_id 谓词；P6-API-04 对 customers/{cid}/injections、customers/{cid}/audit、runs/{run_id}/injections、items/{iid}/injections、items/{iid}/timeline 五端点跨租户 404 全过（with_stats 列表端点无 id 入参，天然租户内）；独立探针复核未认证 401 |
| C5（必须通过） | `memory_injection_log_enabled=false` 可整体降级为仅计数事件 | **pass** | settings.py:157 默认 True；关闭时 worker 在 `_memory_context` 中丢弃 injection_log、事件计数照常（P6-UT-02 worker 门控用例 + P6-IT-11：关后零明细写入、计数字段不变、查询接口返回空列表 200；重新打开后恢复落库）。说明：关闭时事件仍携带 item_ids（仅 id 列表，不违 C1 红线，与 FR-1 事件兼容一致，AC 未 pin 此字段） |

## 4. 性能测量数据

### 4.1 热路径（AC-9，一票否决相关）——有自动化证据，达标

| 项 | 判据 | 实测 | 判定 |
|---|---|---|---|
| 写入方式 | 单条批量 INSERT，无逐条写 | SQL 事件探针：10 次 run（每次 28 条注入）恰好 10 条 INSERT 语句（unnest 批量，repository.py:332-355） | 达标 |
| 额外耗时 | P95 ≤ 50ms | 10 次执行 9 次达标；唯一超标 62.7ms 发生在全套件并发负载下的首轮（样本 10 个、P95 取最大值），随后单测 3 次 + 全套件 1 次 + 单测 5 次共 9 次全部通过 | 达标（超标次判定为测量环境抖动，见 D2） |
| 无注入场景 | 零写入 | P6-UT-05 四条路径（空渲染/无 memory 服务/无 customer/读关闭）均不产生 log 上下文与写入 | 达标 |

### 4.2 查询性能——无计时证据，存在超标风险（P2 缺陷 D1）

| 项 | 判据 | 现状 |
|---|---|---|
| 注入记录查询 | P95 ≤ 2s（三索引已建：customer/item/run） | 无任何自动化计时证据；需类生产累积数据实测 |
| 客户详情首屏 | 懒加载，P95 ≤ 1.5s（万级条目客户） | 无计时证据；且 `GET /memory/customers/{cid}`（service.py customer_detail）仍全量 SELECT 该客户全部 memory_items（无 LIMIT/分页）并逐行 Python 计算 effective_confidence，画像/偏好/事件 Tab 数据均来自这一次全量拉取——万级条目时响应体约 10MB 级 + 万次 decay 计算，存在超标风险（Phase 2 遗留实现，本阶段 NFR 未闭环，记为 D1） |
| 列表统计 | 无 N+1 | P6-API-02 语句计数恒为 2（page_size=100 与 1 相同），with_stats 为每页单条 GROUP BY 批量查询 | 达标 |

## 5. 业务指标核对（05 §5）

| 指标 | 目标 | 现状 |
|---|---|---|
| 明细落库覆盖率 | 100%（有 memory.injected 事件的 run 必有明细行） | P6-IT-02 事件计数与明细行数一致 + 独立探针复核通过；05 §5 要求的「自动化对账脚本抽检」在累积数据环境执行待办（M4） |
| 排障回溯能力 | 会话↔条目双向可查 | A5/A7 自动化通过；§4 排障 UAT 计时待人工（M1） |
| 生命周期完整度 | 有审计即有时间线点 | A6 + P6-IT-09 通过 |
| 排障耗时 | ≤ 5 分钟 | manual-required（M1） |
| 性能 | 热路径 P95 额外 ≤50ms；查询 P95 ≤2s | 热路径达标（4.1）；查询性能无证据（D1，M3） |

## 6. 需人工验收项清单（manual-required，含具体步骤）

| # | 待办 | 具体步骤 | owner | 状态 |
|---|---|---|---|---|
| M1 | §4 排障情景 UAT 计时（核心业务价值验证） | 情景：运营收到投诉「Agent 刚才对客户乱承诺」。全程界面操作：(1) 从会话记录定位问题 run → 会话工作台客户抽屉「本次对话注入」查看当次注入明细；(2) 点开可疑条目详情，核对完整字段、prev_content diff、生命周期时间线与来源会话跳转；(3) 编辑纠正（或删除）该条目，从条目详情反查最近被哪些 run 注入过，评估影响面；(4) 记录全流程耗时。判据：无需查库/翻日志；总耗时 ≤ 5 分钟；每步信息充分支撑下一步 | 客服主管/运营（产品负责人见证） | **未执行** |
| M2 | 界面真实环境走查（A2/A3/A5 的界面部分目前仅 mock API 验证） | 起真实后端 + dev server：(1) 登录控制台→记忆→客户列表，核对统计列与 `psql` 直查一致、点击待确认高亮数字跳转 `?tab=pending`、按服务标记筛选与排序生效；(2) 打开任一多类型记忆客户，遍历 8 个 Tab 核对数据，并在画像/偏好/事件 Tab 各做一次编辑/删除/恢复；(3) 会话工作台打开客户抽屉→本次对话注入→点击条目跳详情抽屉 | QA | **未执行** |
| M3 | 类生产性能实测（关联 P2 缺陷 D1） | 在累积数据（含万级条目客户）的类生产环境：(1) 接口计时注入记录三入口查询 P95 ≤ 2s；(2) 客户详情首屏 P95 ≤ 1.5s（万级条目客户）；(3) 若首屏超标，评估整改：概览改用 _memory_stats 聚合 + 条目分 Tab 分页拉取 | 研发负责人（测量）+ QA（复核） | **未执行** |
| M4 | 明细落库覆盖率对账（05 §5） | 在累积数据环境跑对账脚本：有 `memory.injected` 事件的 run 必有对应明细行，覆盖率 100% | QA + 研发负责人 | **未执行** |
| M5 | quickstart 双容器形态验证（05 §2） | 在 quickstart TSVECTOR 降级形态下验证明细落库与三层视图同样可用（复用旧容器注入新代码时注意 docker cp 目录嵌套） | 研发负责人 | **未执行** |
| M6 | P2/P3 遗留处置签字（D1/D2/D3 排期决策） | 产品负责人审阅第 7 节遗留清单并签字确认排期 | 产品负责人 | **未执行**（验收结论复核时） |

## 7. 遗留清单（含 owner 与期限建议）

| # | 级别 | 项 | 状态与说明 | owner / 期限建议 |
|---|---|---|---|---|
| D1 | P2（NFR 未闭环） | 万级条目首屏性能无证据且实现存在超标风险 | `GET /memory/customers/{cid}` 全量拉取该客户全部条目并逐行计算 effective_confidence，万级条目时首屏大概率超 1.5s；注入记录查询 P95≤2s 亦无计时证据。属 Phase 2 遗留实现，Phase 6 将首屏 P95 写入本阶段 NFR 而未闭环。按 04 §8 需产品签字遗留 | 研发负责人（实测与整改评估）/ 上线后 2 周内在类生产完成 M3 实测；若超标，下一迭代改为概览聚合 + 分 Tab 分页；产品负责人签字确认 |
| D2 | P3（测试缺陷） | P6-IT-03 性能断言方法学脆弱 | 以 10 个样本的 max 近似 P95，首轮全套件并发下 62.7ms 误报一次；SQL spy 证明始终为单条 unnest 批量 INSERT，非产品缺陷。建议增大样本量（如 30 次取 idx=28）或语句计时取中位+上限分层断言 | QA（测试改进）/ 下一迭代随测试维护排期 |
| D3 | P3（测试缺陷） | PG 测试 fixture 清场遗漏 memory_injection_log | `tests/test_platform_postgres.py` 的 platform fixture 清场删 runtime_runs（runtime_events 随 FK 级联），但 memory_injection_log 按 D1 无外键不级联且清场列表未包含它——验收期间共享测试库已累积 3520 行/150 个测试租户的孤儿明细（psql 实测）。不影响产品正确性，但污染「直查 DB 对账」类验收口径。建议 fixture 清场加 `DELETE FROM memory_injection_log WHERE tenant_id=:t` | 研发负责人 / 下一迭代随测试维护排期 |
| D4 | P3（范围外既有测试债） | 28 条前端 Playwright 用例在 HEAD 基线即失败 | 全部 28 条失败经临时 worktree 对照确认在 pristine HEAD 同样复现，与 Phase 6 无关：根因一为部分 spec `goto('/')` 无 hash 撞 AuthBoundary 登录门（mock 未返回 development_access，AuthBoundary 自初始提交未变）；根因二为 agent workspace 类 spec 的 mock 未覆盖 Phase 5 已提交端点（/agents/memory-config-caps、evaluation-reports）。Phase 6 范围内用例（memory-visualization.spec.ts 7 条 + memory.spec.ts 11 条 + agent-memory-config.spec.ts 8 条）26/26 全绿 | 前端测试 owning 团队 / 由 M6 排期决策 |

## 8. 结论

### 第一轮结论（2026-10-19，当前生效）

**有条件通过。**

理由：

1. A1~A12 自动化验收项全部通过：迁移 0041 双向可迁移；PG 集成套件真实执行非 skip（test_memory_injection_postgres.py 18/18、单测 8/8、治理与 decay 回归 48/48）；全量后端回归（同一 PG）550 passed, 20 skipped（20 个 skip 经 `-rs` 确认全为 CREATEDB 角色/opt-in Milvus/无 embedding 列等环境性跳过，无功能 PG 用例被 skip）；非 PG 套件 265 passed, 297 skipped；前端 tsc 零告警、i18n 四 locale 键集完全一致、Playwright Phase 6 范围 26/26 全绿；ruff check 零告警。
2. 三条一票否决项 C1（泄露面）、C2（删除级联）、C4（租户隔离）与热路径批量写入一票否决项均有双层证据（PG 集成测试 + 独立对抗探针直查 DB）通过；C3、C5 通过。
3. `memory.injected` 事件消费方未受影响：Phase 4 观测 hit_rate 聚合用 EXISTS 语义与 payload 无关（observability/service.py），Phase 4 事件 payload 精确匹配用例（含 item_ids）全绿；Phase 1-5 回归套件全绿。
4. 无 P0/P1 未关闭。

**「有条件」之处**（发布前必须完成的人工环节，未写作通过）：

- **排障 UAT 计时（M1）**：05 §4 规定的核心业务价值验证（≤5 分钟全程界面操作）必须人工在真实环境执行，自动化无法计时。
- **类生产性能实测（M3，关联 P2 缺陷 D1）**：查询 P95≤2s 与万级条目首屏 P95≤1.5s 无任何计时证据，且 customer_detail 全量拉取实现存在超标风险。
- **界面真实环境走查（M2）、覆盖率对账（M4）、quickstart 形态验证（M5）**：05 §2/§5 规定的环境与指标环节尚未执行。
- **P2/P3 遗留处置签字（M6）**：D1 需产品负责人按 04 §8 签字遗留；D2/D3/D4 排期决策。

上述 manual-required 项完成并签字前不得发布。

## 9. 签字

| 角色 | 签字 | 日期 |
|---|---|---|
| 产品负责人 | 待签 | |
| QA | 待签 | |
| 研发负责人 | 待签 | |
| 客服主管/运营 | 待签 | |
