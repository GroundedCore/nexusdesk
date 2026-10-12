该目录内置渠道适配器，供 modules/channel 调用：

- `base.py`：`ChannelAdapter` Protocol 与 `IncomingEnvelope`/`WebhookResult`/`DeliveryResult` 契约；`WebhookResult` 支持 `challenge`（challenge 应答）、`ack`（适配器自定义 JSON ACK，如 Discord `{"type":1}`）与 `envelope`（标准化入站消息）。
- `registry.py`：provider 注册表与 `ProviderCatalog`（`generic_webhook`、`wecom`、`wecom_kf`、`feishu`、`dingtalk`、`slack`、`telegram`、`discord`）。
- `wecom.py` / `feishu.py` / `dingtalk.py`：企业微信、飞书、钉钉的验签/解密、回调标准化与出站投递实现。
- `wecom_kf.py`：微信客服（事件回调 + `kf/sync_msg` 拉取客户消息 + `kf/send_msg` 回复）。
- `slack.py`：Slack Events API（Signing Secret 验签、`chat.postMessage` 主动回复）。
- `telegram.py`：Telegram Bot Webhook（Secret Token 校验、`sendMessage` 回复）。
- `discord.py`：Discord 应用交互端点（Ed25519 验签、PING/斜杠命令、机器人发频道消息）。
- `crypto.py`：平台凭据加解密（`CredentialVault`）与验签辅助。

通用 Webhook 协议由 modules/channel 实现。新增平台（微信公众号、Teams 等）在此目录新增 adapter 并注册即可，无需改动调用方。

说明：Discord 无「普通消息」型 HTTP webhook，普通频道消息需 Gateway 长连接；当前采用应用交互端点（斜杠命令）接入。
