# NexusDesk 分支管理与发布规范

Repository: `GroundedCoreAI/nexusdesk`

> ⚠️ **本文档（V1）已被 `workflow-spec-v2.md` 取代（2026-10），仅作历史参考。**
> V2 的主要变化：`integration` 不再要求人工审批（必需 CI 通过即可合并）；
> `integration` Push 且 CI 通过后自动部署到测试服务器（镜像以 commit SHA 标识）；
> 正式 Tag / GitHub Release 不再触发部署。
>
> 本文档为规范的最终参考版。日常实操速查见同目录上级 `SKILL.md`。

## 1. 核心原则

1. `main` 是稳定主干，只接收经过发布准入验证的代码。
2. `integration` 是日常开发集成分支，允许集成尚未正式发布的新功能。
3. 所有代码必须通过 Pull Request（PR）合并，禁止直接 Push 到受保护分支。
4. 社区贡献者的代码默认先进入 `integration`，不得直接进入 `main`。
5. PR 审核和 CI 通过只是必要条件，不代表代码已经具备发布资格。
6. 正式发布必须通过独立的发布候选验证，并使用明确的版本 Tag。
7. `main` 更新不得触发任何环境的部署。
8. 正式版本 Tag 不得移动或覆盖，生产环境只部署明确指定的 Release 版本。

> 本规范以最大限度降低风险为目标，但任何测试与审核机制都无法保证软件绝对没有缺陷。

## 2. 分支模型

仓库的**默认分支是 `integration`**（不是 `main`）。所有日常 PR 的默认目标是
`integration`；`main` 只接收发布候选 PR 和必要的 Hotfix 流程。

### 长期分支

| 分支 | 职责 |
|---|---|
| `main` | 最新的发布准入通过的稳定代码基线 |
| `integration` | 汇集内部开发与社区贡献，持续集成验证 |

### 临时分支

| 分支 | 用途 |
|---|---|
| `feat/*` | 新功能开发 |
| `fix/*` | Bug 修复 |
| `refactor/*` | 代码重构 |
| `docs/*` | 文档修改 |
| `chore/*` | 工程维护 |
| `release/vX.Y.Z` | 冻结版本并进行发布候选验证 |
| `hotfix/*` | 生产环境紧急修复 |

不建立长期 `develop` 分支。`integration` 承担日常集成职责，`release/*` 只在发布准备期间临时存在。

**所有临时分支（含 `release/*`、`hotfix/*`）在其 PR 合并完成后必须删除**，避免分支堆积和误用。唯一的例外是 Hotfix 维护分支（见第 10 节），它在生产升级到更新 minor 版本后删除。

## 3. 日常开发流程

内部开发者和外部贡献者统一遵循 PR 流程。

```text
内部开发者 / 外部贡献者
          |
          v
feature/*、fix/* 等开发分支
          |
          v
PR -> integration
          |
          v
CI + Maintainer Review
          |
          v
integration
```

### 内部开发者

从官方仓库最新的 `integration` 创建开发分支：

```bash
git fetch origin
git checkout integration
git pull origin integration

git checkout -b feat/my-feature
```

开发完成后推送分支，并创建目标为 `integration` 的 PR。

### 外部贡献者

通过 Fork 官方仓库，在个人仓库中创建开发分支，并向 `GroundedCoreAI/nexusdesk:integration` 提交 PR。

PR 描述应说明修改目的、影响范围、测试结果以及潜在兼容性影响。

具体开发环境、测试命令、模块边界、数据库迁移和文档要求，沿用仓库现有的 `CONTRIBUTING.md`、`AGENTS.md` 及对应模块开发规范。

## 4. PR 审核与风险控制

所有进入 `integration` 的代码必须通过必要的 CI 检查，并至少获得一名非 PR 作者的 Maintainer 审批。

CI 至少应覆盖：

- 后端 Ruff 检查、测试和数据库迁移验证。
- 前端国际化检查、TypeScript 检查和构建。
- 根据项目配置启用依赖漏洞、密钥泄露和代码安全扫描。

高风险改动应提高审核要求，尤其是：

- 认证、授权和权限边界。
- 数据库结构、Alembic 迁移和数据删除。
- `.github/workflows/`、`deploy/` 和生产配置。
- 外部 API、文件处理和敏感凭据。
- 运行时调度、任务队列及核心执行逻辑。

高风险改动必须由核心维护者专项审核；涉及核心安全边界、生产部署或破坏性迁移时，应由另一名核心维护者复核。

### 合并方式

- **日常开发 PR（目标为 `integration`）一律使用 squash merge**：把开发分支上的零散提交压缩为一条语义完整的提交进入 `integration`，保持集成分支历史线性、可读，并便于整体 revert。
- **`release/* → main` 的发布 PR 使用 merge commit**（不使用 squash、不使用 rebase）：保留发布候选过程的完整提交历史与签名链，使 `main` 上的每次发布都可追溯到 release 分支上的验证过程。
- Hotfix PR 的合并方式随其目标分支：提到 `main` 或维护分支时用 merge commit（保留修复过程），其余同步类 PR（如修复回 `integration`）用 squash merge。
- **`main` 要求 PR 分支 up-to-date**：合并任何进入 `main` 的 PR 之前，PR 分支必须已基于最新 `main` 更新（merge 或 rebase 同步），确保合并结果与已验证的发布候选一致。建议通过分支保护规则开启 "Require branches to be up to date before merging"。

