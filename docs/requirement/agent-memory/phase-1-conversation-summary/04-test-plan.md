# Phase 1 · 会话摘要记忆 — 测试方案

> 状态：待实施（本阶段开发完成后执行）
> 创建：2026-10-10
> 依据：`02-requirements.md`（FR-1~FR-5、AC-1~AC-6）、`03-technical-design.md`（迁移 `0030`、memory_tasks、注入/接管设计）

## 1. 测试范围与原则

**范围**：会话滚动摘要的生成、存储、注入；人工接管摘要改造；租户级/Agent 级开关；摘要任务表（`memory_tasks`）与 Memory Worker（含部署形态与租约/reaper，2026-10-10 修订后补充 P1-UT-12、P1-IT-15~21、P1-CM-05）。

**原则**：

1. **开关兜底必测**：全局 `summary_enabled=false` 与 Agent 级关闭后，行为必须与现状（纯滑动窗口）逐点一致。
2. **空态回归必测**：`summary` 字段为空时，现有对话、接管、评估行为与接入前完全一致。
3. **异步必测**：摘要生成全程不阻塞 run，对端到端延迟零影响。
4. **时间类用例不等待**：任务退避、去抖等通过直接改库（`run_after` / `created_at`）构造。

## 2. 测试环境与数据构造

| 环境 | 用途 |
|---|---|
| 单测 | prompt 组装、输出校验、任务合并、注入渲染（无需数据库的尽量走纯单测） |
| 集成（PG 17 + pytest postgres fixture） | 任务投递→Worker 执行→摘要落库→注入的完整链路 |
| mock/demo 模型 | 全部自动化用例；摘要模型响应可编程（固定文本、错误、超长、空串） |

**数据构造约定**：

- 会话/消息直接写 `runtime_conversations` / `conversation_messages` 构造任意轮次，不走真实对话驱动（E2E 除外）。
- 任务驱动：投递 `memory_tasks` 行后测试内手动执行一次 claim+execute，断言终态，不依赖真实 Worker 循环时序。

## 3. 单元测试（`test_summary.py` 等）

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P1-UT-01 | prompt 组装：旧摘要为空 + 被丢弃消息 | prompt 含全部丢弃消息、无旧摘要段 | FR-1 |
| P1-UT-02 | prompt 组装：旧摘要非空 | 旧摘要与新片段同时进入 prompt，指令要求合并 | FR-1 |
| P1-UT-03 | 输出校验：模型返回空串/纯空白 | 视为失败，保留旧摘要 | FR-1 |
| P1-UT-04 | 输出校验：返回超过 `summary_max_output_chars` | 取尾部截断后落库，不报错 | FR-1 |
| P1-UT-05 | 任务合并：同一会话已有 pending 摘要任务时再投递 | 不新增任务行，新 dropped 追加进已有 payload | 技术设计 §核心流程1 |
| P1-UT-06 | 任务合并：已有任务为 running | 不干扰运行中任务（按实现决策验证并固化行为） | 技术设计 §核心流程1 |
| P1-UT-07 | `dropped_messages` 超过 `summary_max_input_messages`（40） | 仅保留最近 40 条入 payload | FR-4 |
| P1-UT-08 | 注入渲染：摘要超 800 tokens 估算上限（字符数/2） | 取摘要末尾截断（近期信息优先） | FR-2 |
| P1-UT-09 | profile 选择：Agent 绑定 / 租户默认 / 无 profile | 分别命中绑定 profile、默认 profile、任务 failed(`no_chat_profile`) | 技术设计 §模型调用路径 |
| P1-UT-10 | 失败重试：第 1 次失败 | status 回 pending，`run_after` 为指数退避后的将来时刻，attempts=1 | FR-1 |
| P1-UT-11 | 失败兜底：`attempts` 达 `max_attempts`（2） | 任务置 failed，记审计事件，不再重试 | FR-1 |
| P1-UT-12 | 内嵌装配开关：`embedded_worker` × `summary_enabled` 四种组合 | 仅 `embedded_worker=true` 且 `summary_enabled=true` 时装配 MemoryWorker，其余返回 None | 技术设计 §核心流程3（2026-10-10 修订） |

