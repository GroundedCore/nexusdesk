# 参与贡献

> English speakers: this document is currently Chinese-only. Feel free to open a
> [discussion](https://github.com/GroundedCore/nexusdesk/discussions) or an issue in English —
> we will reply in English and can translate the guidance for you.

感谢你愿意参与 NexusDesk 的建设。可参与的方向包括问题反馈、文档改进、测试用例与代码贡献。

在动手之前，请先了解这个项目的**当前状态与边界**，这能省下双方很多时间：

- 项目处于持续开发阶段，已具备可运行的客服核心流程，但**尚未达到开箱即用的生产可用程度**。
- 功能现状与规划是分开说明的。`docs/` 下各模块 README 的第 1 节描述**代码现状**，其余章节保留的是**目标规划**，不代表已交付。
- 已知限制汇总在各模块文档与 [模块交付说明](docs/modules-delivery.md) 中，请以那里为准。

## 提交问题

1. 使用对应的 issue 模板：**Bug 报告**、**功能建议**、**部署问题**。
2. 说明**使用场景、复现步骤、部署方式与脱敏日志**。部署问题尤其需要"部署方式 / 中间件 / 日志"这三样。
3. 如果还不确定是否适合你的场景，或问题还不到能复现的程度，欢迎先到
   [Discussions](https://github.com/GroundedCore/nexusdesk/discussions) 讨论。

⚠️ **请勿在问题报告、截图或代码中包含 API Key、访问令牌、数据库密码及客户隐私数据。**

涉及安全漏洞请**不要**开公开 issue，按 [SECURITY.md](SECURITY.md) 的方式私下报告。

## 贡献代码

1. **开发前先阅读** [本地开发](docs/local-development.md) 与 [模块开发规范](docs/module-development.md)。
2. 涉及架构或数据结构变化的改动，建议**先开 issue 或 discussion 对齐方向**，避免做完再返工。
3. 提交变更时说明**解决的问题、影响范围与验证结果**。
4. 接口或数据结构发生变化时，**同步更新文档和数据库迁移**。迁移只新增、不修改已应用的历史迁移。

### 提交前的自检

在 `backend` 目录下运行（本项目的 CI 执行的就是这些）：

```bash
# 代码风格与静态检查
ruff check .
ruff format --check .

# 后端测试
pytest
```

> 提示：未设置 `TEST_DATABASE_URL` 时，标记为 `postgres` 的测试会被跳过。涉及迁移或事务的改动请配置真实 PostgreSQL 后再跑一遍。

前端在 `frontend` 目录下：

```bash
npm run build      # 含 i18n 检查与 TypeScript 编译
```

### 一些约定

- **文档默认英文，界面文案与设计文档默认中文**：`README.md` 为英文（仓库主页默认显示），
  中文版在 `README.zh-CN.md`；更新日志同样是 `CHANGELOG.md`（中文）+ `CHANGELOG.en.md`（英文）。
  前端多语言文案在 `frontend/src/i18n/locales/` 下同步维护。
- **改动 README 时两份都要改**：只改一份会让两个语言版本悄悄分叉。两份的章节结构应保持一致。
- **不要顺手批量格式化**与本次改动无关的文件，避免制造巨大的无关 diff。
- 新增功能请尽量**带上测试**，尤其是事务、迁移与权限相关部分。
- **写操作必须保持"提议 → 人工确认"的既有约束**：Agent 不得直接执行不可逆的业务动作。

## 贡献文档

文档错误、表述不清、示例跑不通，都是很有价值的反馈。直接提 PR 或在 issue 中说明位置即可。

## 行为准则

请保持技术讨论的专注与礼貌：就事论事、不针对个人、不引战。这个项目面向企业私有化场景，
讨论中经常出现真实客户环境的信息，**请勿索取或泄露他人的敏感配置**。

## 许可证

本项目采用 [Apache License 2.0](LICENSE)。提交贡献即表示你同意以该许可证授权你的贡献。
