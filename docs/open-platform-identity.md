# 开放平台第三期：企业身份与嵌入聊天

本期支持通用 OIDC、企业微信、钉钉、飞书登录，覆盖平台工作台与嵌入聊天。OAuth 2.0 指平台作为客户端接入企业授权码登录；OIDC 使用授权码 + PKCE S256。平台并非对外签发第三方 OAuth 授权的完整授权服务器。

## 管理入口

1. 开放平台 → 企业身份与 SSO → 新增身份提供方。
2. 界面输入 Client Secret / App Secret，服务端用已有 CredentialVault 加密保存；编辑时留空保留原值，不回显密钥。无需为每个提供方配置环境变量。
3. 平台公开地址填写浏览器实际访问的源，例如 `https://agent.company.com`。保存后复制回调地址到身份平台的回调白名单。
4. 启用提供方，选择是否允许工作台登录；点击验证登录。首次登录会登记企业用户，默认没有工作台权限。
5. 企业用户映射 → 管理权限，分配 viewer/operator/admin。停用、角色变化、身份重新关联都会撤销相关会话。
6. 工作台的“访问与策略”页显示可用企业登录入口。工作台凭据保存在当前浏览器会话，最长一小时；到期重新登录。

## 各平台配置

| 类型 | 必填配置 | 企业归属与身份依据 |
| --- | --- | --- |
| OIDC | Issuer URL、Client ID、Client Secret；支持 basic/post 两种客户端认证方式 | 验证发现文档 issuer、JWKS 签名、固定 RS256/ES256 算法白名单、aud、azp、exp、iat、nonce；使用 sub 作为身份 |
| 企业微信 | Corp ID、自建应用 Agent ID、应用 Secret | 使用该企业应用凭据获取用户信息；必须返回企业成员 userid，拒绝仅有外部联系人 openid 的结果 |
| 钉钉 | 企业内部应用 AppKey、AppSecret | 获取用户 unionId，再使用当前应用的企业 accessToken 将 unionId 转换为企业 userid；非成员拒绝登录 |
| 飞书 | App ID、App Secret、本企业 Tenant Key | 获取用户 open_id，严格校验返回 tenant_key 与配置相符 |

企业微信需配置企业微信登录授权及可信域名；钉钉需开通个人信息读取及 unionId 查询企业用户权限；飞书需开放用户基本信息权限并配置网页应用回调地址。企业后台应用必须发布并对目标成员可见。具体权限名称以各供应商后台当前显示为准。

身份的唯一键为“租户 + 身份提供方 ID + 供应商主体 ID”。修改 Issuer、Client ID、Corp ID 等身份作用域需要新建提供方，避免接管旧身份。不会通过姓名、邮箱自动合并账号。管理员确认同一人后，可显式将来源身份关联到目标用户 ID；双方会话随即撤销，原用户的历史聊天不迁移。

管理员配置的 OIDC 地址允许 HTTPS；HTTP 仅允许 localhost/回环开发地址。私有企业 IdP 应提供可信 HTTPS 证书。第三方平台真实扫码登录还需要企业提供有效应用参数、权限和回调白名单；本地模拟测试不代表已完成企业真实联调。

## 嵌入聊天

应用详情 → 嵌入聊天：设置标题、主题色、允许嵌入来源和登录方式。来源要求完整协议、主机及端口，不支持通配符；例如 `https://oa.company.com`。身份提供方的公开地址必须与聊天 iframe 所在的平台来源一致。

```html
<script src="https://agent.company.com/embed.js"></script>
<script>
  const chat = NexusDeskChat.mount({
    appId: "APPLICATION_UUID",
    baseUrl: "https://agent.company.com",
    // 可选：container: '#chat-container'，否则显示右下角悬浮入口
    // 可选：agentId: 'AUTHORIZED_AGENT_UUID'
  });
  // chat.open(); chat.close(); chat.destroy();
</script>
```

组件在平台自己的 iframe 中运行，使用企业登录弹窗。浏览器须允许弹窗。消息传递同时校验来源、窗口对象和登录随机通道，令牌不放在 URL；应用密钥不进入组件。聊天令牌仅在 iframe 内存中持有，最长 15 分钟。登出会在服务端撤销令牌；配置更新、提供方停用、用户停用也会使已有会话失效。