## 4. 集成测试

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P1-IT-01 | 25 轮对话（`history_turns=10`），mock 摘要返回固定文本 | 截断发生后产生 `conversation_summary` 任务 → 执行 → `runtime_conversations.summary` 更新 → 下一 run 消息序列含 `【此前对话摘要】` | AC-1 |
| P1-IT-02 | 摘要逐轮滚动 | 第 2 次截断时 prompt 输入 = 第 1 次摘要 + 新丢弃消息；摘要滚动覆盖 | FR-1 |
| P1-IT-03 | 注入位置与格式 | 消息序列：SystemMessage(prompt) → SystemMessage(摘要) → history → HumanMessage；空摘要时无摘要消息 | FR-2 |
| P1-IT-04 | 摘要生成期间发起新一轮对话 | 新一轮沿用旧摘要；run 端到端延迟与无摘要时相当（对比断言） | FR-1/非功能 |
| P1-IT-05 | mock 摘要模型连续报错（≥ 3 次） | 对话正常完成；旧摘要保留；任务最终 failed；warning 日志 + 审计事件存在 | AC-4 |
| P1-IT-06 | 前 10 轮内（未发生截断） | 无摘要任务产生，`gateway_calls` 无摘要用量 | AC-5 |
| P1-IT-07 | handoff：会话有摘要时转人工 | handoff summary = `【对话摘要】…` + `【最近消息】…` 两段 | AC-2/FR-3 |
| P1-IT-08 | handoff：会话无摘要 | 行为与现状一致（仅最近 10 条拼接） | FR-3 兜底 |
| P1-IT-09 | 全局 `summary_enabled=false` | 截断发生也不投递任务；行为 == 现状 | AC-3/FR-4 |
| P1-IT-10 | Agent 级 `summary_enabled=false`（草稿→发布→回滚） | 该 Agent 不产生摘要；其他 Agent 不受影响；回滚后配置恢复 | AC-3/FR-4 |
| P1-IT-11 | 会话已关闭/删除时摘要任务执行 | 跳过写库（revision 检查），不报错 | 技术设计 §核心流程2 |
| P1-IT-12 | evaluation 模块跑 case | 空历史下摘要注入为空，评估结果与接入前一致 | FR-2 |
| P1-IT-13 | 并发 claim（多副本安全） | 两个并发 claim 模拟仅一个成功（SKIP LOCKED），任务不被重复执行；获胜方持有 owner 与租约 | memory_tasks 模式 |
| P1-IT-14 | 优雅停机 | Worker 收 shutdown 时 running 任务重置 pending（owner/lease 清空），不丢任务 | 技术设计 §核心流程3 |
| P1-IT-15 | 内嵌形态优雅停机 | API 内嵌装配（`embedded_memory_worker`）的 Worker 停机同样重置 running 为 pending | 技术设计 §核心流程3（2026-10-10 修订） |
| P1-IT-16 | 独立入口可运行 | `python -m agent_platform.apps.worker.memory` 以独立进程认领并执行任务；取消后无 running 残留 | 技术设计 §核心流程3（2026-10-10 修订） |
| P1-IT-17 | claim 写租约 | claim 后行含 owner 与 `lease_expires_at ≈ now()+120s` | Phase 4 设计 §4 租约/reaper（提前落地） |
| P1-IT-18 | reaper 重置过期租约 | claim 前置 reaper：租约过期的 running 重置 pending（owner/lease 清空）；租约未过期的 running 不动 | Phase 4 设计 §4 租约/reaper（提前落地） |
| P1-IT-19 | 心跳续约防误收割 | 执行期间心跳续约（租约前移）；另一副本的 reaper 不收割持有有效租约的 running 任务 | Phase 4 设计 §4 租约/reaper（提前落地） |
| P1-IT-20 | 收割后重认领执行到 done | 硬杀副本的 running 任务（租约过期）被 reaper 重置后，由新副本重新认领并执行到 done（owner/lease 清空、摘要落库） | Phase 4 设计 §4 租约/reaper（提前落地） |
| P1-IT-21 | reaper 对 poison task 置 failed | 租约过期且 attempts 已达上限的 running 任务被 reaper 直接置 failed（`error='memory_worker_lost'`，owner/lease 清空）；同批未达上限的任务重置 pending 并被重认领 | Phase 4 设计 §4 租约/reaper（知识库 attempts 上限先例） |

## 5. 通用核对项（本阶段适用）

| 编号 | 核对项 | 通过标准 |
|---|---|---|
| P1-CM-01 | 迁移 `0030` 在线性 | 存量含数据库上执行成功；只加列加表，不锁表现有读写 |
| P1-CM-02 | 配置加载 | `summary_enabled` / `summary_max_input_messages` / `summary_max_output_chars` 默认值正确、环境变量覆盖生效 |
| P1-CM-03 | 审计与观测 | 每次摘要生成有审计事件（conversation_id、输入消息数、前后长度、模型用量）；run trace 可见"摘要已更新"标记；审计 payload 无敏感信息 |
| P1-CM-04 | 租户隔离 | 摘要存会话行，继承 `tenant_id` 隔离；跨租户会话 ID 访问摘要不可达 |
| P1-CM-05 | 迁移 `0030` 含租约列 | `memory_tasks.lease_expires_at` 列存在、可空、默认 NULL |

## 6. 回归范围

- 现有 conversation / runtime / handoff / evaluation 测试全绿（空 summary 行为不变）；
- `runtime_conversations` 读写在未触发摘要路径时 SQL 行为不变。

## 7. 缺陷分级与出口准则

| 级别 | 定义 | 处理 |
|---|---|---|
| P0 阻断 | 对话主流程被破坏；数据丢失；跨租户泄露 | 修复并回归后方可继续 |
| P1 严重 | 摘要内容张冠李戴注入对话；任务丢失/无限重试；开关失效 | 修复前不得进入验收 |
| P2 一般 | 摘要质量欠佳、截断策略偏差、观测标记缺失 | 记录后可带问题验收，需产品签字 |
| P3 轻微 | 文案、日志问题 | 排期修复 |

**出口准则**：P1-UT/IT 与通用核对项全部通过；AC-1~AC-6 场景在集成环境复现通过；回归套件全绿；开关关闭状态下与基线行为 diff 为零；无 P0/P1 未关闭。
