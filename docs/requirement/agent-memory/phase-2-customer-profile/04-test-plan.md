# Phase 2 · 客户实体 + 画像/偏好记忆 — 测试方案

> 状态：待实施（本阶段开发完成后执行）
> 创建：2026-10-10
> 依据：`02-requirements.md`（FR-1~FR-6、AC-1~AC-8）、`03-technical-design.md`（迁移 `0031`、身份解析、抽取流水线、控制台 API）

## 1. 测试范围与原则

**范围**：客户实体与身份绑定（`memory_customers` / `memory_identities`）、画像/偏好记忆的自动抽取/存储/注入（`memory_items`）、控制台记忆管理（API + 前端）、工单 `customer_id` 联动、Agent 级读写开关。

**原则**：

1. **保守识别必测**：宁可不识别不可错识别——渠道/开放平台身份严格按规则解析，任何异常都落"不识别 + warning"，绝不错绑。
2. **防污染必测**：playground、示例 Agent（`is_example`）会话零身份解析、零抽取、零记忆写入，有常驻自动化断言。
3. **开关兜底必测**：`memory_extract_enabled=false` / Agent 级 `memory_write=false` / `memory_enabled=false` 后行为与 Phase 1 一致。
4. **抽取容错必测**：LLM 输出不可信——非法 JSON 整批丢弃、低置信丢弃，宁缺毋滥。
5. **时间类用例不等待**：抽取去抖通过直接改 `created_at` 构造。

## 2. 测试环境与数据构造

| 环境 | 用途 |
|---|---|
| 单测 | 身份解析规则、confidence 路由、upsert 合并、注入渲染 |
| 集成（PG 17 + pytest postgres fixture） | 抽取链路、身份解析接入点、工单联动、API 端点 |
| mock/demo 模型 | 全部自动化用例；抽取输出可编程（合法 JSON、非法 JSON、部分非法、各种 confidence） |

**数据构造约定**：会话/消息直接写库构造；抽取任务投递后测试内手动执行一次 claim+execute 断言终态；渠道会话的 `external_id` 与身份解析的 `external_key` 必须用同一哈希公共函数生成（测试同时固化这一点）。

## 3. 单元测试（service / extraction）

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P2-UT-01 | `resolve_customer` 未命中 | 同事务创建 customer + identity，返回新 id | FR-1 |
| P2-UT-02 | `resolve_customer` 命中 | 返回已有 customer，不新建 | AC-2 |
| P2-UT-03 | `resolve_customer` 并发唯一冲突 | 捕获 UNIQUE 冲突后重查，返回已有 customer（幂等） | 技术设计 §1 |
| P2-UT-04 | 三种来源 external_key 规则 | channel=`sha256(session_id)`（与会话 external_id 同源，公共函数两处一致）；open_platform=`{app_id}:{external_user_id}`；enterprise=`enterprise:{user_id}` | FR-1 |
| P2-UT-05 | 抽取 JSON 解析：非法 JSON | 整批丢弃，记 warning，无部分写入 | 技术设计 §2 |
| P2-UT-06 | 抽取 JSON 解析：单条缺字段/类型非法 | 按实现决策跳过该条或整批丢弃（验证并固化） | 技术设计 §2 |
| P2-UT-07 | confidence 路由 | ≥0.8→active；0.5~0.8→pending；<0.5→丢弃；边界值 0.8 / 0.5 各测一次 | FR-2 |
| P2-UT-08 | 同键 upsert | 同 `(customer_id,type,key)` update：content 覆盖、`prev_content`=旧值、revision+1、`last_referenced_at` 顺手刷新 | AC-7 |
| P2-UT-09 | op=add 撞已有键 | `ON CONFLICT DO NOTHING`，不产生重复行（UNIQUE 约束兜底） | 技术设计 §2 |
| P2-UT-10 | 受控 key 词表与 `custom_*` 扩展 | 词表内 key 正常；`custom_*` 允许；非法 key 拒绝 | 技术设计 §2 |
| P2-UT-11 | `active_items` 过滤与裁剪 | 仅 active；>20 条按 `updated_at` 倒序取前 20；超 1000 tokens 同样裁剪 | FR-3 |
| P2-UT-12 | 注入渲染分节 | `【客户画像】`/`【客户偏好】` 分节格式正确；空分组不渲染 | FR-3 |

