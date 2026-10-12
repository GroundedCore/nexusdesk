# 渠道接入（channel）

把外部渠道消息转换为统一输入，并可靠地将服务回复交付给正确的客户会话。

管理台入口已统一到「开放平台 → 客服渠道」，不再在配置管理中单独展示。自研系统调用 Agent 优先使用开放平台应用 API；本模块保留客服消息映射、验签、去重及人工回复拉取能力。现有接口不变，具体区别见 [开放平台接入说明](../../../../../docs/open-platform.md)。

本文是模块开发规划。当前实现以第 1 节和代码为准，其余内容为目标设计；P0/P1 表示建设优先级，不表示已经完成。跨模块约定见[独立开发规范](../../../../../docs/module-development.md)，全局索引见[模块架构](../../../../../docs/architecture.md)。

## 1. 当前实现与差距

已实现通用入站消息、渠道令牌、消息标识去重、启停及令牌轮换，并内置企业微信、微信客服、飞书、钉钉、Slack、Telegram、Discord 七个原生适配器（`wecom`/`wecom_kf`/`feishu`/`dingtalk`/`slack`/`telegram`/`discord`）。原生回调采用**入站 outbox**：webhook 只做验签+标准化+落库即返回 ACK，由后台入站 worker 再建会话/触发 Runtime，支持退避重试与死信。

- 通用协议（`generic_webhook`）：POST `/ingress/channels/{id}/messages` 提交、GET `/ingress/channels/{id}/replies` 拉取；支持可选 HMAC-SHA256 验签（签名输入为 timestamp + 点号 + 原始请求体，接受 300 秒时钟窗口），密钥从进程环境引用。
- 原生协议：POST `/ingress/channels/{id}/webhook/{provider}` 接收回调，适配器负责平台验签/解密、challenge 应答与消息标准化，随即写入 `channel_inbound_events`（按 `channel_id + external_message_id` 去重）并返回平台 ACK；入站 worker 消费事件建立会话并触发 Runtime。Agent 及人工回复经 `channel_deliveries` 出站队列，由投递 worker 主动推送并支持退避重试；记录经 `/channels/{id}/deliveries` 查询、`/deliveries/{did}/retry` 重放。
- 平台凭据（CorpID、AppSecret、EncodingAESKey、AppKey、机器人 Webhook 等）经 `CredentialVault` 按 `tenant + channel_id + provider` 加密保存，不回显、不入日志。
- 多租户：webhook 按渠道行解析 `tenant_id`，入站/出站 worker 逐事件携带所属租户，不再依赖部署级单一租户；会话幂等锁按渠道加锁，避免跨租户串行。

## 2. 功能模块与优先级

P0：独立开发及稳定集成所需基础能力。P1：规模化、治理或复杂场景扩展。按现有能力增量交付，不重复重建已实现功能。

| 功能模块 | 功能点 | 优先级 |
|---|---|---|
| 渠道配置 | 类型、连接参数、Agent 绑定、启停与健康检测 | P0 |
| 入站认证 | 渠道令牌、原生签名、时间窗与重放防护 | P0 |
| 消息标准化 | 文本、事件、外部消息编号与时间；扩展附件引用 | P0 文本，P1 多模态 |
| 身份映射 | 可信客户标识、外部会话映射、渠道隔离 | P0 |
| 幂等收件 | 消息唯一键、内容冲突检测、原处理结果查询 | P0 |
| 回复投递 | 游标拉取、原生主动投递适配器、投递记录与重试 | P0 拉取，P1 推送 |
| 可靠重试 | 入站/出站 Outbox、退避、死信、人工重放与补偿查询 | P0 已落地 |
| 平台适配 | 内置企业微信、微信客服、飞书、钉钉、Slack、Telegram、Discord；官网 Web、微信公众号等按需扩展 | P1 |
| 流量治理 | 渠道速率限制、消息大小、故障隔离与指标 | P1 |

## 3. 数据与状态归属

现有 `channels`、`channel_receipts`，以及 `0031_channel_adapters` 新增的 `channel_conversations`（外部会话到平台会话映射）、`channel_deliveries`（出站 outbox）与 `channel_delivery_attempts`（投递尝试与回执）；`0032_channel_inbound_outbox` 新增 `channel_inbound_events`（入站 outbox，含租户、状态、尝试次数与租约）。`channels` 扩展 `provider`、`config`、`encrypted_credentials`、`last_error_code`、`last_activity_at`。渠道凭据加密保存，消息内容由会话账本保存。

现有表由已提交的 Alembic 迁移管理；本规划不创建新表。未来扩展使用新增迁移，避免修改已应用历史迁移。跨模块数据先明确所有者，再增加服务端口。

## 4. 接口与事件契约

以下 HTTP 路径均以 `/api/v1` 为前缀。目标方法、字段和事件为待实现契约，不能作为现有 API 调用。

