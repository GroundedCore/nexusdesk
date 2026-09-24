# 数据库迁移说明

当前单一 Alembic 链：0001_runtime → 0002_platform → 0003_model_gateway → 0004_tool_versions → 0005_service_identity → 0006_knowledge_jobs → 0007_vector_index → 0008_ticketing → 0009_ticket_settings。

## 新增 0008_ticketing

- 新建 ticket_types、ticket_type_versions、tickets_detail、ticket_process_log。
- tickets 增加工单编号、类型版本、来源 Run、客户、渠道、优先级、负责人、创建主体及处理时间；描述一次性转存至详情，备注导入过程记录后删除旧 description/note 列。
- 建立租户联合外键、状态/优先级约束和列表/处理记录索引。会话删除使用 RESTRICT，连同 action_id 引用保护工单不被级联删除。
- 为已有工单按租户创建通用类型 v1，编号回填为 TK- 加 UUID 去连字符；详情保留原描述，原备注导入 legacy_note。不会伪造完整历史、解决时间和来源 Run。
- 不创建旧代码兼容触发器、不保留双写。现有工单服务同步切换为主表＋详情＋过程记录的事务写入；列表及详情通过查询组合返回描述和最近备注。
- 工单创建使用租户通用类型 v1，记录真实确认主体。类型设计器、自定义字段编辑/Schema 权限校验仍未实现，当前流程创建的 custom_fields 为空对象。
- 状态接口及页面支持 waiting_customer；解决/关闭要求处理说明，解决说明存入详情，处理记录保留状态变化和备注。关闭后的业务修改被拒绝。

开发阶段采用直接切换，新后端必须配套升级到最新迁移，不支持旧后端运行在新表结构上。

数据回填在 Alembic 事务内完成，并创建普通索引；不是无锁在线迁移。大数据环境先测量回填时间、锁等待和空间占用，安排维护窗口或另行设计分批扩展迁移。

## 新增 0009_ticket_settings

只为既定的两项范围准备持久层，不建立通用配置发布中心：

| 表 | 内容 |
|---|---|
| platform_ticket_policy | 单行平台能力边界：available、allowed_modes、revision |
| tenant_ticket_settings | 租户模式、enabled/draining/disabled/emergency_disabled 状态、连接引用、之前模式及修订号 |

迁移默认平台允许 internal，已知租户初始化为 internal/enabled。draining 和 emergency_disabled 保留原模式与连接，正常 disabled 则清空当前连接，通过 previous_mode/previous_integration_id 保留恢复信息。

Agent 层沿用 agents.draft / agent_versions.config，不再建立重复的 Agent 配置表；后续由业务服务解析继承。新租户的设置需由后续初始化服务创建，不能把当前一次性回填当成租户生命周期实现。

integration_id 仅为预留 UUID 引用，目前无已实现的外部工单连接目录，因此没有虚构外键。外部适配就绪后由领域服务校验租户、连接和能力。数据库检查形状一致性，不代表模式切换、排空、授权和实时阻断已经生效。现有 API 尚未消费这些设置，请勿通过手工改表期待停用功能生效。

## 执行

在 backend 目录、确认 AGENT_DATABASE_URL 指向目标环境后执行：

```powershell
uv run alembic heads
uv run alembic current
uv run alembic upgrade head
```

已有部署使用自己的配置，切勿复制测试数据库地址。本次未升级开发数据库。

## 回滚

空库支持降到 base 后重升。仅含迁移前数据时，可回退到 0007 并从详情和导入记录恢复 description/note；新增表和类型元数据被删除，回滚前仍需备份。

如果存在非空 custom_fields、解决说明、新工单属性、非默认类型/设置或新业务过程记录，迁移拒绝直接回退，分别返回 ticketing_downgrade_requires_data_export / ticket_settings_downgrade_requires_data_export。必须先完成数据导出及明确的兼容转换；不能用 stamp 假装回滚成功。空库循环测试不等于生产回滚无损承诺。

## 验证

新增 tests/test_ticket_migrations.py，使用 MIGRATION_TEST_DATABASE_URL 连接专用测试 PostgreSQL，并创建随机 migration_test_* 数据库，结束后删除该随机库；测试角色需要 CREATEDB。

覆盖空库完整升级/回退/再升级、旧数据回填、新表无旧列/兼容触发器、租户外键、JSONB 类型及优先级约束、删除保护和有数据回滚拒绝。常规业务回归使用 TEST_DATABASE_URL。测试数据库与开发数据库隔离。

验证结果：84 项完整后端回归通过，另新增 1 项新工单生命周期测试通过；2 条 Playwright 流程通过，TypeScript/Vite 构建及 Ruff 检查通过。迁移测试覆盖空库和有原始工单数据的升级/回退/再升级。开发数据库未迁移。

## 0011 Agent 工作台

`0011_agent_workspace` 在 `runtime_conversations` 增加 `source TEXT NOT NULL DEFAULT 'business'`，约束为 business/playground；新增租户/Agent/来源/更新时间索引及 Agent 目录排序索引。既有会话不删除或重建。部署本版前执行 `uv run alembic upgrade head`，再启动后端。降级移除来源字段和新增索引，因此来源分类不会保留；迁移回归在临时数据库验证旧数据保留、来源约束、降级与再升级。


## 0012 已归档智能体永久删除

新增 `runtime_conversations.deleted_agent` JSONB，用于保存原智能体 ID、名称与发布版本。数据库约束要求带有该历史标记的会话解除 Agent 外键关联并保持 closed，应用层禁止重新打开。没有修改已有消息、运行和工单表，也没有级联删除历史记录。

已有历史删除标记时降级报 `agent_deletion_downgrade_requires_history_export`，避免去掉字段后旧会话丢失归属并可被误用。空数据降级/再升级与有历史数据阻止降级均有迁移测试。


## 0013_agent_classification

Agent 增加 industry、tags（文本数组）、is_example 字段，以及行业和标签索引。迁移依据 industry-cases-v1 的 agent.created 审计记录回填案例行业与用途，不根据名称猜测。其余 Agent 默认未分类、非案例。降级移除这些分类字段及索引，保留 Agent 与知识数据。
