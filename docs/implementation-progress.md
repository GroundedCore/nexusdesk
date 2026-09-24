# 模块逐个实施记录

本轮已为 12 个模块交付或回归首轮实现。**不表示各 README 的全部 P0 已验收**；第 1 节记录代码现状，其余章节保留目标规划。业务开发库未启动、未迁移，验证使用专用 PostgreSQL 测试库。

| 模块 | 本轮交付 | 后续重点 |
|---|---|---|
| policy | 上下文、Decision、默认拒绝 | 动态策略持久化 |
| tool_gateway | 发布快照、Schema、调用诊断 | 其他适配器、通用写账本 |
| agent_config | 依赖版本、归档、搜索、差异 | 审批与灰度 |
| agent_runtime | 版本与调用事件集成回归 | 流式输出、压测 |
| conversation | 关闭/重开、状态版本、归属校验 | 完整筛选与游标 |
| customer_service | 会话内工单查询、确认流程回归 | 外部业务适配 |
| human_handoff | 员工凭证、本人权限、状态版本 | SSO、排班与 SLA |
| channel | HMAC、轮换、防重复 | 具体渠道与出站回执 |
| knowledge | 任务、三种分片、Parser/S3/Milvus 适配 | 全格式/真实服务验收、大文档、混合召回 |
| evaluation | Judge、相似度、硬规则分层 | 人工复核持久化及校准 |
| observability | 调用关联、网关计量 | 游标、告警、指标导出 |
| model_gateway | 新消费者集成及回归 | 真实供应商完整验收 |

验证：82 项后端测试（81 项全量回归与新增 1 项 Judge 契约测试）、2 条浏览器业务流程、TypeScript 检查与生产构建通过。详见 [实施说明](modules-delivery.md)。

## 2026-09-21：新增平台设置规划

新增 [platform_settings](../backend/src/agent_platform/modules/platform_settings/README.md) 为第 13 个模块，当前仅完成方案及索引。尚未实现服务、数据库迁移、API、前端或模块开关；此前测试结果不覆盖本模块。按最新范围，方案只保留平台／租户／Agent 配置层级与继承规则、工单模式及停用流程。已移除其他配置功能、通用发布中心及扩展阶段，不再作为本模块待办。

## 2026-09-21：通用工单设计 v1

已确定 [混合存储设计](../backend/src/agent_platform/modules/customer_service/TICKETING.md)：3 张业务表＋2 张配置表，沿用 pending_actions。预置字段按类型选择，JSONB 保存业务详情，固定列承载通用查询；已规划版本、权限、事务和兼容迁移。此次仅更新方案，未修改代码、启动服务或执行数据库迁移。

## 2026-09-21：补齐迁移

新增 0008_ticketing 和 0009_ticket_settings，覆盖新版工单扩展表、一次性回填及最小平台/租户工单设置。工单现有读写和状态页面已切换新结构；类型设计器和平台设置服务仍待实现。Agent 层沿用现有配置存储。按开发阶段删除旧列并移除兼容触发器。执行及回滚边界见 [数据库迁移说明](database-migrations.md)。开发数据库未升级。

验证结果：84 项完整后端回归通过，另新增 1 项新工单生命周期测试通过；2 条 Playwright 流程通过，TypeScript/Vite 构建及 Ruff 检查通过。迁移测试覆盖空库和有原始工单数据的升级/回退/再升级。开发数据库未迁移。

## Ant Design 首轮改造

接入 Ant Design 6，统一中文主题及分组布局，工单中心改为筛选表格与详情抽屉，按需加载。原聊天和其他模块暂保留自定义组件；完整时间线仍待后端查询接口。方案与验证见 [前端改造说明](frontend-antd.md)。