## 4. 集成测试

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P2-IT-01 | 渠道会话报姓名/电话/"用英文回复" → 结束 → 同 session_id 再进线 | 解析到同一 customer；条目生成；新会话消息序列含画像与偏好注入（mock 模型断言被注入内容） | AC-1/AC-2 |
| P2-IT-02 | 开放平台会话（app_id + external_user_id） | 按规则解析建档；与渠道身份互不串扰（不同 customer） | FR-1 |
| P2-IT-03 | 身份解析异常（DB 瞬断注入） | 会话创建不阻断，`customer_id` 置空，warning 日志 | FR-1 |
| P2-IT-04 | playground 会话 / 示例 Agent 会话 | 不解析身份、不产生抽取任务、零记忆写入 | AC-5/FR-2 |
| P2-IT-05 | 抽取去抖 | 同一会话 5 分钟内多个 run 完成仅 1 个抽取任务；5 分钟后（改库构造）可再次投递 | FR-2 |
| P2-IT-06 | 改口场景 | "我叫张三" → "叫我张工就行"：同键 update，revision=2，`prev_content` 正确，不堆叠两条 | AC-7 |
| P2-IT-07 | confidence=0.6 条目 | status=pending；注入不出现；控制台确认后注入出现 | AC-3 |
| P2-IT-08 | 注入位置 | 摘要消息之后、历史消息之前；与 Phase 1 摘要同时存在时顺序正确 | FR-3 |
| P2-IT-09 | Agent 级开关分离 | `memory_write=false`：不产生抽取任务；`memory_enabled=false`：不注入但抽取照常 | AC-8 |
| P2-IT-10 | 全局 `memory_extract_enabled=false` | 无任何抽取任务投递 | 配置 |
| P2-IT-11 | 工单联动 | 绑定客户的会话 propose → payload 含 customer_id → approve → `tickets.customer_id` 非空（str(UUID)）；详情 API 返回该字段 | AC-6/FR-5 |
| P2-IT-12 | 无客户会话的工单 | `tickets.customer_id` 为空，流程不受影响 | FR-5 |
| P2-IT-13 | 存量会话兼容 | 迁移前创建的会话（customer_id=NULL）正常对话、不解析、不报错 | 上线与回滚 |

## 5. API 测试（记忆管理端点）

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P2-API-01 | 8 个端点鉴权矩阵 | 未认证 401；viewer 对写操作（PATCH/DELETE/confirm/reject/restore）403；operator 全部可用 | FR-4 |
| P2-API-02 | 客户列表搜索/分页 | `q=` 按 external_key 匹配；分页正确；展示记忆条数与最近活跃时间 | FR-4 |
| P2-API-03 | 编辑条目 | content 更新、`source_type='human'`、confidence=1.0、`prev_content` 保留编辑前值 | FR-4 |
| P2-API-04 | 软删除与恢复 | DELETE → status=deleted，注入消失；restore → active，注入恢复 | FR-4 |
| P2-API-05 | pending 队列 | confirm → active；reject → deleted；列表仅含 pending | FR-4 |
| P2-API-06 | 租户隔离 | 跨租户访问 customer/item id 返回 404（不泄露存在性）；service 层所有查询带 `tenant_id` | 非功能 |

## 6. 前端测试（记忆管理页）

客户列表/详情/pending 队列渲染与交互（编辑、删除、确认、拒绝）；`source_ref` 跳转来源会话；工单详情跳转客户记忆链接。关键交互（编辑保存、软删除、pending 确认）建议组件级自动化，其余走验收阶段手工检查清单。

## 7. 通用核对项（本阶段适用）

| 编号 | 核对项 | 通过标准 |
|---|---|---|
| P2-CM-01 | 迁移 `0031` 在线性 | 存量库执行成功；纯增量 |
| P2-CM-02 | 配置加载 | `memory_extract_*`、`memory_confidence_*`、`memory_inject_max_items` 默认值与覆盖生效 |
| P2-CM-03 | 审计 | 抽取落库、人工编辑、删除/恢复、确认/拒绝均留审计；payload 无敏感信息 |
| P2-CM-04 | 模型用量 | 抽取调用落 `gateway_calls`，用量可归因到租户/Agent |

## 8. 回归范围

- playground / 示例 Agent 零写入断言常驻；
- 现有工单、渠道、开放平台、conversation 测试全绿；
- Phase 1 摘要链路不受影响（注入顺序共存用例 P2-IT-08 覆盖）。

## 9. 缺陷分级与出口准则

| 级别 | 定义 | 处理 |
|---|---|---|
| P0 阻断 | 跨租户记忆泄露；身份错绑（A 的记忆给 B）；对话主流程破坏 | 修复并回归后方可继续 |
| P1 严重 | 抽取误写（幻觉入库为 active）；人工编辑被覆盖；防污染失效 | 修复前不得进入验收 |
| P2 一般 | 单条记忆质量欠佳；注入格式/顺序偏差；控制台体验问题 | 记录后可带问题验收，需产品签字 |
| P3 轻微 | 文案、日志问题 | 排期修复 |

**出口准则**：全部用例通过；AC-1~AC-8 复现通过；防污染与租户隔离断言常驻；无 P0/P1 未关闭。