当前接口：`GET/POST /channels`、`PATCH /channels/{id}`、`GET /channels/providers`、`GET/PUT /channels/{id}/config`、`GET /channels/{id}/deliveries`、`POST /channels/{id}/deliveries/{did}/retry`、`POST /ingress/channels/{id}/messages`、`GET /ingress/channels/{id}/replies`、`POST /ingress/channels/{id}/webhook/{provider}`。通用入站使用 X-Channel-Token，消息含 message_id/session_id/text；原生 webhook 由适配器验签与解析。

已落地适配器契约（`integrations/channels`，`ChannelAdapter` Protocol）：`validate(config, credentials)`、`handle_webhook(body, query, headers, config, credentials) -> WebhookResult`、`deliver(content, target, config, credentials) -> DeliveryResult`，并声明 `config_fields`/`credential_fields` 供管理台动态渲染。统一消息 `IncomingEnvelope` 含 channel_id、external_message_id、external_session_id、external_user_id 与 text。

## 5. 依赖与职责边界

依赖 conversation 建立会话，调用 customer_service 路由消息。只负责协议、身份映射及投递，不自行执行 Runtime、写工单或改变人工服务状态。外部平台 SDK 留在 integrations/channels。

独立开发不要求立即拆成微服务。先定义 Protocol/DTO 与假实现，由组合入口注入；新增跨模块调用不直接导入对方仓储。当前共享事务例外见独立开发规范。

## 6. 关键设计约束

- 不能信任终端自行提交的租户或客户身份；服务端渠道 token 不下发给客户浏览器。
- 相同消息编号及内容返回原结果，不同内容复用编号返回冲突。
- 入站落库与业务受理须有一致性保证；出站采用至少一次投递加幂等，不能承诺网络端到端恰好一次。
- 推送重试要尊重回复顺序和渠道窗口，过期内容不能无条件重发。

## 7. 管理台规划

渠道列表按 provider 展示，创建时选择 provider 并按适配器声明的 `config_fields`/`credential_fields` 动态渲染配置与凭据表单；支持令牌交付、回调地址与接入示例，以及投递记录查看与失败重试。后续增加签名诊断和健康状态。

界面权限与后端校验保持一致；配置区分草稿和生效版本。加载、空数据、冲突、拒绝和失败均有明确反馈，未实现操作不显示为可用。

## 8. 实施阶段

- P0（已完成）：冻结统一信封及适配器契约，补齐客户身份映射规则。
- P1（企业微信/微信客服/飞书/钉钉/Slack/Telegram/Discord 已落地）：原生协议适配、出站 outbox、投递记录与重放；更多平台按需扩展。

每阶段先冻结最小接口与验收用例，再实现领域服务、持久层、API 和页面；集成时验证与上下游的兼容性。

## 9. 独立开发验收

- 并发重发同一消息只创建一次业务运行，跨渠道相同编号不互相影响。
- 签名错误、停用渠道或无权会话无法读取回复。
- 模拟渠道断网及回执丢失后可恢复投递且不重复办理业务。
- 适配器可通过录制且脱敏的协议样例独立验证。

上述为目标验收条件，不是已有测试覆盖声明。已验证范围参见项目 `docs/platform-validation.md`。交付需更新本节对应测试位置及第 1 节实现状态。
## 10. 阶段 2 P0 收口

- 出站长文分片：按适配器 `max_message_chars` 切分（Slack 4000 / Telegram 4096 / Discord 2000 / 企业微信与微信客服 2048 字符），优先在换行或空格边界断开；分片以 `seq` 写入 `channel_deliveries`，`0033_channel_delivery_chunks` 将唯一键改为 `(channel_id, message_id, seq)`。
- 保序投递：投递 worker 仅在 `(channel_id, message_id)` 分组内更小 `seq` 已 `delivered` 时才领取后续分片，避免乱序或跳片。
- 限流退避：适配器遇到 429 时返回 `DeliveryResult.retry_after`（解析 `Retry-After` 的秒数或 HTTP-date，上限 1 小时），worker 优先采用该值，否则回退到默认退避序列。
- 微信客服 `sync_msg`：回调 Token 拉取整批客户消息，`next_cursor` 持久化到 `channels.provider_cursor`；同批多条文本分别写入入站 outbox（按 `external_message_id` 去重），不再只处理第一条。
- 令牌轮换收敛：`rotate-token` 仅对 `generic_webhook` 渠道开放，原生适配器渠道返回 422 `channel_token_not_supported`。

## 11. 阶段 2 P1（唤醒 + 并发 + 共享 token 缓存）

- 即时唤醒：webhook 落库、出站入队、人工重放三条路径都在各自事务内 `pg_notify('channel_work')`；入站/出站循环通过共享 `LISTEN` 连接订阅并立刻领取，未收到通知时按 `AGENT_CHANNEL_WORKER_POLL_SECONDS`（默认 5s）兜底轮询。
- 并发领取：`AGENT_CHANNEL_WORKER_CONCURRENCY`（默认 4）控制每进程并发的领取循环数；`FOR UPDATE SKIP LOCKED` + owner/lease 保证多副本不重复处理，分片仍按 `seq` 保序。
- 共享 token 缓存：provider access token 两级缓存——进程内热缓存 + `channel_access_tokens` 表；scope 为 `provider:主体:密钥哈希`，密钥不落库，多副本共用同一 token。
