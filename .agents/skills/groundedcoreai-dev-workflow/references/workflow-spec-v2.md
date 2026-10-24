# NexusDesk 分支管理与 CI/CD 规范

Repository: `GroundedCoreAI/nexusdesk`

## 1. 核心原则

1. `main` 是稳定主干，保持现有 Branch Ruleset，不降低现有保护要求。
2. `integration` 是日常集成分支，优先保证集成和测试效率。
3. 所有常规代码变更通过 Pull Request 进入目标分支。
4. `integration` 不要求人工审批；必需 CI 通过后即可合并。
5. `integration` 每次有效更新并通过 CI 后，自动部署到测试服务器。
6. `integration` 阶段不创建 Git Tag，不创建 GitHub Release。
7. 正式发布仍采用 `release/*` → `main` → Tag → GitHub Release 的流程。
8. `main` 更新、正式 Tag 和 GitHub Release 均不触发服务器部署。

## 2. 分支管理

### 长期分支

| 分支 | 职责 | 部署 |
|---|---|---|
| `main` | 正式发布的稳定代码主干 | 不部署 |
| `integration` | 日常开发集成与测试 | CI 通过后自动部署到测试服务器 |

### 临时分支

| 分支 | 用途 |
|---|---|
| `feat/*` | 新功能开发 |
| `fix/*` | Bug 修复 |
| `refactor/*` | 代码重构 |
| `docs/*` | 文档修改 |
| `chore/*` | 工程维护 |
| `release/vX.Y.Z` | 正式发布前的冻结与验证 |

开发分支合并完成后及时删除。`release/*` 仅在准备正式发布时临时创建。

## 3. 日常开发流程

内部开发者从 `integration` 创建开发分支：

```bash
git fetch origin
git checkout integration
git pull origin integration

git checkout -b feat/my-feature
```

完成开发后：

```bash
git add .
git commit -m "feat: add my feature"
git push -u origin feat/my-feature
```

随后创建 PR，目标分支为 `integration`。

外部贡献者通过 Fork 官方仓库，在自己的开发分支完成修改后，向 `GroundedCoreAI/nexusdesk:integration` 提交 PR。

统一流程：

```text
内部开发者 / 外部贡献者
          |
          v
feat/* / fix/* / ...
          |
          v
PR -> integration
          |
          v
CI 通过后即可合并
          |
          v
integration
```

PR 仍用于追踪变更和讨论，但不要求人工审批。开发者应根据变更范围补充必要测试，涉及数据库、接口、安全或核心执行逻辑时，必须提供相应验证。

具体开发环境、测试命令、模块开发和双语文档要求以仓库现有 `CONTRIBUTING.md`、`AGENTS.md` 及模块规范为准。

## 4. integration 保护规则

`integration` 的保护以快速集成和避免误操作为目标，不采用严格的审批机制。

保留以下要求：

- PR 作为常规代码合并入口。
- 必需的 CI 检查通过。
- 禁止 Force Push。
- 禁止删除 `integration`。

调整以下要求：

- 必需审批人数设为 `0`。
- 不强制 Code Owner 审批。
- 不要求最后一次 Push 后再次审批。
- 不要求额外的人工审批。

必需的 CI 检查至少包括：

- Backend：Ruff 检查、后端测试、数据库迁移验证。
- Frontend：国际化检查、TypeScript 检查和构建。

CI 只做检查，不应通过自动修改代码来掩盖检查失败。

## 5. integration CI/CD

### 5.1 CI 触发条件

`.github/workflows/ci.yml` 继续覆盖：

- Pull Request 指向 `integration`。
- 代码 Push 到 `integration`。
- 正式发布流程所需的 `main` 检查。

PR 检查只负责验证，不触发服务器部署。

### 5.2 自动部署条件

`.github/workflows/deploy.yml` 应改为测试环境部署工作流：

- 只在 `integration` 的 Push 对应 CI 工作流成功后触发。
- 必须检查上游 CI 的结论为 `success`。
- 必须确认上游事件是分支 Push，而不是 PR 检查。
- 必须确认上游分支为 `integration`。
- 部署上游 CI 实际验证的 Commit SHA，不得改为拉取最新 `integration`。
- CI 失败、取消或没有运行时，不得部署。
- 不依赖人工审批，不创建 Git Tag。

部署流程：

```text
Push to integration
        |
        v
       CI
        |
        +---- Failed / Cancelled
        |           |
        |           v
        |        不部署
        |
        +---- Success
                    |
                    v
            构建指定 Commit 的镜像
                    |
                    v
              推送镜像仓库
                    |
                    v
              更新测试服务器
                    |
                    v
                健康检查
```

