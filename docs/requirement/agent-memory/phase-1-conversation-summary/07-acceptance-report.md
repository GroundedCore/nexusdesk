# Phase 1 · 会话摘要记忆 — 验收报告

> 状态：已完成（结论：**有条件通过**，见 §7；2026-10-10 修订轮复验记录见 §8）
> 创建：2026-10-10
> 依据：`05-acceptance-plan.md`（A1~A6、业务指标、合规核对 C1~C3）、`06-test-report.md`（测试执行结果，含 §10 修订轮）
> 执行：验收代理（自动化验收轮）；执行日期 2026-10-10（修订轮复验同日，触发原因 `ae82a65` 部署形态文档修订）

## 1. 验收组织与范围说明

本轮为**自动化验收轮**：A1/A3/A4/A5/A6 按验收方案用 mock/demo 模型在集成测试层面复现（本机引擎层验收脚本 + CI postgres:17 集成测试双层佐证，证据含命令、输出与断言快照）。以下各项超出本环境交付能力，按方案要求如实标记：

- **A2**（坐席工作台 UI）：标记 `pending_env`，后端证据已就绪（见 §3-A2）；
- **UAT**（坐席代表判定）与**真实模型业务指标**（指标 1、2）：标记"待生产验证"并给出验证方法（见 §4）；
- CI job 日志明细：无 admin 权限无法下载，以 step conclusion + 本地复跑佐证（见 `06-test-report.md` §9）。

**前置条件核对**：`04-test-plan.md` 全部用例通过（`06-test-report.md` §3~§5），无 P0/P1 未关闭。验收数据使用 CI 独立测试租户（`test-<uuid>`），不与其他数据混用。

## 2. 验收环境

| 环境 | 配置 | 承担场景 |
|---|---|---|
| 本机（macOS，无 PG/Docker） | demo/mock 模型 | A1/A3/A5/A6 引擎层语义复现；ruff + 非 postgres 全量回归 |
| CI（ubuntu + postgres:17 service） | demo 网关 + 可编程 mock 摘要模型 | A1/A3/A4/A5 持久化链路（任务投递→worker 执行→落库→注入/审计） |
| 类生产（真实模型）+ 坐席工作台 | — | 本轮不可用：A2 UI、UAT、指标 1/2 留待该环境 |

## 3. 功能验收记录（A1~A6）

执行人：验收代理（自动化）；日期：2026-10-10。

| # | 判定 | 执行记录与实际结果 | 证据 |
|---|---|---|---|
| A1 | **通过（自动化层）**；真实模型作答质量待生产验证 | 前置：`history_turns=10`，25 轮对话，第 1 轮声明"订单号 A123，对花生过敏"，中间无关填充，末轮问"推荐零食"。实际：①引擎层（本机脚本）：早期事实已滑出窗口（断言=True），注入消息序列 `SystemMessage(prompt)→SystemMessage(【此前对话摘要】)→history→HumanMessage`，注入体含"订单号 A123"与"花生过敏"，run 正常完成；②持久化层（CI P1-IT-01/02）：截断触发任务→worker 执行→`runtime_conversations.summary` 非空→下一 run 携带摘要，且摘要逐轮滚动 | `06-test-report.md` 附录 B；CI run 38025292718 |
| A2 | **pending_env** | 本环境无坐席工作台 UI，未执行。后端证据已就绪：CI P1-IT-07 断言 handoff summary = `【对话摘要】客户对花生过敏，订单号 A123。…\n\n【最近消息】\nuser: …` 两段结构；P1-IT-08 断言无摘要时与旧格式逐点一致。**所需环境**：前端控制台 + 坐席工作台页面 + 一个带摘要的真实会话；**建议验收人**：坐席代表 + 产品负责人 | CI run 38025292718 |
| A3 | **通过** | ①引擎层（本机脚本）：`summary_enabled=false` 时 system 消息仅 1 条（不注入），开启后 2 条（恢复注入）；②CI P1-IT-09：全局关闭下发生截断仍零任务、零摘要落库；P1-IT-10：Agent 级草稿关闭→发布后仅该 Agent 无任务、其他 Agent 不受影响→回滚后恢复。"早期信息丢失（行为与旧版一致）"由 A1 引擎层前半段（事实确已滑出窗口）与 IT-09（无摘要产生）共同佐证 | `06-test-report.md` 附录 B；CI run 38025292718 |
| A4 | **通过** | 摘要模型指向故障 profile（mock 持续抛 `model_provider_unavailable`）：对话 run 全程 `completed`；第 1 次失败任务回 `pending`、`attempts=1`、`run_after` 为退避后的将来时刻；第 2 次失败后 `failed`、`attempts=2`、不再重试；旧摘要"保留摘要"原样保留；审计 `memory.summary.failed` 1 条（含 error 字段）；warning 日志含 "failed permanently"。另有 P1-IT-05b：无可用 profile 直接 `failed(no_chat_profile)` 不重试 | CI P1-IT-05/05b |
| A5 | **通过** | ①引擎层（本机脚本）：5 轮（≤10 轮窗口）对话 `dropped=0`，投递谓词 `dropped>0` 不成立；②CI P1-IT-06：窗口内对话后 `memory_tasks` 无任务行、`gateway_calls` 中 `actor='memory'` 计数为 0，worker 无任务可执行 | `06-test-report.md` 附录 B；CI P1-IT-06 |
| A6 | **通过** | 构造 4016 字符超长摘要注入对话：注入正文恒被截断至 1600 字符（=800 tokens × 2 字符/token 估算预算），尾部近期信息（"订单号 A123。"）保留，模型调用正常完成、上下文不溢出；本地 P1-UT-08、`test_engine_injection_obeys_token_budget` 同向断言 | `06-test-report.md` 附录 B；本地 P1-UT-08 |

