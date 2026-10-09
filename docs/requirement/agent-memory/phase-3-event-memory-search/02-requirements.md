# Phase 3 · 事件记忆 + 语义检索 — 需求文档

## 用户故事

**US-1（终端客户）**：我上周报修的问题工单已解决，这周来咨询相关问题时，客服记得上次的处理结论，不给我矛盾的方案。

**US-2（终端客户）**：客服承诺过"48 小时内退款"，我三天后来问进度时，客服知道这个承诺并能告诉我当前状态。

**US-3（人工坐席）**：我批准工单时填写的解决方案摘要，会自动成为客户的记忆，不需要我二次录入。

**US-4（租户管理员）**：我可以配置事件记忆的保留时长，过期事件自动清理，控制数据规模。

## 功能需求

### FR-1 工单事件自动写入

| 工单事件 | 记忆内容 | source_ref |
|---|---|---|
| 创建（decide approve） | `工单 {ticket_no} 已创建：{description 摘要}` | ticket_id |
| 解决（→resolved） | `工单 {ticket_no} 已解决：{resolution 摘要}` | ticket_id |
| 关闭（→closed） | `工单 {ticket_no} 已关闭：{关闭说明}` | ticket_id |
| 承诺备注 | 坐席在工单回复/备注中标记为"承诺"的内容（process_log 的 remark/reply 增加 `is_commitment` 标记入口） | ticket_id + log_id |

- 写入条件：工单关联的会话有 `customer_id`；无客户绑定的工单不产生记忆；
- `source_type='ticket'`，`confidence=1.0`，`status='active'`；
- 状态联动：工单后续状态变化时，对应的"未解决"类记忆需更新（如 in_progress 记忆在 resolved 后被解决记忆取代，见 FR-3）。

### FR-2 memory_search 工具

| 项 | 说明 |
|---|---|
| 工具名 | `memory_search` |
| 参数 | `query: string`，`limit: int（默认 5，上限 10）` |
| 检索范围 | 当前会话客户的 `active` 事件记忆（profile/preference 不走检索，直接注入） |
| 排序 | `score = 相关度 × 时间衰减因子`，近期事件在相关度相近时优先（客服场景"越近越相关"）；衰减基于 `last_referenced_at`，半衰期可配（事件默认 30 天） |
| 返回 | `[{content, type, updated_at}]`，按 score 排序；无结果时返回明确空提示 |
| 权限 | 仅当会话绑定了 customer 且 Agent 开启 `memory_enabled` 时可用；否则工具不出现在工具列表 |
| 降级 | 无 pgvector 环境自动切换关键词检索（PG TSVECTOR），对 Agent 透明 |

### FR-3 常驻注入（高优事件）

- 注入内容：该客户 `active` 且 `key` 属于高优类的事件条目——`open_ticket`（在途工单）、`commitment_pending`（未兑现承诺）；
- 上限 5 条，按 updated_at 倒序；格式：`【待跟进事项】- 工单 TK-008 处理中：物流异常…`；
- 工单 resolved/closed 时对应条目 `status` 更新为 `expired`（而非删除，保留审计），注入自动消失；
- 承诺兑现（坐席标记或工单关闭）后同理过期。

### FR-4 事件记忆 TTL

| 项 | 说明 |
|---|---|
| 默认保留期 | 180 天（`memory_event_ttl_days` 可配） |
| 过期方式 | 每日清理任务将 `expires_at < now()` 的条目置 `expired`（软过期，不物理删除） |
| 例外 | `commitment_pending` 类条目不过期，直到被人工/流程标记兑现 |

### FR-5 配置

| 配置项 | 默认 | 说明 |
|---|---|---|
| `memory_event_ttl_days` | `180` | 事件记忆保留天数 |
| `memory_search_limit_max` | `10` | 工具返回上限 |
| `memory_vector_enabled` | `auto` | `auto`（探测 pgvector）/ `true` / `false`（强制关键词模式） |
| AgentConfig `tool_names` | — | `memory_search` 作为内置工具，默认加入，可在 Agent 草稿中移除 |

## 非功能需求

- 事件写入走 `memory_tasks` 异步任务（kind=`event_write`），工单状态接口延迟无感；
- embedding 生成失败时降级：条目正常写入（无可检索向量），标记 `embedding_failed`，可由清理任务重试；
- 检索 P99 ≤ 300ms（pgvector，单客户条目量级）。

## 验收标准

| # | 场景 | 预期 |
|---|---|---|
| AC-1 | 客户会话创建工单并 resolved，隔日新会话问"上次的问题处理得怎样" | Agent 引用解决结论回答 |
| AC-2 | 坐席在工单备注标记承诺"48 小时退款"，客户 3 天后追问 | Agent 知道承诺存在及当前工单状态 |
| AC-3 | 客户有在途工单时进线 | 注入含"待跟进事项"，Agent 主动提及工单进度 |
| AC-4 | 无 pgvector 环境执行 AC-1 | 关键词检索可用，结果质量可接受 |
| AC-4b | 两条相关度相近的事件（一条 3 天前、一条 150 天前） | 检索结果中 3 天前的排在前面 |
| AC-5 | 事件条目超过 TTL | 清理任务置 expired，检索与注入均不出现 |
| AC-6 | 工单 resolved 后 | 对应"在途工单"条目 expired，注入的待跟进事项消失 |
| AC-7 | Agent 草稿移除 `memory_search` 工具 | 工具不出现在该 Agent 运行时的工具列表 |
