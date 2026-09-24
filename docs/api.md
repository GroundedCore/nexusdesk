# API

基础路径：`/api/v1`。OpenAPI：`/openapi.json`；交互文档：`/docs`。

| 方法 | 路径 | 用途 |
|---|---|---|
| GET | /health | 进程存活；不检查数据库 |
| GET | /modules | 模块实现状态 |
| GET | /ready | 检查 PostgreSQL 表可访问；不保证模型凭据有效 |
| POST | /runs | 创建异步运行，返回 202 |
| GET | /runs/{id} | 查询状态和最终回复 |
| POST | /runs/{id}/cancel | 取消排队任务或请求中断运行 |
| GET | /runs/{id}/events | SSE 阶段事件，支持 after 与 Last-Event-ID |

平台接口在配置 AGENT_API_TOKEN 后要求 `Authorization: Bearer <token>`。租户由部署配置决定，不接受客户端指定租户。/health 和 /modules 是公开元数据。

## 提交与查询

```powershell
$body = @{conversation_id='support-001'; message='查询演示'} | ConvertTo-Json
$run = Invoke-RestMethod -Method Post -Uri 'http://127.0.0.1:8000/api/v1/runs' -ContentType 'application/json; charset=utf-8' -Body ([Text.Encoding]::UTF8.GetBytes($body))
Invoke-RestMethod "http://127.0.0.1:8000/api/v1/runs/$($run.id)"
curl.exe -N "http://127.0.0.1:8000/api/v1/runs/$($run.id)/events"
```

请求 conversation_id 是调用方稳定的外部会话标识；响应 conversation_id 是数据库内部 UUID。后续消息继续使用原外部标识 support-001。

提交请求只允许 conversation_id 和 message；模型、工具权限和执行预算来自服务端配置。

## SSE 示例

```text
id: 7
event: run.completed
data: {"output":"[演示模式] ...","error_code":null}

```

事件顺序以 id 为准。断线不会取消运行，重新连接可以从已消费事件之后继续。没有逐 Token 流。

HTTP 错误：401 无效令牌；404 运行不存在或不在当前租户；409 会话忙；422 输入不合法；429 容量不足；503 数据库不可用。

运行 error_code：model_timeout、run_timeout、model_round_limit、tool_call_limit、repeated_tool_call、invalid_model_response、duplicate_tool_call_id、empty_model_response、model_output_too_large、worker_lost、queue_timeout、configuration_changed、execution_error。

工具错误作为模型观察输入，本身不一定使运行失败。模型可以说明失败或在预算内改正参数。

## 平台接口

Agent、知识库、会话、人工队列、工单、渠道、评测与审计的接口分组见 [平台使用说明](platform.md)。完整请求与响应字段以运行服务的 `/docs` 为准。

## 模型网关

新增 `/api/v1/model-gateway`，支持连接、模型、版本方案、六种 operation、Chat SSE、临时媒体和调用记录。路径、权限及请求示例见 [网关实际协议](../backend/src/agent_platform/modules/model_gateway/PROTOCOL.md)。Runtime 运行事件仍是轮次/工具事件；逐段模型输出使用独立 `/model-gateway/stream` 接口。


本轮模块增量、配置和验证状态见 [模块实施说明](modules-delivery.md) 与 [实施记录](implementation-progress.md)；旧版验证记录保留作历史记录。