## 4. 业务指标逐项判定

| 指标 | 目标 | 本轮判定 | 数据 / 验证方法 |
|---|---|---|---|
| 长对话（>20 轮）早期信息引用正确率 | ≥ 90% | **待生产验证** | 需真实模型。验证方法：评估集 ≥20 个 >20 轮 case（每个含 2 个早期关键事实提问）跑批 + 抽样人工判分。前置链路已就绪：A1 证明早期事实经摘要完整进入模型输入（mock 模型不具备推理能力，作答正确率本身须真实模型测量） |
| 坐席接管后重复询问率 | 下降 ≥ 50% | **待生产验证** | 需上线后数据。验证方法：上线后 2 周人工抽检 ≥30 个接管会话，与上线前基线对比。前置链路已就绪：两段式 handoff 后端断言通过（A2 后端证据） |
| 单轮注入 token 成本 | ≤ 800 tokens | **通过（已测量）** | A6 实测：任意长度摘要注入正文恒 ≤1600 字符（800 tokens 估算预算），超出取尾部。生产复核方法：`gateway_calls` / run trace 抽查 ≥20 个注入实例 |
| 对 run 延迟影响 | 0（异步） | **通过（行为断言）** | CI P1-IT-04：摘要任务 pending 期间 run 正常 `completed` 且沿用旧摘要，确定性证明非阻塞语义；投递在 `finish()` 同事务内完成，run 路径无额外 LLM 调用。说明：未做开关开/关各 20 run 的延迟分布对比——CI 共享 runner 耗时断言必然 flaky，该偏差已经 QA 复核接受 |

## 5. 合规核对

| # | 核对项 | 判定 | 证据 |
|---|---|---|---|
| C1 | 审计事件不含明文敏感信息 | **通过** | CI P1-IT-01 断言审计 details 仅含 conversation_id / 输入消息数 / 前后长度 / 模型用量，且消息原文不出现在 payload（`"问题0" not in details`）；失败审计仅含 error code |
| C2 | 摘要存会话行，随会话删除而删除，无独立泄露面 | **通过** | 摘要为 `runtime_conversations.summary` 列（迁移 0030 仅加列加表，P1-CM-01）；P1-IT-11 断言会话关闭/删除后任务跳过写库；P1-CM-04 断言跨租户任务不可写他租户摘要、跨租户 claim 不可达 |
| C3 | 全局开关可即时整体关闭，回滚无需动迁移 | **通过** | P1-IT-09：`summary_enabled=false` 即时零任务零摘要；P1-IT-10：Agent 级开关随草稿/发布/回滚；迁移 0030 可回滚且存量数据保留（P1-CM-01），功能回退仅需改配置 |

## 6. 遗留问题清单

