# 用户权限模块 P0

P0 提供单租户、固定三角色的成员与访问控制基线，覆盖成员目录、本地成员账号、
活动会话、服务凭据和操作审计。当前不包含自定义角色、资源级 ACL、团队范围或
跨租户管理。

## 数据模型

- 真人成员统一存放在 `enterprise_users`，管理员、客服操作员和只读用户共用同一目录。
- 本地登录凭据存放在 `local_admin_credentials`；`is_emergency=true` 仅标记应急管理员。
- `staff_accounts` 继续表示服务凭据，不作为真人成员使用。
- 本地会话与 SSO 会话分别存放在 `local_admin_sessions`、`enterprise_sessions`。
- 迁移 `0040_access_control` 增加登录时间、最近活动时间、停用原因、授权版本、
  会话 IP/User-Agent 和成员会话索引。

## 固定角色

| 权限码 | 管理员 | 客服操作员 | 只读查看 |
| --- | --- | --- | --- |
| `member.read` / `member.manage` | 是 | 否 | 否 |
| `role.read` / `role.manage` | 是 | 否 | 否 |
| `session.self` | 是 | 是 | 是 |
| `session.read` / `session.revoke` | 是 | 否 | 否 |
| `credential.read` / `credential.manage` | 是 | 否 | 否 |
| `conversation.read` | 是 | 是 | 是 |
| `conversation.write` | 是 | 是 | 否 |
| `handoff.manage` / `ticket.manage` | 是 | 是 | 否 |
| `agent.read` | 是 | 是 | 是 |
| `agent.manage` / `agent.publish` | 是 | 否 | 否 |
| `knowledge.read` | 是 | 是 | 是 |
| `knowledge.manage` / `knowledge.publish` | 是 | 否 | 否 |
| `model.manage` / `tool.manage` / `channel.manage` | 是 | 否 | 否 |
| `evaluation.read` / `observability.read` | 是 | 是 | 是 |
| `evaluation.run` | 是 | 否 | 否 |
| `audit.read` | 是 | 否 | 否 |

旧 `Admin`、`Operator`、`Reader` 依赖仍保留用于兼容；新增接口使用
`require_permission(...)`。`GET /api/v1/me` 返回当前角色的权限码，前端据此展示入口。

## API

| 方法 | 路径 | 权限 |
| --- | --- | --- |
| GET | `/api/v1/members` | `member.read` |
| GET | `/api/v1/members/{id}` | `member.read` |
| POST | `/api/v1/members/invite` | `member.manage` |
| PATCH | `/api/v1/members/{id}` | `member.manage` |
| POST | `/api/v1/members/{id}/enabled` | `member.manage` |
| POST | `/api/v1/members/{id}/password/reset` | `member.manage` |
| GET | `/api/v1/members/{id}/sessions` | 本人或 `member.read` |
| POST | `/api/v1/members/{id}/sessions/revoke` | 本人或 `member.manage` |
| GET | `/api/v1/sessions` | 当前登录用户 |
| POST | `/api/v1/sessions/revoke` | 当前登录用户 |
| GET | `/api/v1/roles` / `/api/v1/permissions` | `role.read` |

成员列表支持姓名/用户名/身份 Subject/ID 搜索、角色筛选、状态筛选和服务端分页，
默认每页 10 条。

## 安全约束

- 新成员使用一次性临时密码，登录后通过现有改密流程设置正式密码。
- 角色变化或启停状态变化会立即撤销该成员的全部本地和 SSO 工作台会话。
- 管理员不能修改自己的角色或启停状态。
- 不能停用或降级最后一个有效管理员。
- 应急管理员不能停用、降级或绑定企业身份。
- 重置密码会撤销该成员的本地会话。
- 会话列表返回 IP、User-Agent、最近活动时间和过期时间；不返回令牌或摘要。
- 成员、角色、会话和密码操作写入 `audit_records`，不记录密码或完整令牌。

## 前端入口

“系统管理”分组提供：

- 成员管理：邀请本地成员、编辑角色与状态、重置密码、查看并撤销会话。
- 角色与权限：查看固定角色权限矩阵。
- 会话与安全：查看并撤销当前账号的活动会话；非管理员也可使用。

## 验证

- `ruff check backend`
- `npm run check:i18n`
- `npm run typecheck`
- `npm run build`
- PostgreSQL 集成测试：成员邀请、首次改密、权限拒绝、会话撤销、角色变更即时失权、
  最后管理员保护、自修改保护和审计记录。
- Alembic `0040_access_control` 升级、回退、再升级验证通过。

## 后续阶段

- P1：自定义角色、权限编辑和更细的资源授权。
- P2：团队/组织范围、成员邀请邮件、SCIM/企业目录同步。
- P3：资源级 ACL、审批流和跨租户平台管理。
