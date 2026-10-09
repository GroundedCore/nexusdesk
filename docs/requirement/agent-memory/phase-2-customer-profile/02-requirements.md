# Phase 2 · 客户实体 + 画像/偏好记忆 — 需求文档

## 用户故事

**US-1（终端客户）**：作为回头客，我希望客服记得我的姓名、产品型号和之前的约定，开口不用重新自我介绍。

**US-2（终端客户）**：作为有语言偏好的客户，我说过"用英文回复我"之后，希望后续所有会话都遵循，不用每次重申。

**US-3（人工坐席）**：作为坐席，我希望在会话详情页看到当前客户的画像与偏好，辅助我快速理解服务对象。

**US-4（运营管理员）**：作为运营，我希望能在控制台审查自动抽取的记忆，纠正错误（比如 Agent 记错了客户的姓氏），删除不当内容。

**US-5（工单处理人）**：作为处理工单的同事，我希望工单上自动带有客户标识，可以反查该客户的会话与记忆。

## 功能需求

### FR-1 客户身份解析

| 来源 | 解析规则 | 自动绑定 |
|---|---|---|
| 渠道 webhook | `external_key = sha256(session_id)`（与现有会话 external_id 生成逻辑同源），按 `(tenant_id, 'channel', external_key)` 查 identity，不存在则创建 customer + identity | ✅ 自动 |
| 开放平台应用 API | `external_key = "{app_id}:{external_user_id}"`，按 `(tenant_id, 'open_platform', external_key)` 解析 | ✅ 自动 |
| 企业嵌入 | `external_key = "enterprise:{user_id}"`（沿用 open_platform 现有约定） | ✅ 自动 |
| playground 会话 | 不解析、不建档 | — |

- 解析时机：会话创建时（`ConversationService.create` / 渠道 `send` / open_platform 会话复用路径），在 `runtime_conversations` 增加 `customer_id` 列承载结果；
- 解析失败（异常）不阻断会话创建，`customer_id` 置空，记 warning。

### FR-2 记忆自动抽取

| 项 | 说明 |
|---|---|
| 触发 | run 成功完成且会话已绑定 customer；按会话去抖（同一会话 5 分钟内最多一次抽取任务） |
| 输入 | 最近 N 条消息（默认 20）+ 该客户已有记忆的 (type, key) 清单 |
| 输出 | 结构化 JSON：`[{op: add|update|noop, type: profile|preference, key, content, confidence}]` |
| 生效规则 | `confidence ≥ 0.8` → `active`；`0.5 ≤ confidence < 0.8` → `pending`（待人工确认）；`< 0.5` 丢弃 |
| 冲突合并 | 同 `(customer_id, type, key)` 已存在：`update` 覆盖 content，`revision + 1`，旧值写入历史（JSONB 字段 `history` 或独立历史表，见技术文档决策） |
| 抽取类别示例 | profile：`name` / `phone` / `email` / `account_id` / `product_model` / `company`；preference：`language` / `style` / `address_name`（称呼）/ `contact_channel` |
| 防污染 | playground、示例 Agent、`source='playground'` 会话不产生抽取任务 |

### FR-3 记忆注入

| 项 | 说明 |
|---|---|
| 注入内容 | 该客户 `status='active'` 的 profile + preference 条目 |
| 注入格式 | system 消息，固定模板分节：`【客户画像】key: content…` / `【客户偏好】key: content…` |
| 上限 | 最多 20 条或 1000 tokens，超出按 `updated_at` 倒序取舍（最近更新的优先） |
| 开关 | AgentConfig `memory_enabled`（默认 true）与 `memory_write`（默认 true）分别控制读与写 |
| 位置 | 在摘要消息之后、历史消息之前（与 Phase 1 注入点相邻） |

### FR-4 控制台记忆管理

| 功能 | 说明 |
|---|---|
| 客户列表 | 按租户分页，支持按 external_key 搜索；展示记忆条数、最近活跃时间 |
| 客户详情 | 身份绑定列表 + 记忆条目列表（按 type 分组），每条显示 content、confidence、status、source_ref（可跳转来源会话）、updated_at |
| 编辑 | 修改 content（`source_type` 转为 `human`，confidence 置 1.0） |
| 删除 | 软删除（`status='deleted'`），可恢复 |
| 待确认队列 | `pending` 条目列表，一键确认（→active）或拒绝（→deleted） |
| 权限 | 沿用现有 staff 角色：operator 可编辑，viewer 只读 |

### FR-5 工单联动

- Agent 工具 `propose_ticket` 生成提案时，若会话已绑定 customer，提案 payload 携带 `customer_id`；
- 坐席批准创建工单时写入 `tickets.customer_id`（存 customer UUID 字符串，兼容现有 VARCHAR(100) 字段，不改表）；
- 工单详情页可按 customer_id 反查会话列表与记忆（只读链接到记忆管理页）。

### FR-6 Agent 配置

`AgentConfig` 新增（随草稿/发布链路）：

| 字段 | 默认 | 说明 |
|---|---|---|
| `memory_enabled` | `true` | 对话时是否注入该客户的记忆 |
| `memory_write` | `true` | 该 Agent 的对话是否参与记忆抽取 |

## 非功能需求

- 抽取全程异步，对 run 延迟零影响；
- 身份解析与会话创建同事务，不引入最终一致性问题；
- 所有 memory 表查询强制 `tenant_id` 过滤（沿用服务层现有模式）。

## 验收标准

| # | 场景 | 预期 |
|---|---|---|
| AC-1 | 渠道会话中客户说"我叫张三，电话 138****，以后用英文回复"，结束会话后再次进线 | Agent 用英文问候并能称呼"张三" |
| AC-2 | 同一 `session_id` 多次进线 | 解析到同一 customer，记忆持续累积 |
| AC-3 | 抽取输出 confidence=0.6 的条目 | 进入 pending 队列，注入时不出现；控制台确认后出现 |
| AC-4 | 控制台编辑某条记忆内容 | 下一轮对话注入编辑后的内容；条目显示人工来源 |
| AC-5 | playground 会话提供个人信息 | 不产生任何记忆条目 |
| AC-6 | 从已绑定客户的会话创建工单 | `tickets.customer_id` 已填充，工单详情可跳转客户记忆 |
| AC-7 | 客户先说"我叫张三"，后说"其实叫我张工就行" | 称呼条目 update 而非堆叠两条，revision 递增 |
| AC-8 | Agent 关闭 `memory_write` | 对话不产生抽取；`memory_enabled` 关闭则不注入 |
