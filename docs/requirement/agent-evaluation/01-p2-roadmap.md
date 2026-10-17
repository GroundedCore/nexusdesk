# Agent 评测增强 · P2 路线图

本文件记录 Agent 工作区「评测」能力的分阶段规划。P0/P1 已交付，
本文件聚焦 P2，并明确当前落地状态。

状态标记：

- ✅ 已实现
- ⬜ 未实现（已规划，未开工）

---

## 1. 背景

P0 打通了「配置页摘要 → 评测视图 → 用例/报告/运行」的最小闭环；
P1 补齐了用例编辑/删除、单条用例试跑、报告分页、按版本筛选与报告对比。

P2 解决的是「让评测从手动工具变成可信赖的质量基线」：

- 结果可追溯（和环境版本绑定）
- 有趋势可看
- 能自动/定时跑
- 能作为发布门禁

---

## 2. P2-A · 质量趋势 + 运行环境快照 ✅ 已实现

### 2.1 交付内容

**报告运行环境快照（结果联动基础）**

- `evaluation_reports` 新增 `environment` JSONB 列（迁移 `0038_evaluation_environment`）。
- 每次全量评测落库时冻结运行环境：

```json
{
  "agent_version": 3,
  "model": { "profile_id": "...", "version": 2 },
  "tools": ["knowledge_search", "propose_ticket"],
  "knowledge": [{ "id": "...", "name": "售后知识库", "documents": 4, "published": 3 }],
  "memory": { "enabled": true, "write": true, "summary": true }
}
```

- 快照随报告一起冻结，之后模型方案 / 知识库 / 记忆开关变更不会改写历史。
- 历史报告该列允许为 `null`，前端按「未知」处理。

**质量趋势**

- 新增只读接口 `GET /api/v1/agents/{id}/evaluation-trend?limit=20`。
- 返回按时间升序的趋势点：`agent_version / total / passed / pass_rate / needs_review / errors`。
- 评测面板顶部展示最近若干次评测的通过率柱状趋势（自绘 SVG/CSS，不引入图表库）。

**界面联动**

- 报告卡片展示运行环境摘要：模型方案版本、知识库数量与已发布文档数、记忆开关、工具数量。
- 报告对比弹窗新增「运行环境」对照表（模型方案 / 记忆 / 知识库）与「用例结果」对照表。

### 2.2 验收

- 后端：`test_platform_postgres.py` 覆盖 environment 落库、趋势接口返回。
- 前端：`npm run typecheck` / `check:i18n` / `build` 通过；评测面板与对比弹窗截图核验。

---

## 3. P2-B · 评测任务队列 + 自动回归 + 定时评测 ⬜ 未实现

> 状态：未实现。以下为规划，不是现有 API 契约。

### 3.1 目标

- 评测不再依赖人工点击，支持发布后自动回归与按周期定时执行。
- 评测运行从「进程内布尔锁」升级为「可跨进程 / 多租户安全的任务队列」。

### 3.2 前置问题（硬约束）

当前 `EvaluationService.busy` 是**进程内**布尔锁，同时只能跑一个评测。
一旦引入自动/定时触发，会出现：

- 多个 worker 实例之间互不感知；
- 定时任务与人工触发互相 `429 evaluation_busy`；
- 长任务无法排队、无法重试、无法取消。

因此 P2-B 的**第一步必须是**把 `busy` 换成 DB 级并发控制。

### 3.3 规划改动

**数据层**（新迁移，`0039+`）

- `evaluation_jobs`：`id / tenant_id / agent_id / agent_version / status / attempts /
  lease_expires_at / error / trigger / created_at / updated_at`
- `evaluation_schedules`：`id / tenant_id / agent_id / enabled / interval_minutes / next_run_at`
- 并发控制：`evaluation_jobs` 上的唯一部分索引（同 agent 同时只有一个 running）
  或 `SELECT ... FOR UPDATE SKIP LOCKED` 领取任务。

**服务层**

- `EvaluationService.run()` 拆分为「入队」与「执行」两段。
- 新增 worker：生产独立容器 `evaluation-worker`；体验版（quickstart 两容器）
  沿用 memory worker 的「空闲周期」模式嵌在 API 进程内。
- 触发来源（`trigger`）：`manual` / `publish` / `schedule`。

**自动回归**

- `AgentConfigService.publish()` 成功后入队一次 `trigger=publish` 的评测任务。
- 结果写回 `evaluation_reports`，并复用 P2-A 的 `environment` 快照。

**定时评测**

- `evaluation_schedules` 由 worker 心跳扫描 `next_run_at <= now()`。
- 关闭/归档 Agent 时暂停调度。

### 3.4 验收要点

- 人工评测与自动回归并发时，任务排队而不是报 429。
- worker 崩溃后能靠 lease 超时重领，attempts 超限置 failed。
- 发布后无需人工操作即可产出报告。
- 定时任务到点执行，且同一 agent 不会重复入队。

---

## 4. P2-C · 数据集版本 + 发布门禁 ⬜ 未实现

> 状态：未实现。以下为规划，不是现有 API 契约。

### 4.1 目标

- 评测用例从「扁平列表」升级为「可版本化的数据集」。
- 发布前用评测结果作为门禁，阻断低质量发布。

### 4.2 数据集版本

**数据层**（新迁移）

- `evaluation_datasets`：`id / tenant_id / name / description`
- `evaluation_dataset_versions`：`id / dataset_id / version / cases jsonb / created_at`
- 报告记录 `dataset_version_id`，保证「同一组用例」可复现对比。

**兼容策略（需先定）**

现有 `evaluation_cases` 是每个 agent 最多 20 条的扁平表，
数据集成型后会改变归属语义，必须二选一：

1. 迁移期内保留 `evaluation_cases`，数据集仅作为可选分组；
2. 新建数据集表并把存量用例迁移为「每个 agent 的默认数据集 v1」。

### 4.3 发布门禁（关键设计决策）

**问题**：当前评测跑的是**已发布版本**（`agents.snapshot()` 取 `published_version`），
而门禁要判断的是**待发布的草稿**。两者不是同一个东西。

可选方案：

- **方案 1（推荐）· 候选版本评测**
  - 用草稿 config 构造临时 snapshot（不写 `agent_versions`）跑评测；
  - 通过后才允许 `publish`；
  - 语义严谨，但需要改 `snapshot` 调用方式并新增候选评测流程。
- **方案 2 · 沿用已发布版本结果**
  - 门禁只看「最近一次已发布版本的评测结果」；
  - 版本不一致时警告或阻断；
  - 改动小，但可能拦错版本。

**门禁分级**（不建议一步到位强制阻断）

1. 提示：配置页已展示最近评测摘要（P0 已实现）；
2. 警告：评测未通过或版本过期时二次确认（P1 已实现）；
3. 强制：高风险/生产 Agent 评测未通过则禁止发布（P2-C）。

### 4.4 验收要点

- 数据集版本冻结后，历史报告可按同一数据集版本复现对比。
- 候选版本评测不污染 `agent_versions`。
- 门禁可按 Agent 配置开关与阈值，且能审计「谁在什么结果下放行」。

---

## 5. 依赖关系

```text
P2-A ✅ 质量趋势 + 环境快照
   │
   └──> P2-B ⬜ 任务队列 + 自动回归 + 定时评测
             │
             └──> P2-C ⬜ 数据集版本 + 候选版本评测 + 发布门禁
```

- P2-B 依赖 P2-A 的环境快照（自动回归要能定位「跑的是哪套环境」）。
- P2-C 的门禁依赖 P2-B 的队列化评测结果（不能同步阻塞发布请求）。