| # | 级别 | 问题 | owner 建议 | 期限建议 |
|---|---|---|---|---|
| L1 | P2 | FR-1 要求摘要输出 ≤500 字；实现（prompt 指令与硬截断）按 `summary_max_output_chars=1000` 字符执行（`settings.py:61`）。注入侧 800 tokens 预算兜底，成本与上下文安全不受影响，仅摘要可能偏长、滚动信息密度低于预期 | 产品负责人：确认收紧默认值至 500，或修订需求口径并签字 | 上线前 |
| L2 | P3 | 无 Agent 绑定的 legacy 会话每次截断产生一个不可重试的 `failed(no_chat_profile)` 任务，`memory_tasks` 失败行随轮次累积（符合降级语义，但存在表膨胀与失败噪音） | 研发负责人：Phase 2 决策——投递时跳过无绑定会话，或引入租户默认 profile | Phase 2 启动前 |
| L3 | P3 | 硬杀（kill -9 等非优雅停机）的 MemoryWorker 持有的 running 任务不会被重置（无 lease/reaper）；优雅停机路径已被 P1-IT-14 覆盖 | **已关闭（2026-10-10）**：lease/reaper 随 memory-worker 独立容器（`apps/worker/memory.py` + production compose `memory-worker` 服务）一并落地——claim 写 owner + `lease_expires_at=now()+120s`、执行期间心跳续约、claim 前置 reaper 重置过期 running（attempts 达上限者置 failed）；覆盖用例 P1-IT-15~21 | 已关闭 |
| L4 | pending_env | A2 坐席工作台 UI 验收未执行（后端证据已就绪）；另 FR-5 的 `summary.queued` 事件前端控制台是否渲染未在本阶段范围 | 产品负责人 + 坐席代表（A2/UAT）；前端负责人（事件渲染确认） | 前端联调环境就绪后 1 周内 |
| L5 | 待生产验证 | 指标 1、2 及 §4 所列生产复核抽查 | 研发负责人出具测量数据，产品负责人判定 | 上线后 2 周内 |
| L6 | P2（CI 阻塞） | 2026-10-10 修订轮的 postgres 集成证据缺口：P1-IT-13~21、P1-CM-05 未在任何环境执行（本机无 PG 属既定约束；CI 因 PR #1 已关闭未触发，详见 §8.3）。代码本体经 QA 第 3 轮对抗性复核无缺陷 | 研发负责人：重开 PR #1 或以 `feat/agent-memory-phase-1` 新建 PR；QA：Backend job（postgres:17）跑绿后回填 `06-test-report.md` §10 并关闭本项 | PR 重开后首个 CI run |

## 7. 结论与签字

**结论：有条件通过。**

- A1/A3/A4/A5/A6 在自动化层全部通过；A2 标记 `pending_env`（所需环境与验收人已明确，后端两段式证据已就绪）；
- 合规核对 C1~C3 全部通过；无 P0/P1 遗留；
- 可测量硬指标（注入 ≤800 tokens、异步零延迟影响）本轮测量通过；指标 1、2 按方案定义需真实模型与上线后抽检，判定"待生产验证"并附验证方法（L5）；
- 遗留 1×P2（L1，需产品签字确认口径）+ 1×P3（L2），均有 owner 与期限建议，符合验收方案 §7"有条件通过"标准（L3 已于 2026-10-10 随 memory-worker 独立容器与 lease/reaper 落地关闭）。
- 2026-10-10 修订轮（`ae82a65` 部署形态修订，见 §8）：部署形态对齐与租约/reaper 经静态核验 + 单测层复验通过，L3 关闭确认；新增 L6（CI 阻塞，postgres 集成证据待 PR 重开后跑绿回填），不影响代码本体结论。

| 角色 | 签字 | 日期 |
|---|---|---|
| 产品负责人（含 L1 P2 处置确认） | 待签 | |
| QA | 验收代理（本轮自动化执行 + 2026-10-10 修订轮复验）：通过 | 2026-10-10 |
| 研发负责人 | 待签 | |

## 8. 修订轮复验记录（2026-10-10，`ae82a65` 部署形态文档修订触发）

**触发原因**：`ae82a65` 修订 Memory Worker 部署形态（quickstart 内嵌 API 进程、生产独立 `memory-worker` 常驻容器、并发度=副本数）并将 Phase 4 设计 §4 的租约/reaper 提前落地；代码对齐提交 `d43a02b`..`e863e34`（分支 HEAD `e863e34`）。本轮复验受部署形态影响的验收项，并新增部署形态专项核验。测试执行明细见 `06-test-report.md` §10。

### 8.1 受影响 AC 复验结论

| 项 | 复验结论 | 证据 |
|---|---|---|
| A1 摘要链路端到端（内嵌模式下仍通过） | **通过（代码层 + 首轮 CI 链路）** | `summary.py`/`engine.py`/handoff 本轮零改动（`git diff ae82a65..HEAD` 为空），摘要生成/注入/降级语义不受部署形态调整影响；内嵌模式下 claim 循环改由 API lifespan 经 `embedded_memory_worker` 装配（开关=`AGENT_EMBEDDED_WORKER AND summary_enabled`），首轮 CI 的 P1-IT-01/02 链路证据仍然有效；本轮 P1-IT-15/16 补充两形态闭环证据（待 CI） |
| A3 开关行为 | **通过** | 新增 P1-UT-12 固化装配开关四组合（本机通过）；全局/Agent 级开关语义（P1-IT-09/10）未受影响；`summary_enabled=false` 时独立入口空转待命而非退出，避免 `restart: unless-stopped` 崩溃循环 |
| A4 失败降级 | **通过（代码层 + 首轮 CI）** | 重试/退避/failed 语义未改动（P1-IT-05/05b 首轮 CI 通过）；新增保障——reaper 对 attempts 达上限任务直接置 `failed(memory_worker_lost)`（P1-IT-21，待 CI），`_finish` owner 守卫防租约被收割后误写状态/审计 |

