# Phase 7 · 生产观测与告警 — 需求文档

## 用户故事

**US-1（平台运维）**：我在观测页一眼能看到记忆任务队列是否积压、embedding 是否大面积失败、检索是否在降级，而不是等用户报障。

**US-2（平台运维）**：记忆 Worker 挂掉或维护任务漏跑时，系统能在半小时内产生告警记录，我按告警排查而不是定期人肉巡检。

**US-3（运营）**：我想按 Agent 对比记忆注入率与写入量，判断哪些 Agent 的记忆配置需要调优（配合 Phase 5 的开关）。

**US-4（研发）**：联调与灰度期间，我能看到 memory_search 的调用量、空结果率与降级率，评估检索质量变化。

## 功能需求

### FR-1 观测指标扩展

在既有 `GET /observability/memory`（返回抽取量/命中率/积压/治理/衰退/检索计数）基础上扩展，全部从现有表直查聚合，限定时间窗（默认 24h，可选 7d）：

| 类别 | 指标 | 数据来源 |
|---|---|---|
| 任务健康 | memory_tasks pending/running/failed/done 数量 | `memory_tasks` |
| 任务积压 | 最老 pending 任务年龄 | `memory_tasks.run_after`（或新增 created_at，见技术设计 D1） |
| 任务性能 | 按 kind 的平均/P95 执行耗时、成功率 | `memory_tasks`（新增 duration_ms，见 D1） |
| embedding | 当前失败积压数（embedding_failed=true）、每日重试扫描量/成功量 | `memory_items` + 重试审计 |
| 检索模式 | vector / text / 单次降级（keyword fallback）调用分布 | `memory_search.completed` 事件 data 扩展 mode 字段 |
| 检索质量 | 调用数、命中数、空结果率、平均结果数 | 同上事件 |
| 维护健康 | 最近一次 TTL/衰退迁移/容量淘汰/规则扫描/embedding 重试的执行时间与结果 | 新增维护完成审计 `memory.maintenance.completed` |
| Agent 维度 | 按 agent_id：绑定客户 run 数、注入率、抽取写入量、任务失败量 | `runtime_runs` + 审计 + 任务表 |
| 注入明细 | 平均注入条数、flag/profile/preference/event 分布 | `memory_injection_log`（Phase 6；未上则按事件计数降级） |
| 质量 | pending 确认率、拒绝率（7d/30d） | `memory.item.confirmed/rejected` 审计 |

前端观测页"记忆"卡片区按上述类别分组展示；维护健康超期（如 24h 未执行）卡片置红。

### FR-2 轻量告警检查

复用 memory Worker 周期扫描框架新增告警检查任务（默认每 15 分钟，quickstart 内嵌形态同样生效）：

| # | 告警规则 | 默认阈值 |
|---|---|---|
| R1 | memory_tasks 15 分钟内出现 failed | > 0 条 |
| R2 | 最老 pending 任务年龄 | > 60 分钟 |
| R3 | embedding 失败积压持续增长 | 连续 2 个检查周期单调上升且 > 0 |
| R4 | vector 降级率（keyword fallback + text 模式占比） | > 20%（24h 窗，调用数 ≥ 20 才判定，防小样本误报） |
| R5 | 24 小时无成功维护任务（TTL/衰退/淘汰任一） | 超期 |
| R6 | 规则扫描超 2 个周期未执行（规则扫描开启时） | 超期 |
| R7 | pending 待确认积压 | > 500 条（平台可配） |
| R8 | memory_worker 心跳缺失（独立部署形态：无任务被 claim 且无空转心跳记录） | > 30 分钟 |

| 行为 | 说明 |
|---|---|
| 告警记录 | 写 `audit_records`，action `memory.alert.<rule>`，details 含规则、当前值、阈值、时间窗；不含敏感数据 |
| 冷却 | 同类告警冷却窗口默认 60 分钟（平台可配），冷却内不重复记录 |
| 展示 | 观测页新增"记忆告警"列表（时间、规则、当前值/阈值、状态） |
| 恢复 | 告警为事件制不维护状态机；展示层按"冷却窗内最后一次触发"呈现，恢复靠指标卡片变绿体现 |

### FR-3 Webhook 外发（可选增强）

| 项 | 说明 |
|---|---|
| 配置 | `memory_alert_webhook_url`（空 = 不外发，默认空） |
| 载荷 | JSON：规则、当前值、阈值、时间窗、租户、触发时间（不含敏感数据） |
| 约束 | 单 URL；同步发送带超时（≤ 5s）；失败仅记日志不阻塞检查任务；与告警记录共用冷却窗口，不重复外发 |

### FR-4 配置项

全部为平台级 settings（不进入 Agent 配置，与 Phase 5 边界一致）：

| 配置 | 默认 | 说明 |
|---|---|---|
| `memory_alert_enabled` | true | 告警检查总开关 |
| `memory_alert_scan_interval_minutes` | 15 | 检查周期（0 = 停用） |
| `memory_alert_cooldown_minutes` | 60 | 同类告警冷却 |
| `memory_alert_pending_task_max_age_minutes` | 60 | R2 阈值 |
| `memory_alert_pending_backlog_threshold` | 500 | R7 阈值 |
| `memory_alert_vector_fallback_rate` | 0.2 | R4 阈值 |
| `memory_alert_webhook_url` | "" | FR-3 |

## 非功能需求

- 观测聚合 SQL 全部限时间窗（默认 24h/7d），explain 无全表顺序扫描（万级行表）；
- 告警检查单轮执行 ≤ 5s，不阻塞任务 claim 主循环；
- 告警发现时延 ≤ 2 个检查周期（默认 ≤ 30 分钟）；
- 零新中间件：不引入 Prometheus/Redis 等；quickstart 双容器形态全功能可用。

## 验收标准

| # | 场景 | 预期 |
|---|---|---|
| AC-1 | 打开观测页记忆卡片区 | FR-1 十类指标分组展示，数值与数据库直查一致 |
| AC-2 | 构造一个 memory_task 失败 | ≤ 2 个检查周期内出现 `memory.alert.task_failed` 审计记录；观测页告警列表可见 |
| AC-3 | 同一告警条件持续满足 | 冷却窗口内不重复告警；冷却结束后再次告警 |
| AC-4 | 构造 pending 任务停留 61 分钟 | 触发积压告警，details 含最老年龄与阈值 |
| AC-5 | 停掉规则扫描（interval=0 之外的停用方式：阻塞 worker）超 2 个周期 | 触发 R6 告警 |
| AC-6 | 构造 24h 无维护任务执行 | 触发 R5 告警 |
| AC-7 | pgvector 不可用（quickstart TSVECTOR 形态）跑足量检索 | R4 按窗口占比正确判定；调用数 < 20 时不误报 |
| AC-8 | 配置 webhook 后触发告警 | URL 收到符合 schema 的 JSON；webhook 宕机时检查任务正常完成、仅记日志 |
| AC-9 | `memory_alert_enabled=false` | 无告警记录产生；观测指标照常 |
| AC-10 | Agent 维度指标 | 两个 Agent 产生差异化流量后，按 agent_id 的注入率/写入量/失败量分别正确 |
| AC-11 | 未上 Phase 6 的部署形态 | 注入明细指标按 `memory.injected` 事件计数口径降级展示，无报错 |
| AC-12 | quickstart 双容器 | 告警检查在内嵌 Worker 正常周期执行 |
