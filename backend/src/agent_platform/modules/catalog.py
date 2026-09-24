"""Implemented MVP capabilities; production extensions are documented separately."""

MODULES = [
    {
        "id": "channel",
        "name": "渠道接入",
        "description": "通用 Webhook、渠道令牌、会话映射、消息去重与回复拉取",
        "status": "mvp",
    },
    {
        "id": "conversation",
        "name": "会话管理",
        "description": "消息、上下文、会话归属与消息调度",
        "status": "mvp",
    },
    {
        "id": "agent_config",
        "name": "Agent 配置与发布",
        "description": "Agent 定义、版本、发布与回滚",
        "status": "mvp",
    },
    {
        "id": "agent_runtime",
        "name": "Agent Runtime",
        "description": "异步 ReAct 循环、预算、排队、取消与事件回放",
        "status": "mvp",
    },
    {
        "id": "policy",
        "name": "策略与权限",
        "description": "授权、数据范围、动作确认与配额",
        "status": "mvp",
    },
    {
        "id": "model_gateway",
        "name": "模型网关",
        "description": "五类模型目录、版本方案、流式 Chat、调用治理与用量",
        "status": "mvp",
    },
    {
        "id": "knowledge",
        "name": "知识服务",
        "description": "文档、索引、检索、引用与权限过滤",
        "status": "mvp",
    },
    {
        "id": "tool_gateway",
        "name": "工具网关",
        "description": "只读 API 注册、主机白名单、参数校验、启停与调用",
        "status": "mvp",
    },
    {
        "id": "customer_service",
        "name": "客服业务与流程",
        "description": "客户消息路由、工单拟定、幂等确认与状态流转",
        "status": "mvp",
    },
    {
        "id": "human_handoff",
        "name": "人工客服协同",
        "description": "转接、分配、交接与恢复自动服务",
        "status": "mvp",
    },
    {
        "id": "observability",
        "name": "运行观测与审计",
        "description": "运行轨迹、延迟、Token 用量与操作审计",
        "status": "mvp",
    },
    {
        "id": "evaluation",
        "name": "质量评测",
        "description": "用例、模拟工具、断言与版本化评测报告",
        "status": "mvp",
    },
]
