# Phase 3 · 事件记忆 + 语义检索 — 测试方案

> 状态：待实施（本阶段开发完成后执行）
> 创建：2026-10-10
> 依据：`02-requirements.md`（FR-1~FR-5、AC-1~AC-7）、`03-technical-design.md`（迁移 `0032`、MemoryStore 双实现、事件写入、TTL）

## 1. 测试范围与原则

**范围**：工单事件自动写入（创建/解决/关闭/承诺）、`memory_search` 内置工具、pgvector/TSVECTOR 双模式检索、高优事件常驻注入、事件 TTL 与清理任务、embedding 失败降级与重试、pgvector 镜像部署变更。

**原则**：

1. **双模式矩阵必测**：所有检索相关用例在 pgvector 与 TSVECTOR 两种模式下各执行一遍，排序语义一致（同一衰减函数）。
2. **降级路径常驻**：无 pgvector 环境功能完整可用不是一次性验证，而是常驻 CI 保障。
3. **部署承诺不破**：quickstart 两容器、无新中间件承诺不变；pgvector 镜像为唯一中间件级变更，替换与回退都要测（生产 memory-worker 容器复用后端镜像，不计入新中间件）。
4. **时间类用例不等待**：TTL、时间衰减通过直接改 `expires_at` / `last_referenced_at` 构造。
5. **衰减只影响排序**：旧事件降权不隐藏——检索可见性不受衰减影响，必须有断言固化。

## 2. 测试环境与数据构造

| 环境 | 用途 |
|---|---|
| 单测 | 衰减函数、事件渲染、Store 探测逻辑、参数裁剪 |
| 集成 A（PG 17 普通镜像） | TSVECTOR 模式全量用例；迁移探测块在无扩展时正常 |
| 集成 B（pgvector 镜像） | vector 模式全量用例；CI 增加该 job（矩阵） |
| quickstart 双容器 | 部署形态冒烟；镜像替换/回退 |
| mock/demo 模型 | 全部自动化用例；embedding 响应可编程（正常/报错） |

**数据构造约定**：事件条目直接写 `memory_items`；时间衰减场景 `UPDATE last_referenced_at = now() - interval 'N days'`；检索质量用例的 query 与 content 需有可控的相关度差异（text 模式靠词汇重叠，vector 模式 mock embedding 用可控向量距离）。

## 3. 单元测试（store / events）

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P3-UT-01 | Store 探测：有扩展 + 有 embedding profile | `available()='vector'`，选 PgVectorStore | 技术设计 §1 |
| P3-UT-02 | Store 探测：缺任一条件 | 选 TsVectorStore | FR-2 降级 |
| P3-UT-03 | `memory_vector_enabled` 覆盖 | `false` 强制 text（即使 pgvector 可用）；`true` 强制 vector | FR-5 |
| P3-UT-04 | 时间衰减函数 | 两个 Store 复用同一函数；age=半衰期时因子=0.5；age 基于 `last_referenced_at` 而非 created_at | FR-2 |
| P3-UT-05 | 衰减只影响排序 | 超老条目仍返回（排序靠后），不因衰减被过滤隐藏 | 技术设计 §1 |
| P3-UT-06 | 四类事件内容渲染 | created/resolved/closed/commitment 的 content 模板与 key 规则（`ticket:{no}` / `ticket:{no}:resolved` / `commitment:{log_id}`） | FR-1 |
| P3-UT-07 | resolved/closed 联动 | 生成解决/关闭条目的同时，`open_ticket` 类条目置 expired（非删除，保留审计） | AC-6/FR-3 |
| P3-UT-08 | `expires_at` 规则 | 事件类 = now + `memory_event_ttl_days`；commitment 类 = NULL | FR-4 |
| P3-UT-09 | memory_search 参数 | `limit` 默认 5；>10 截断为 10；空结果返回明确空提示文案 | FR-2 |
| P3-UT-10 | 工具注册条件 | customer 为空 / `memory_enabled=false` / tool_names 移除 / playground：四种情形工具均不出现在工具列表 | FR-2/AC-7 |
| P3-UT-11 | 双实现过滤一致性 | 两个 Store 的 search 都只返回当前 tenant + customer + active + type=event | FR-2 |

