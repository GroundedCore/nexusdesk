# 前端导航

使用原生 History API 和 Hash 路由（`src/app/hashRouter.ts`），无需服务器为子路径配置 HTML 回退。

- `#/overview`：平台总览；其他一级模块使用 `#/<模块标识>`。
- `#/agents`：Agent 列表。
- `#/agents/new/config`：新建草稿；首次保存后替换为正式 ID 的路由。
- `#/agents/:id/config`：Agent 配置，直接访问时从后台加载已保存草稿。
- `#/agents/:id/conversations`：Agent 对话记录。
- `#/models/models`、`#/models/connections`、`#/models/profiles`：模型广场、渠道和配置方案。
- 模型网关其余子页面使用 `#/models/<section>`，包括 playground、logs、monitor、access_keys、sensitive_words、alert_rules、quota。

刷新、复制链接、前进和后退均根据 URL 恢复页面。未知路径回到 `#/overview`；Agent 不存在或加载失败时保留地址并提供重试/返回列表。

离开含未保存修改的 Agent 工作台需要确认，包括浏览器前进/后退；同一 Agent 的配置与对话记录切换保留编辑状态。刷新恢复的是后台已保存内容，不把未保存表单、聊天输入、列表筛选或抽屉状态写入 URL。

验证：`npm run build`；`tests/hash-routing.spec.ts` 覆盖子页面刷新、浏览器历史、取消离开、草稿保存后的地址替换和无效链接处理。
