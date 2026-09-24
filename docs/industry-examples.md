# 行业案例初始化

显式命令创建 9 行业、18 个 Agent 草稿、9 个行业知识库和 36 篇可检索文档。行业为电商零售、企业软件、制造业、教育培训、酒店文旅、房产物业、物流运输、企业人力资源和游戏。内容在 `backend/src/agent_platform/apps/industry_cases.json`。

## 执行

使用已迁移数据库，设置 `AGENT_DATABASE_URL` 和目标 `AGENT_TENANT_ID` 后，在 backend 目录运行：

```powershell
.venv/Scripts/python.exe -m agent_platform.apps.seed_industries --report industry-cases-report.json
```

可显式附加 `--profile-id <已发布Chat方案UUID> --profile-version <版本>`。必须同时提供，方案需支持工具调用。未指定时模型绑定留空；指定时只绑定新创建的草稿，也不会自动发布或调用模型。已有 Agent 不会被重新绑定。操作人应在配置工作台检查并发布。

## 内容与行为

- 每行业两个职责不同的 Agent，共用该行业知识库；咨询型绑定 `knowledge_search`，服务型另外绑定 `propose_ticket`。
- 每行业三篇业务资料和一篇试聊验收指南。订单、设备、预订和充值等都是文档中的虚构快照；未接入实时业务工具。
- 不伪造对话消息、运行日志、模型调用费用、实际工单或人工接管记录。需要演示运行流程时，在配置并发布模型和 Agent 后手动试聊。
- 全部资源名称带 `[案例]`；Agent 描述记录行业、场景和批次 `industry-cases-v1`，审计记录还包含初始化执行 ID。

## 重复执行与恢复

整个批次使用一个数据库事务和租户级事务锁：失败时整体回滚，同租户并发执行串行处理。Agent 和知识库使用包含租户、批次与业务键的稳定 UUID；文档使用同事务审计标记识别。重复执行保留原 ID、名称、人工编辑、归档及启停状态，不按名称覆盖其他数据。

已删除的案例资源会在下一次初始化时补建。不要删除案例审计标记后再初始化，否则无法识别原文档。该脚本不在部署或服务启动时自动执行，也不清理旧的验收数据。报告列出各行业 Agent/知识库 ID、创建及保留计数、发布状态和建议试聊问题。

## 验证

`tests/test_industry_seed.py` 覆盖并发去重、人工修改和归档保留、行业资料检索与租户隔离、无效模型绑定、导入中途失败回滚，以及显式绑定时不发布、不调用模型。

案例初始化现已写入结构化 industry、tags、is_example 字段，支持 Agent 列表快速筛选。已有批次由迁移 0013_agent_classification 按审计记录回填；重复执行初始化仍保留人工分类修改。

## 我的智能体

Agent 列表的“范围”提供“全部 / 我创建的 / 仅看案例”单选，默认全部；可继续叠加行业、用途、名称、发布状态和归档筛选。URL 使用 `scope=mine` 或 `scope=examples`，旧 `examples=1` 链接仍可打开。

接口 `GET /api/v1/agents/catalog?mine_only=true` 按当前认证身份筛选，创建者由服务端写入 `agents.created_by`，编辑、发布或归档不会转移归属。迁移 `0015_agent_creator` 根据同租户最早的 `agent.created` 审计记录回填；没有记录的保持空值，不会推定为当前用户。案例初始化记录其脚本执行身份。

多人共用管理员令牌时属于同一身份；无令牌开发模式统一为 `local-development`。此筛选不新增账号或改变创建权限，目前创建 Agent 仍需管理员权限。