## 4. 集成测试

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P3-IT-01 | 工单全生命周期 | create→resolve→close 后：3 条事件记忆 active，open_ticket 条目 expired；各条目 source_type='ticket'、confidence=1.0、source_ref 正确 | FR-1 |
| P3-IT-02 | 事件写入异步性 | 工单状态接口同步返回无感（延迟对比）；`event_write` 任务执行后记忆可查；写入延迟 ≤ 1 分钟（Worker 轮询周期内） | 非功能/业务指标 |
| P3-IT-03 | 无 customer 工单的流程 | 不产生事件记忆任务，工单流程不受影响 | FR-1 |
| P3-IT-04 | commitment 标记入口 | 工单备注/回复 API 带 `is_commitment=true` → 承诺记忆生成；未标记不产生 | FR-1 |
| P3-IT-05 | 常驻注入 | 有在途工单/未兑现承诺的客户进线：消息序列含 `【待跟进事项】`，≤5 条、按 updated_at 倒序；工单 resolved 后下一轮注入消失 | AC-3/AC-6 |
| P3-IT-06 | 工具调用端到端 | mock 模型返回 memory_search tool_call → 工具执行 → 结果含目标事件 content（双模式各跑一遍） | AC-1/AC-4 |
| P3-IT-07 | 双模式检索等价性 | 同一 query 在 vector / text 模式均能命中目标条目 | AC-4 |
| P3-IT-08 | 时间衰减排序 | 构造两条相关度相近事件（3 天前 / 150 天前，UPDATE last_referenced_at）：3 天前排前；调整半衰期配置后排序变化符合公式 | AC-4b |
| P3-IT-09 | TTL 清理 | 构造 `expires_at` 已过条目 → 执行清理任务 → status=expired；检索与注入均不可见；commitment 类不过期 | AC-5 |
| P3-IT-10 | embedding 失败降级 | mock embedding 报错：条目正常写入、`embedding_failed=true`、text 模式仍可检索；重试任务成功清标记；连续失败保留标记等下轮 | 非功能 |
| P3-IT-11 | 检索过滤 | 检索结果仅含当前 tenant + 当前 customer + status=active + type=event；profile/preference 不走检索 | FR-2 |
| P3-IT-12 | 检索性能 | 单客户 1000 条事件数据下 P99 ≤ 300ms（pgvector 模式；本地基准，CI 可放宽为冒烟阈值） | 非功能 |

## 5. 部署/迁移测试

| 编号 | 用例 | 预期 |
|---|---|---|
| P3-DP-01 | pgvector 镜像全新部署 | 迁移建 vector 列与 HNSW 索引；Store 探测为 vector |
| P3-DP-02 | 普通 postgres 镜像部署 | `0032` 迁移正常通过、无 vector 列；探测降级 text；检索/注入/TTL 用例在 text 模式全绿 |
| P3-DP-03 | 镜像替换升级（postgres → pgvector，同数据卷） | 数据保留；替换后探测切换 vector，新条目开始生成 embedding |
| P3-DP-04 | 镜像回退（pgvector → postgres） | 服务可用，自动降级 text，vector 列残留无害 |
| P3-DP-05 | embedding 维度校验 | 输出维度与迁移默认（1536）不一致的 Embedding profile 在配置校验时拒绝绑定 |

## 6. 通用核对项（本阶段适用）

| 编号 | 核对项 | 通过标准 |
|---|---|---|
| P3-CM-01 | 配置加载 | `memory_event_ttl_days` / `memory_search_limit_max` / `memory_vector_enabled` / `memory_event_inject_max` / `memory_search_half_life_days` 默认值与覆盖生效 |
| P3-CM-02 | 模型用量 | embedding 调用走模型网关、落 `gateway_calls` |
| P3-CM-03 | 异步可靠性 | 事件写入/重试/清理任务遵循 memory_tasks 语义（claim 并发安全、失败退避、停机重置） |
| P3-CM-04 | 文档 | `deploy/README.md` 补充降级差异说明（有/无 pgvector 的行为差异） |

## 7. 回归范围

- Phase 1/2 测试全绿（注入段追加后各小节顺序正确）；
- 现有工单状态机、process_log 相关测试全绿（`is_commitment` 默认 false 不改变现有行为）；
- 无 pgvector 容器跑全量测试（CI 矩阵 job）常驻。

## 8. 缺陷分级与出口准则

| 级别 | 定义 | 处理 |
|---|---|---|
| P0 阻断 | 检索返回其他租户/其他客户数据；工单主流程破坏 | 修复并回归后方可继续 |
| P1 严重 | 事件错写/漏写；resolved 联动失效导致待跟进事项常驻；降级环境功能缺失 | 修复前不得进入验收 |
| P2 一般 | 检索排序质量欠佳；空提示文案问题；性能接近阈值 | 记录后可带问题验收，需产品签字 |
| P3 轻微 | 日志、展示问题 | 排期修复 |

**出口准则**：双模式矩阵用例全绿（CI 含 pgvector job）；AC-1~AC-7（含 AC-4b）复现通过；部署/迁移测试 P3-DP-01~05 通过；无 P0/P1 未关闭。