### 8.2 部署形态专项核验（新增）

| 核验项 | 判定 | 证据 / 说明 |
|---|---|---|
| 内嵌 API 模式任务可被处理（quickstart 两容器形态闭环） | **通过（静态 + 单测层）；集成证据待 CI** | quickstart compose 与 `ae82a65` 逐字节一致（diff 为空，postgres+app 两容器承诺未破坏）、`AGENT_EMBEDDED_WORKER=true`；lifespan 装配/拆卸顺序（`stop()→await task→serve() finally reset_running`）经代码复核正确；P1-UT-12 本机通过；P1-IT-15（内嵌形态停机重置 running）待 CI |
| 独立入口可启动并认领任务 | **pending_env（CI）** | `apps/worker/memory.py` 独立入口存在、与 `apps/worker/knowledge.py` 先例形态一致；端到端用例 P1-IT-16 已就绪，但 postgres 环境本轮不可用（本机无 PG/Docker；CI 未触发，见 §8.3），不虚构执行结论 |
| 租约 reaper 生效 | **pending_env（CI）** | claim 写 owner + 120s 租约、30s 心跳续约、claim 前置 reaper、attempts 上限分流置 failed，均经代码复核并与 `knowledge/ingestion.py` 先例（`docs/modules-delivery.md`：SKIP LOCKED + 120s 租约 + reap）逐点比对一致；P1-IT-17/18/19/20/21 已就绪待 CI |
| 生产 compose 配置静态核验 | **通过** | `test_deployment.py` 本机通过（29 passed 含全部部署用例）：`memory-worker` 复用 `x-backend` 锚点（同镜像、同 env_file、同 credentials 卷）、command=`[python, -m, agent_platform.apps.worker.memory]`、`AGENT_EMBEDDED_WORKER=false`、depends_on migrate 完成；生产服务清单仅 migrate/api/runtime-worker/knowledge-worker/memory-worker/web，**无新中间件** |
| 真实多副本部署、容器编排级验证（扩缩容、硬杀容器后的租约恢复） | **pending_env** | 所需环境：Docker/容器编排环境 + PostgreSQL。建议方式：`docker compose -f deploy/production/compose.yaml up -d --scale memory-worker=2`，`docker kill` 任一 memory-worker 容器后观察其 running 任务在租约过期（≤120s）后被其他副本重认领并执行至 done（P1-IT-20 的编排级对应）；另观察 `memory_tasks.lease_expires_at` 与 worker 日志 |

### 8.3 CI 状态（本轮阻塞）

- 核验方式：本机无 `gh` CLI、无写凭据，经未认证 GitHub REST API 核验公开仓库（2026-10-10，验收代理亲自执行）。
- HEAD `e863e34` 的 check-runs `total=0`；分支最新 run 为 <https://github.com/GroundedCore/nexusdesk/actions/runs/38036813999>（head `ae82a65`，pull_request，success，2026-10-10T08:07:54Z），早于本轮全部 5 个提交；PR #1 state=closed、`merged_at=null`；`.github/workflows/ci.yml` 触发器仅 `push[main]`/`pull_request`/`workflow_dispatch`。
- **结论：本轮全部 postgres 集成用例（P1-IT-13~21、P1-CM-05）从未在真实 PG 上执行**，其"待 CI 执行 / pending_env（CI）"标记须随 PR 重开、Backend job（postgres:17）跑绿后方可转为通过。解除路径：重开 <https://github.com/GroundedCore/nexusdesk/pull/1> 或以 `feat/agent-memory-phase-1` 新建 PR。

### 8.4 遗留清单刷新

- **L3：已关闭（2026-10-10）**——§6 已登记；本轮验收复核确认实现方式与测试证据齐备（§8.1/§8.2、`06-test-report.md` §10.5），关闭结论维持。
- **新增 L6**（P2，CI 阻塞）：见 §6 表。解除后需回填 `06-test-report.md` §10.1/§10.4 与本节 §8.2 的 pending_env（CI）项。
- L1/L2/L4/L5 维持原判定不变。
