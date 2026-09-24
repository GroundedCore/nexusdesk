# Agent 管理工作台

列表使用服务端分页卡片，支持名称搜索、发布状态筛选、归档恢复；列表切换到配置工作台时保留筛选与页码。配置工作台由顶部保存/发布操作、左侧分组导航、中间配置表单与右侧试聊组成，桌面支持拖动分隔栏，窄屏切换配置/试聊标签。

- `AgentsPage.tsx`：列表、草稿编辑、工具/知识库选择、版本历史与字段差异。发布先保存未保存修改；保存冲突不继续发布；离开模块和关闭浏览器时保护未保存草稿。
- `Conversations.tsx`：已发布版本试聊、当前 Agent 对话记录、来源筛选、历史消息游标加载。配置与对话记录切换时保留未保存表单和试聊会话。
- `agents.css`：模块基础布局。`workspace.css`：独立配置工作台的视觉细节与响应式覆盖；包含紧凑操作栏、滚动分组高亮、表单层级、试聊空态与输入框。

试聊通过实际会话和 Runtime 执行，可能调用真实模型与工具，使用发布版本，不支持运行未发布草稿。试聊会话显式标记 `source=playground`，业务会话默认 `business`。每条新消息按发送时的当前发布版本执行；发布/回滚不会修改已提交运行的快照。

## 接口

- `GET /api/v1/agents/catalog?q=&status=all&include_archived=false&page=1&page_size=12` 返回 `{items,total,page,page_size}`，status 为 all/published/unpublished。
- `GET /api/v1/agents/{id}` 返回单个 Agent；原 `/agents` 列表接口保持兼容。
- `GET /api/v1/agents/{id}/conversations?source=all&q=&page=1&page_size=12`，来源 all/business/playground。
- `POST /api/v1/conversations` 增加可选 source；创建试聊要求 Agent 已发布且未归档。
- `GET /api/v1/conversations/{id}/history?before=<seq>&limit=50` 返回时间正序的 `{items,has_more}`，用于向前加载消息。

配置写入需要 admin，试聊需要 operator/admin，对话读取需要 Reader 并校验租户；页面遵循相同权限。

## 数据库与验证

启动新版后端前执行 `cd backend` 后 `uv run alembic upgrade head`，应用增量迁移 `0011_agent_workspace`。历史会话默认归为 business，新增来源校验及查询索引。

- 后端：`tests/test_agent_workspace.py`、`tests/test_agent_workspace_migrations.py`。
- 前端：`tests/agents-workspace.spec.ts` 使用模拟接口验证冲突阻断、草稿保留、权限与移动布局；`tests/agents-fullstack.spec.ts` 使用实际后端验证创建发布、试聊、记录、版本与归档恢复。


## 永久删除已归档智能体

管理员在「显示已归档 → 卡片更多 → 永久删除」中确认后，通过 `DELETE /api/v1/agents/{id}?revision=<draft_revision>` 删除配置及发布版本。校验归档状态、租户、当前修订；渠道引用（包含停用渠道）、评测用例/报告引用、排队或运行中任务、未完成的人工协同、未过期待确认操作均会阻止删除，并展示具体原因。

历史会话保留 `deleted_agent` 中的原 ID/名称/发布版本，解除当前 Agent 关联并转为只读。消息、运行快照、事件、工单与审计不删除；可在会话工作台查看。被删除智能体无法恢复，历史会话也不能重新打开，避免误用默认模型继续运行。

迁移 `0012_agent_deletion` 为增量迁移，部署前执行 `uv run alembic upgrade head`。存在已删除 Agent 的历史会话时，降级会阻止丢失归属信息。测试：`backend/tests/test_agent_deletion.py`、`backend/tests/test_agent_deletion_migrations.py`、`frontend/tests/agents-delete.spec.ts`。


## 模型方案必选

Agent 不再回退到部署默认模型。允许未绑定模型的草稿保存；发布、回滚和运行必须绑定可用的已发布 Chat 模型方案及版本，否则返回 `agent_model_required`（HTTP 409）。旧版未绑定 Agent 需选择方案并重新发布，运行快照缺少模型绑定时同样阻断。

配置页面仅提供已发布且启用的 Chat 方案；空状态引导前往模型网关的配置方案页，支持刷新选项。跳转遵循未保存修改保护。

验证：`backend/tests/test_agent_model_required.py`、`backend/tests/test_model_gateway.py`、`frontend/tests/agent-model-required.spec.ts`。


## 分类与快速筛选

列表新增行业、用途标签和仅看案例。同行业/用途组内 OR、组间 AND，与名称、发布状态、归档和分页共同在后端查询。已选标签可移除或清空，重置按钮清空所有筛选。Hash 查询参数使用 industry/tag 重复键及 q/status/archived/examples/page/size，刷新、浏览器历史和工作台返回均保留。

配置页允许管理员编辑行业、用途标签和案例标记，遵循原草稿冲突及未保存保护；分类属于目录元数据，不更改已发布运行配置。旧调用方更新 Agent 时省略分类字段会保留原值，显式空值可清空。

接口 GET /agents/catalog 新增 industry、tag 数组与 examples_only 布尔参数；创建/更新 Agent 新增 industry、tags、is_example。使用前执行迁移 0013_agent_classification。
