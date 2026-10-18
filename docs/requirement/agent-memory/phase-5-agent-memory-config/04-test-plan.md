# Phase 5 · Agent 记忆配置开关 — 测试方案

> 状态：待实施（本阶段实现落地后执行）
> 创建：2026-10-17
> 依据：`02-requirements.md`（FR-1~FR-6、AC-1~AC-11）、`03-technical-design.md`（配置模型、求值函数、门控点、D1~D3）

## 1. 测试范围与原则

**范围**：AgentConfig 新增字段与校验、`resolve_memory_config` 求值、注入/抽取/检索三侧门控、review 写入策略、memory_types 过滤、黑名单转人工下沉、前端表单联动与预设、存量兼容。

**原则**：

1. **存量行为等价是红线**：默认值下全部既有记忆测试（Phase 1-4 套件）必须原样通过；存量快照反序列化后行为不变有专项断言。
2. **"只能收紧"逐组合验证**：全局×Agent 的开/关组合、数值类的 min 截断，用参数化测试穷尽，不靠抽查。
3. **review 模式纵深验证**：入队快照、异步执行时配置已变更两个时序都要覆盖（write_mode 以任务 payload 快照为准）。
4. **平台级能力不可见**：PII/衰退/淘汰等不得出现在 Agent 配置表单与 API 返回的 Agent 配置结构内。

## 2. 测试环境与数据构造

| 环境 | 用途 |
|---|---|
| 单测 | `resolve_memory_config` 全组合、pydantic 校验、route_confidence 的 write_mode 分支、types 过滤纯逻辑 |
| 集成（PG + pytest fixture） | 注入门控、抽取任务 review 路由、检索工具注册门槛、存量快照等价 |
| 前端 | Playwright（`frontend/tests/agents-workspace.spec.ts` 扩展或新增 `agent-memory-config.spec.ts`）：表单联动、预设、平台禁用提示 |

**数据构造约定**：Agent 配置经 API 保存后发起新会话断言行为；进行中会话的快照沿用用 runtime_runs.config 直查断言。

## 3. 单元测试

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P5-UT-01 | 求值默认值 | 全默认 AgentConfig + 默认 settings → 全 true、auto、inject=20、search=5 | FR-1 |
| P5-UT-02 | 全局关闭优先 | `memory_inject_enabled=false`（D1）或 `memory_extract_enabled=false` 时，Agent 全开仍为关 | FR-2/AC-6 |
| P5-UT-03 | Agent 收紧 | 全局开 + Agent 关（read/write/search 各项）→ 关 | FR-2 |
| P5-UT-04 | 数值截断 | Agent inject_max=999 → 生效 min(999, 平台 20)=20；Agent=5 → 5；None → 平台值 | FR-2/AC-7 |
| P5-UT-05 | 黑名单 OR 语义 | 平台 false + Agent true → 生效 true；平台 true + Agent false → 生效 true | 03 §黑名单 |
| P5-UT-06 | 总开关联动 | `memory_enabled=false` → read/write/search 全 false（无论子开关） | FR-3 |
| P5-UT-07 | 检索依赖读 | read 关闭时 search 强制 false | FR-3 |
| P5-UT-08 | pydantic 校验 | `memory_types=[]` → 422；非法枚举值 → 422；下限越界（≤0）→ 422；超平台上限值（如 999）**允许保存**、运行时截断（AC-7） | FR-1/AC-7 |
| P5-UT-09 | 存量快照兼容 | 仅含旧三键的 config JSON 反序列化成功，新字段取默认值，求值结果与旧行为一致 | AC-9 |
| P5-UT-10 | review 路由 | `route_confidence(write_mode="review")`：置信度 0.95 也返回 pending；human/rule 来源不经此路由 | FR-4 |
| P5-UT-11 | types 过滤 | types=["profile"] 时 preference/event 条目注入查询被过滤；抽取产出 preference/event 计 dropped | FR-1/AC-5 |

