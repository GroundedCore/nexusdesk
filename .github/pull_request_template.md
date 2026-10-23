<!--
PR 模板 / Pull Request Template
请用中文或英文填写；鼓励双语。 / Fill in Chinese or English; bilingual is encouraged.
删除不适用的章节。 / Delete sections that do not apply.
-->

## 目的 / Purpose

<!-- 这个 PR 解决什么问题？关联的 Issue（如 Fixes #123）？
     What problem does this PR solve? Link related issues (e.g. Fixes #123). -->

-

## 影响范围 / Scope of Impact

<!-- 涉及哪些模块/目录？是否触及高风险路径（.github/workflows/、deploy/、
     backend/migrations/、认证授权、敏感凭据）？
     Which modules/directories are affected? Does it touch high-risk paths
     (.github/workflows/, deploy/, backend/migrations/, auth, credentials)? -->

-

## 验证结果 / Verification

<!-- 本地或 CI 验证步骤与结果：pytest、ruff、npm run check:i18n、npm run build、
     手动验证步骤及截图等。
     Local or CI verification steps and results: pytest, ruff,
     npm run check:i18n, npm run build, manual checks, screenshots, etc. -->

-

## 兼容性影响 / Compatibility

<!-- 是否包含数据库迁移、API 变更、配置变更？是否需要数据备份或特殊的
     升级/回滚步骤？无影响请写“无 / None”。
     Database migrations, API changes, config changes? Any backup or special
     upgrade/rollback steps required? Write "None" if not applicable. -->

-

## 自查清单 / Self-Check List

- [ ] 不包含任何敏感信息（密钥、密码、内网地址等）/ Contains no sensitive information (secrets, passwords, internal addresses)
- [ ] Commit 信息符合 groundedcoreai-commit-standard 规范 / Commit messages follow the groundedcoreai-commit-standard
- [ ] 文档改动已提供中英双语版本 / Documentation changes are provided in both Chinese and English
- [ ] CI 检查（Backend / Frontend）已全部通过 / All CI checks (Backend / Frontend) pass
- [ ] 已评估对 `main` 与 `integration` 的影响；本 PR 目标分支正确 / Impact on `main` and `integration` reviewed; this PR targets the correct branch
- [ ] 高风险改动已标注并请求核心维护者审核 / High-risk changes are flagged and a core maintainer review is requested