建议在 `deploy.yml` 中使用 `workflow_run` 监听 CI 完成事件，并同时校验上游工作流名称、分支、事件类型和执行结论，避免 PR CI 意外触发部署。

### 5.3 镜像版本

测试部署使用 Commit SHA 标识镜像，例如：

```text
nexusdesk-quickstart:971f2c8ca8a7
```

禁止将 `latest` 作为测试部署的唯一版本依据。

部署完成后记录实际 Commit SHA、部署时间和健康检查结果，便于定位问题以及重新部署历史版本。

### 5.4 部署凭据隔离

由于 `integration` 接受未经人工审批的代码，必须区分构建和部署权限：

- PR CI 不得访问部署凭据。
- 构建 Job 不得获取 SSH 部署密钥。
- 测试服务器凭据仅供独立的部署 Job 使用。
- 部署 Job 不应执行 PR 中的任意脚本。
- 部署工作流应从受保护的默认分支加载，并部署已经构建的指定 Commit 镜像。
- 测试环境不得访问生产数据库、生产凭据或其他生产资源。

## 6. 测试服务器规范

当前服务器作为测试服务器使用，部署目标为 `integration` 最新一次通过 CI 验证的版本。

现有 Quickstart 部署可以继续用于测试，但不能被视为正式生产环境。

必须遵守：

- 只使用测试数据、测试账号和测试凭据。
- 限制公网访问，优先使用 VPN、IP 白名单或可靠的认证入口。
- 不保存生产数据库数据和生产凭据。
- 允许通过后续 `integration` 提交更新测试版本。
- 测试失败时保留日志和失败信息，不得误报为部署成功。

测试服务器不要求保留每个集成版本，但必须能够查明当前运行的 Commit SHA。

## 7. 正式 Release 流程

正式发布流程保持不变，不受 `integration` 快速部署机制影响。

当准备发布时，从最新的 `integration` 创建 Release 分支：

```bash
git fetch origin
git checkout integration
git pull origin integration

git checkout -b release/vX.Y.Z
git push -u origin release/vX.Y.Z
```

发布准备包括：

1. 更新需要发布的版本号及锁文件。
2. 更新中英文 `CHANGELOG`。
3. 执行完整后端、前端和必要的集成验证。
4. 确认发布 Commit 对应的代码已通过检查。
5. 提交 PR：`release/vX.Y.Z` → `main`。

正式发布 PR 必须遵循 `main` 当前已有的保护规则，包括必需 CI、审批和 Code Owner 审核。使用现有允许的合并策略；发布流程默认使用 Merge Commit 保留发布候选的历史。

PR 合并后，确认最终 `main` Commit 的 CI 通过，再在该 Commit 上创建正式 Tag：

```bash
git checkout main
git pull --ff-only origin main

git tag -a vX.Y.Z -m "Release vX.Y.Z"
git push origin vX.Y.Z
```

然后在 GitHub 创建对应的正式 Release。

正式版本 Tag 创建后不得移动或覆盖。

**正式 Release 只负责记录和发布版本，不触发测试服务器部署。**

发布完成后，通过 PR 将发布相关的版本号、更新日志及其他必要变更同步回 `integration`，保留 `integration` 上已有的新开发和依赖更新。

## 8. main 与 Tag 保护规则

`main` 的现有 Ruleset 保持不变，不因为 `integration` 取消审批而降低正式主干的要求。

正式 Tag 继续使用现有 `Protect release tags` Ruleset，防止已发布版本被删除或修改。

正式版本与测试版本明确区分：

| 类型 | 标识 | 用途 |
|---|---|---|
| 集成版本 | Commit SHA | 测试服务器快速迭代 |
| 正式版本 | `vX.Y.Z` Tag | 正式发布与版本追踪 |

`integration` 不创建正式 Tag，不发布 GitHub Release。

## 9. 最终规则

| 项目 | 规范 |
|---|---|
| `main` | 保持现有保护规则 |
| `integration` | 快速集成，不要求审批 |
| 常规代码变更 | PR 合并 |
| `integration` CI | 必须通过后才能合并 |
| 测试服务器部署 | `integration` Push 对应 CI 成功后自动部署 |
| 集成阶段 Tag | 不创建 |
| 正式 Release | Release 分支 PR → `main` → Tag → GitHub Release |
| 正式 Release 部署 | 不自动部署到测试服务器 |
| 数据库迁移 | 必须测试，不能假设旧镜像可以自动回滚数据库 |
| 安全隔离 | 测试环境与生产环境彻底隔离 |

**最终原则：`integration` 负责快速集成、自动测试和自动部署；`main` 继续保持现有保护规则，负责正式版本发布；正式 Release 与测试服务器部署完全解耦。**