### 不可信代码隔离

社区 PR 的 CI 必须遵循最小权限原则：

- 不向 Fork PR 提供生产 Secrets、SSH 密钥或生产部署权限。
- 不在可访问生产环境的自托管 Runner 上执行未经信任的代码。
- 避免在 `pull_request_target` 工作流中检出并运行外部提交的代码。
- CI 失败时不得自动绕过检查继续合并。
- CI 使用只读校验，不应通过自动修改代码来掩盖检查失败。

建议在关键目录配置 `CODEOWNERS`，使敏感路径修改自动要求指定维护者审核。

## 5. integration 与 main 的区别

`integration` 是开发集成区，不保证始终具备发布资格。

`main` 是发布准入通过的稳定代码基线，不接收日常功能 PR。

日常流程：

```text
开发分支
    |
    v
PR -> integration
    |
    v
持续集成与功能验证
    |
    v
准备发布时创建 release/vX.Y.Z
```

`main` 只通过发布候选 PR 或必要的 Hotfix 流程更新。

禁止直接 Push、Force Push，禁止通过管理员绕过常规准入要求。紧急情况应使用预先定义的 Hotfix 流程，而不是直接修改主干。

## 6. 发布候选验证

当 `integration` 中的功能达到发布条件时，创建临时 Release 分支：

```bash
git fetch origin
git checkout integration
git pull origin integration

git checkout -b release/v0.2.0
git push -u origin release/v0.2.0
```

`release/vX.Y.Z` 用于冻结本次发布范围和完成最终验证。

该分支只允许：

- 必要的 Bug Fix。
- 发布版本及中英文 CHANGELOG 更新。
- 必要的发布配置修正。
- 回归测试和兼容性修复。

不再加入新的大型功能。

发布候选必须完成：

1. 全部必需 CI 检查。
2. 后端、前端及关键业务流程回归。
3. 数据库迁移和兼容性验证。
4. 安全敏感改动复核。
5. 构建与部署冒烟测试。
6. 发布说明、已知限制和升级注意事项检查。

发布候选应在独立测试环境中验证，不能使用生产数据库、生产凭据或生产数据进行试运行。

如果只有一台服务器，可先使用本地或独立的 Docker Compose 测试环境；确实需要在同一主机验证时，应使用独立数据库、独立数据卷、独立配置和不同端口，不得覆盖生产环境。

验证失败时，修复并重新执行必要测试，不能因为某个版本已经创建了 Release 分支就降低准入标准。

## 7. 发布进入 main

发布候选验证全部通过后，创建 PR：

```text
release/v0.2.0 -> main
```

该 PR 必须：

- 通过发布所需的 CI 检查。
- 获得另一名核心维护者审批。
- 确认最终变更与已验证的发布候选一致。
- 确认 PR 分支已基于最新 `main` 更新（up-to-date）。
- 完成所有必要的 Review 问题处理。

**使用 merge commit 合并该 PR**（见第 4 节"合并方式"），保留发布过程与签名链。

合并后，等待合并后 Commit 的必需 CI 检查完成，再在最终的 `main` Commit 上创建正式 Tag。

```bash
git checkout main
git pull origin main

git tag -a v0.2.0 -m "Release v0.2.0"
git push origin v0.2.0
```

随后在 GitHub 创建对应 Release，记录功能变更、修复、已知限制和升级说明。

**发布候选的测试结果不能替代对最终发布 Commit 的验证。** 如果合并结果与已测试版本的代码不同，必须重新验证相关内容后才能创建正式 Tag。

Release 分支中的修复和发布文档变更，发布完成后必须同步回 `integration`，再删除临时分支。

## 8. 版本与 Tag 管理

正式版本采用 Semantic Versioning：

```text
v<MAJOR>.<MINOR>.<PATCH>
```

例如：

```text
v0.2.0
v0.2.1
v1.0.0
```

- `MAJOR`：不兼容的重大变更。
- `MINOR`：向后兼容的新功能。
- `PATCH`：向后兼容的 Bug 修复。

正式 Tag 创建后不得移动或覆盖。建议为 `v*` 配置独立的 GitHub Ruleset，限制正式版本 Tag 的删除和更新。

## 9. 部署管理

`main` 和 `integration` 均不得触发部署。

正式发布流程：

```text
integration
    |
    v
release/vX.Y.Z
    |
    v
完整验证与审批
    |
    v
main
    |
    v
正式 Release Tag
    |
    v
构建并发布指定版本制品
    |
    v
明确审批后部署
    |
    v
Production
```

部署工作流必须：

- 通过明确的版本输入或正式 Release 触发。
- 使用指定 Tag 对应的代码构建部署制品。
- 不默认使用 `main`、`integration` 或可变化的 `latest`。
- 通过 GitHub Environment 等机制控制生产部署审批。
- 部署后执行健康检查并记录实际运行版本。
- 保留必要的部署日志和回滚信息。

