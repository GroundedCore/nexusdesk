# NexusDesk 记忆系统 · 分阶段规划文档

> 状态：Phase 1-4 已上线；Phase 5-7 规划评审中（未开始实施）
> 创建：2026-10-09
> 修订：2026-10-17（新增 Phase 5-7 迭代规划）

## 背景

NexusDesk 当前的"上下文"仅有 `runtime_conversations.history` JSONB 滑动窗口（默认保留最近 10 轮，见 `backend/src/agent_platform/settings.py`），超出即丢弃，无摘要、无跨会话记忆。全库不存在 memory / 用户画像 / 偏好相关的表与模块。这导致：

- 长对话早期信息丢失（如客户第 1 轮说的关键约束，第 15 轮已遗忘）；
- 老客户每次进线都被当作新客户，无跨会话连续性；
- 人工接管时坐席只能看到最近 10 条消息拼接的摘要；
- 工单表预留的 `customer_id` 字段无数据来源，始终为空。

本目录是记忆系统的分阶段规划文档，供产品、研发按阶段独立评审。

## 总体原则

1. **零新中间件**：存储用 PostgreSQL，队列复用 PG 任务表 + Worker claim 模式，向量检索用 pgvector（可降级 TSVECTOR），LLM 调用走模型网关。与"两容器快速体验"部署承诺兼容。部署形态：quickstart 两容器承诺不变（Memory Worker 内嵌于 API 进程，跟随 `AGENT_EMBEDDED_WORKER`）；生产拓扑使用独立 `memory-worker` 常驻容器（复用后端镜像，非新中间件），与 runtime-worker 解耦、可独立伸缩（2026-10-10 修订，见 `phase-1-conversation-summary/03-technical-design.md` §核心流程3）。
2. **每阶段独立可上线、独立有价值**：不做依赖后续阶段才能生效的设计。
3. **记忆严格按 `tenant_id + customer_id` 维度隔离**：跨客户共享知识是知识库的职责，不属于记忆系统。
4. **错误记忆必须可人工纠正**：所有自动写入的记忆都可查询、可编辑、可删除、可溯源。

## 阶段路线图

| 阶段 | 主题 | 核心价值 | 难度 | 预估 | 目录 |
|---|---|---|---|---|---|
| Phase 1 | 会话摘要记忆 | 解决滑动窗口遗忘；提升人工接管上下文质量 | 低 | ~1 周 | [phase-1-conversation-summary/](phase-1-conversation-summary/) |
| Phase 2 | 客户实体 + 画像/偏好记忆 | 跨会话连续服务，记忆系统主体 | 中 | ~2-3 周 | [phase-2-customer-profile/](phase-2-customer-profile/) |
| Phase 3 | 事件记忆 + 语义检索 | 工单闭环联动；海量记忆按需检索（含检索时间衰减） | 中 | ~2 周 | [phase-3-event-memory-search/](phase-3-event-memory-search/) |
| Phase 4 | 治理、衰退与标记记忆 | 生产合规前提：纠错、遗忘、防污染；记忆衰退与强化保鲜 | 中低 | ~1-2 周 | [phase-4-governance/](phase-4-governance/) |
| Phase 5 | Agent 记忆配置开关 | 记忆行为按 Agent 场景裁剪；配置可见可改；只能收紧不能放宽 | 低 | ~1 周 | [phase-5-agent-memory-config/](phase-5-agent-memory-config/) |
| Phase 6 | 记忆可视化与注入可解释性 | 三层记忆视图；注入明细落库，"本次对话用了哪几条记忆"可回溯 | 中 | ~1.5-2 周 | [phase-6-memory-visualization/](phase-6-memory-visualization/) |
| Phase 7 | 生产观测与告警 | 任务/embedding/检索/维护健康度可视；轻量告警主动发现故障 | 中低 | ~1-1.5 周 | [phase-7-observability-alerts/](phase-7-observability-alerts/) |

各阶段目录下统一包含五份文档：

- `01-business-goals.md` — 业务目标、价值、成功指标、范围边界
- `02-requirements.md` — 用户故事、功能需求、验收标准
- `03-technical-design.md` — 数据模型、代码改动点、接口设计、测试方案
- `04-test-plan.md` — 测试方案：单元/集成/API 用例、非功能专项、出口准则（实现落地后执行）
- `05-acceptance-plan.md` — 验收方案：AC 逐条验收步骤、业务指标测量、合规核对与签字

## 明确不做的（整体边界）

- **程序性记忆**（Agent 在线自我进化）：Agent 质量改进走现有 evaluation 模块离线迭代，在线自学习不可控。
- **跨客户共享记忆**：属于知识库职责。
- **引入第三方记忆框架**（Mem0 / Zep / LangMem）：其多租户模型、存储依赖与平台体系不匹配；借鉴其抽取 prompt 与 ADD/UPDATE/DELETE 合并策略的设计思路，自研实现。
- **新中间件**（Redis / Celery / 独立图库等）：见各阶段技术文档的复用方案。

## Review 指引

- 每个阶段可独立评审、独立排期；建议按 Phase 1 → 4 顺序实施，Phase 4 的部分治理项可视合规要求提前穿插。
- 评审重点：
  - Phase 1：摘要生成时机与成本（每次截断触发一次 LLM 调用）；
  - Phase 2：身份解析规则（渠道侧只有 session_id 哈希）与抽取置信度阈值；
  - Phase 3：pgvector 引入决策（compose 镜像变更）与降级策略；检索排序时间衰减的半衰期默认值（30 天）；
  - Phase 4：PII 与"忘记我"合规流程；衰退机制的分类型半衰期与阈值（profile 365 天转 pending、preference 90 天、protected 免疫）、强化信号的三级近似（L1 同键 upsert / L2 检索命中 / L3 关键词重叠）是否可接受；
  - Phase 5：存量 Agent 迁移的行为等价性（旧三开关 → 新配置映射）；"Agent 只能收紧"的平台级能力清单是否完整；写入策略 `review` 模式与既有置信度阈值的关系；
  - Phase 6：`memory_injection_log` 的写放大控制（批量插入 + 90 天保留期）；审计时间线不引入新的 PII 泄露面；历史无明细数据的展示降级；
  - Phase 7：告警阈值默认值与冷却窗口（宁缺勿滥）；观测聚合 SQL 的时间窗限制（避免全表扫描）；零新中间件约束是否守住。

## 记忆种类现状（Phase 5-7 规划时盘点）

存储层 `memory_items.type` 共 4 类：`profile`（客户画像）、`preference`（客户偏好）、`event`（事件记忆）、`flag`（服务标记，治理标记而非认知记忆）；另有会话摘要 `runtime_conversations.summary`（工作记忆，非独立存储）。对应认知分类：工作记忆有（滑动窗口 + 滚动摘要）、情景记忆有（event，偏客服工单场景）、语义记忆有（profile + preference，非通用知识图谱）、**过程记忆没有**——Agent 在线自我进化维持"明确不做"（走 evaluation 模块离线迭代），Phase 5-7 不新增记忆类型。