## 4. 集成测试

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P5-IT-01 | 总开关关闭端到端 | 配置保存后新会话：无客户记忆注入段、无 memory_search 工具、finish 后无抽取任务入队 | AC-2 |
| P5-IT-02 | 只读不写 | 读开写关：既有记忆正常注入；会话结束无 memory_extract 任务 | AC-3 |
| P5-IT-03 | review 端到端 | mock 抽取产出高置信条目：全部 pending、确认前不注入、确认后注入 | AC-4 |
| P5-IT-04 | review 快照时序 | 入队后把 Agent 配置改回 auto，任务执行仍按 payload 快照的 review 路由 | 03 §review |
| P5-IT-05 | 注入上限生效 | 客户 25 条 active 记忆，Agent inject_max=3 → 注入段恰好 3 条（flag 不计） | FR-1 |
| P5-IT-06 | 检索上限与开关 | search_limit=2 → memory_search 返回 ≤2 条；search_enabled=false → 工具不注册 | AC 对应 FR-1 |
| P5-IT-07 | 事件写入边界（D3） | types 不含 event：事件仍写入（全局 event 开关开），但不注入 | 03 D3 |
| P5-IT-08 | 进行中会话不变 | 会话创建后修改 Agent 配置：当前 run 行为不变（config 快照直查断言）；新会话按新配置 | AC-10 |
| P5-IT-09 | 存量行为等价 | 升级前抓取的注入段 fixture（Phase 2/3 测试资产）在默认新配置下逐位一致 | AC-9 |
| P5-IT-10 | 黑名单转人工下沉 | 平台 false + Agent true：blacklist 客户进线自动转人工 | 03 §黑名单 |

## 5. API 测试

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P5-API-01 | 配置保存/读取 | 新字段完整往返；operator 可写、viewer 403、未认证 401 | FR-6 |
| P5-API-02 | 非法配置拒绝 | types 空、枚举非法、下限越界（≤0）→ 422 且响应 detail 指明字段名；超平台上限值（999）保存成功、运行时截断（AC-7） | FR-1/AC-7 |
| P5-API-03 | 平台上限提示接口 | caps 接口返回平台开关与上限值；与 settings 一致 | 03 §前端 |
| P5-API-04 | 配置审计 | 保存后审计记录含操作人与变更前后值 | AC-11 |
| P5-API-05 | 平台级能力不外泄 | Agent 配置 GET 响应中无 PII/衰退/淘汰等平台级键 | 原则 4 |

## 6. 前端测试（Playwright）

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P5-FE-01 | 分组渲染 | 10 项配置按 4 组展示，默认值正确 | AC-1 |
| P5-FE-02 | 总开关联动 | 关闭"客户长期记忆"→ 子项全部隐藏并提示，重新开启后恢复 | FR-3 |
| P5-FE-03 | 条件显隐 | 写关时 write_mode 隐藏；检索开才显示 search_limit | FR-3 |
| P5-FE-04 | 预设应用 | 逐套预设：应用后表单值符合预设表，可再修改，保存生效 | AC-8 |
| P5-FE-05 | 平台禁用提示 | mock 平台关闭注入：对应项禁用 + "平台已禁用"提示 | AC-6 |
| P5-FE-06 | 超上限提示 | inject_max 输入 999：保存前提示截断为平台上限 | AC-7 |
| P5-FE-07 | viewer 只读 | 全部控件禁用，无保存按钮 | AC-1 |

## 7. 回归范围

- Phase 1-4 记忆全量测试套件（`backend/tests/test_memory_*.py` 18 个文件）原样通过；
- 评估模块快照与回放（evaluation）不受影响；
- example agent 抽取排除逻辑不变。

## 8. 缺陷分级与出口准则

| 级别 | 定义 | 处理 |
|---|---|---|
| P0 阻断 | 存量 Agent 行为变化（默认值不等价）；全局关闭被 Agent 绕过；平台级能力可从 Agent 配置关闭 | 修复并回归后方可继续 |
| P1 严重 | review 模式漏路由（高置信直接 active）；快照时序错（按当前配置而非入队快照）；数值截断失效 | 修复前不得进入验收 |
| P2 一般 | 表单联动错误；预设值错；提示文案缺失 | 记录后可带问题验收，需产品签字 |
| P3 轻微 | 文案、样式问题 | 排期修复 |

**出口准则**：全部用例通过；AC-1~AC-11 复现通过；**存量等价、"只能收紧"为阻断项**；Phase 1-4 回归套件全绿；无 P0/P1 未关闭。