针对 NexusDesk 现有 `.github/workflows/deploy.yml`，需要取消由 `main` 的 CI 完成事件触发服务器部署的行为，并将部署目标改为指定版本。

### 环境区分

- `integration`：只进行集成验证，不部署生产。
- `release/*`：只进行发布候选验证，不覆盖生产。
- `main`：保存发布准入通过的稳定代码，不执行部署。
- `vX.Y.Z`：正式发布的版本依据。
- Production：只运行明确批准部署的正式版本。

NexusDesk 的 `deploy/quickstart` 用于快速体验，不应未经评估直接作为正式生产环境。正式业务环境应采用符合实际安全要求的 `deploy/production` 配置。

## 10. Hotfix 与回滚

生产环境出现严重问题时，先确认生产实际运行的版本 Tag，再从该版本（而非 `main` 最新代码）创建 Hotfix 分支：

```bash
git checkout v0.2.0
git checkout -b hotfix/v0.2.1
```

完成必要修复、测试和核心维护者审核后，构建并验证新版本。紧急修复可以在严格受控的情况下优先发布，但必须补齐 Review、测试和审计记录。

### 维护分支条款（生产版本落后于 main）

如果生产运行的不是最新版本（例如生产为 v0.2.0，而 `main` 已发布 v0.3.0），**不要**把 hotfix PR 直接提到 `main`——那会把 v0.3.0 的全部新功能一起带进生产。此时：

1. 从生产对应的旧 Tag 切出维护分支，作为 hotfix PR 的目标：

   ```bash
   git checkout v0.2.0
   git checkout -b release/v0.2.x
   git push -u origin release/v0.2.x
   ```

2. 从维护分支切 `hotfix/v0.2.1`，PR 目标为 `release/v0.2.x`，完成验证后在维护分支上打 Tag `v0.2.1` 并发布、部署。
3. 将修复 **cherry-pick 回 `main` 和 `integration`**，确保新版本线不再出现同一问题。
4. 维护分支（如 `release/v0.2.x`）在生产环境升级到更新的 minor 版本后删除；在此之前作为该 minor 版本线的 hotfix 基线保留。

Hotfix 发布后，必须通过 PR 或受控的提交同步方式，将修复同步回 `main` 和 `integration`，避免未来版本再次引入相同问题。

回滚可以恢复此前验证过的应用版本，但旧镜像不等于数据库回滚。对于数据库结构变更，必须单独评估向前兼容、数据备份及恢复方案。

## 11. GitHub 保护规则

为 `main` 配置 Ruleset：

- 必须通过 PR 合并。
- 必须有至少一名非作者 Maintainer 审批。
- 必需状态检查全部通过。
- 必须解决需要处理的 Review 问题。
- PR 分支必须 up-to-date（Require branches to be up to date before merging）。
- 禁止 Force Push 和删除。
- 尽量避免任何管理员绕过保护规则。

为 `integration` 配置 PR、CI、审批和禁止 Force Push 规则。

为 `v*` 配置正式 Tag 保护。

将仓库**默认分支设为 `integration`**，使新 PR 默认以 `integration` 为目标，避免社区贡献误提到 `main`。

通过 `CODEOWNERS` 保护 `.github/workflows/`、`deploy/`、数据库迁移和其他核心安全路径。

GitHub Ruleset 不能单凭"分支存在"就保证 `main` 只接收 Release PR；如需机器强制限制 PR 来源，应增加自动化检查，并将该检查设为必需状态检查。

## 12. 最终规则

| 项目 | 规范 |
|---|---|
| `main` | 稳定、发布准入通过的主干 |
| `integration` | 日常开发集成分支，**仓库默认分支** |
| 社区 PR | 默认提交至 `integration` |
| 内部开发 | 短生命周期分支 + PR |
| 临时分支 | 合并后立即删除（hotfix 维护分支除外） |
| 普通 PR | CI 通过 + Maintainer 审批 |
| 日常 PR 合并方式 | 目标 `integration`，一律 **squash merge** |
| 发布 PR 合并方式 | `release/* → main`，一律 **merge commit** |
| 进入 `main` 的 PR | 分支必须 up-to-date，核心维护者审批 |
| 高风险改动 | 核心维护者专项复核 |
| Release Candidate | 独立冻结、回归与部署验证 |
| `main` 更新 | 不触发任何部署 |
| 正式发布 | 正式 Tag + GitHub Release |
| Production | 只部署明确批准的 Release 版本 |
| Hotfix | 从生产实际运行版本切出；发布后同步回 `main` 和 `integration` |
| Hotfix 维护分支 | 生产落后 `main` 时，从旧 Tag 切 `release/vX.Y.x` 作为 hotfix 目标，生产升级后删除 |
| 回滚 | 单独评估数据库兼容性与备份恢复 |

**最终原则：社区代码先进入集成区，再经过独立发布准入才能进入 `main`；`main` 不承担部署职责，生产环境只部署经过明确批准的正式版本。**