聊天支持选择应用授权 Agent、创建会话、发送消息、展示处理状态与回复、新建会话。后台运行状态通过轮询获取，当前不是逐 token 展示。关闭并重新创建组件会开始新会话。

### 已有企业登录态的服务端换票

企业后端必须从已验证的自身登录会话中取得用户 ID，不能直接信任网页传入的用户 ID。后端持应用密钥调用：

```http
POST /openapi/v1/chat/token
Authorization: Bearer opk_...
Content-Type: application/json

{"external_user_id":"employee-123","name":"张三","parent_origin":"https://oa.company.com"}
```

返回 `access_token`、`token_type`、`expires_in: 900`、规范化 `external_user_id` 和用户摘要。应用服务端断言的身份隔离在当前应用的命名空间内，不自动授予工作台权限。创建的用户凭据绑定企业用户和应用，不能伪造 `X-External-User-ID`、创建新凭据或调用知识库同步及后台管理 API；撤销父应用密钥也会使其失效。

Python 服务端：

```python
from nexusdesk import Client
client = Client("https://agent.company.com/openapi/v1", app_key)
session = client.chat_token(verified_employee_id, "https://oa.company.com", name=verified_name)
```

JavaScript 服务端：

```js
const session = await client.chatToken(verifiedEmployeeId, 'https://oa.company.com', {name: verifiedName});
```

企业前端通过自己的受认证后端获取短期令牌：

```js
NexusDeskChat.mount({
  appId: 'APPLICATION_UUID', baseUrl: 'https://agent.company.com',
  getToken: async () => {
    const r = await fetch('/internal/agent-chat-session', {method:'POST', credentials:'same-origin'});
    if (!r.ok) throw new Error('Login required');
    return r.json(); // {access_token: 'chat_...', ...}
  }
});
```

企业后端的换票接口应使用既有登录校验、CSRF 防护与访问速率限制。`getToken` 在组件初始化或用户点击重新获取凭据时调用。

## 部署与运维

- 数据库迁移：`0027_enterprise_identity`，在已有 `0026_open_integrations` 之后执行。迁移仅增加表，不修改 Agent 案例或已有会话。
- API 增加 PyJWT 加密依赖。安装依赖后迁移并重启 API；前端重新构建。
- 反向代理保留 `/api/`、`/openapi/`；`/embed/<app-id>` 由前端 SPA 路由承接，`/embed.js` 为静态文件。前端和 API 对外使用同一个平台来源。
- 回调路径为 `/api/v1/sso/callback/<provider-id>`，不从请求 Host 或转发头推断公开地址，始终使用管理员保存的地址。
- 登录 state 一次性消费，有效期 5 分钟，并绑定 HttpOnly、SameSite=Lax 浏览器 cookie。HTTPS 部署设置 Secure。OIDC nonce 与 PKCE verifier 均随机生成，verifier 加密存储。
- 登录会话存储令牌哈希；过期 state/session 在登录发起时清理。待处理登录数量按租户限制。生产代理应另配置登录接口速率限制、请求大小和连接超时。
- 网关和反向代理日志不应记录 Authorization、token 响应或 OAuth 回调查询参数。供应商查询型接口含一次性 code / accessToken，不应打开 HTTP 客户端明文调试日志。
- 备份数据库时同时备份安装凭据主密钥文件，沿用现有模型凭据的备份方式。不要将该文件放进公开仓库。
- 生产部署沿用现有平台服务令牌和角色策略，不使用无令牌的 development 管理员回退。SSO 不会自动关闭开发模式的本机管理员入口。
- 不包含 SAML、SCIM 自动离职同步、企业组织架构同步、供应商全局单点登出、OAuth 授权服务器或静默续期。这些可单独扩展。

## 协议参考

- [OpenID Connect Core](https://openid.net/specs/openid-connect-core-1_0.html)
- [OAuth 2.0 Security Best Current Practice / RFC 9700](https://www.rfc-editor.org/rfc/rfc9700.html)
- [企业微信网页授权](https://developer.work.weixin.qq.com/document/path/91023)
- [钉钉获取用户 token](https://open.dingtalk.com/document/orgapp/obtain-user-token)
- [飞书获取 user_access_token](https://open.feishu.cn/document/authentication-management/access-token/get-user-access-token)
- [飞书官方 SDK 用户信息结构](https://github.com/larksuite/oapi-sdk-python/blob/v2_main/lark_oapi/api/authen/v1/model/user_info.py)
