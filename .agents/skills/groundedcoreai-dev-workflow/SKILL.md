---
name: groundedcoreai-dev-workflow
description: GroundedCoreAI/nexusdesk 开发流程操作指南。当需要创建分支（feat/fix/release/hotfix 等）、开 Pull Request、决定 PR 提到哪个分支、选择合并方式、发布版本（release 分支、打 tag、GitHub Release）、处理生产 hotfix、或执行/评估部署（integration CI 通过后自动部署测试服务器；main/tag/release 不部署）时使用。只管"提到哪个分支、走什么流程、何时发布/部署"；commit message 怎么写归 groundedcoreai-commit-standard 管。
---

# groundedcoreai-dev-workflow

nexusdesk（`GroundedCoreAI/nexusdesk`）的分支管理、PR、发布、hotfix 与部署流程。
**当前生效规范蓝本是 `references/workflow-spec-v2.md`（分支管理与 CI/CD 规范 V2）**，
本文件是 agent 实操速查版。`references/workflow-spec.md`（V1）已被 V2 取代，仅作历史参考。

## 分工边界

- **本 skill 管**：分支怎么切、PR 提到哪、用什么方式合并、何时发布/打 tag/部署。
- **commit message 怎么写**（格式、issue 引用、签名提交）：走
  `groundedcoreai-commit-standard`，本 skill 不重复规定。

## 分支模型速查表

仓库**默认分支是 `integration`**（不是 `main`）。所有日常 PR 的默认目标都是
`integration`；只有发布候选和 hotfix 才能进入 `main`。

| 分支 | 职责 | 部署 | PR 目标 | 合并方式 |
|---|---|---|---|---|
| `main` | 正式发布的稳定代码主干 | **不部署** | — （只接收 release/hotfix PR） | — |
| `integration` | 日常开发集成与测试 | **CI 通过后自动部署到测试服务器** | — | — |
| `feat/*` `fix/*` `refactor/*` `docs/*` `chore/*` | 日常开发短分支 | — | `integration` | **squash merge**（现有约定） |
| `release/vX.Y.Z` | 正式发布前的冻结与验证 | — | `main` | **merge commit**（保留发布过程与签名链） |
| `hotfix/*` | 生产紧急修复 | — | 生产对应版本线（见 Hotfix 节） | 视目标分支而定 |

要点：

- 临时分支（`feat/*` … `hotfix/*`、`release/*`）**合并后立即删除**。`release/*` 仅在准备正式发布时临时创建。
- `main` 要求 PR 分支 **up-to-date**（合并前必须基于最新 `main` 更新），并维持现有严格保护（审批 + Code Owner + 必需 CI），不得因 `integration` 放宽而降低。
- 禁止直接 push / force push 到 `main` 和 `integration`，禁止删除这两个分支；一切改动走 PR。
- **`integration` 不要求人工审批**：必需审批人数为 0、不强制 Code Owner 审批、不要求最后一次 push 后再次审批。必需 CI 检查通过即可合并。
- 社区（fork）PR 一律提到 `integration`，不得直接进 `main`。
- `integration` 阶段**不创建 Git Tag、不创建 GitHub Release**；集成版本以 commit SHA 标识，正式版本以 `vX.Y.Z` Tag 标识。

## 日常开发流程

1. 从最新 `integration` 切分支：

   ```bash
   git fetch origin
   git checkout integration && git pull origin integration
   git checkout -b feat/my-feature   # 或 fix/ refactor/ docs/ chore/
   ```

2. 开发、自检（对应端 lint/test/build 全绿），提交走
   `groundedcoreai-commit-standard`。
3. 推送分支，创建目标为 **`integration`** 的 PR。PR 描述说明修改目的、影响
   范围、测试结果与兼容性影响。涉及数据库、接口、安全或核心执行逻辑时，必须
   提供相应验证。
4. **必需 CI 检查全部通过后即可用 squash merge 合并**（`integration` 不要求
   人工审批）。PR 仍用于追踪变更与讨论；高风险改动（见下）仍应主动请核心维护
   者复核。
5. 合并后删除开发分支。合并进入 `integration` 的 push 在 CI 通过后会**自动部
   署到测试服务器**（见"部署规则"节），无需人工干预。

## 发布流程

1. 从最新 `integration` 切 `release/vX.Y.Z` 并推送：

   ```bash
   git fetch origin
   git checkout integration && git pull origin integration
   git checkout -b release/v0.3.0
   git push -u origin release/v0.3.0
   ```

2. release 分支上只允许：必要 bug fix、版本号与中英文 CHANGELOG 更新、发布
   配置修正、回归/兼容性修复。**不再加入新功能**。
3. RC 验证清单（全部通过才可进入下一步）：
   - 全部必需 CI 检查通过；
   - 后端、前端及关键业务流程回归；
   - 数据库迁移与兼容性验证；
   - 安全敏感改动复核；
   - 构建与部署冒烟测试（独立测试环境，**禁用生产数据库/凭据/数据**；
     只有一台服务器时用独立数据库、独立数据卷、独立配置、不同端口）；
   - 发布说明、已知限制、升级注意事项检查（中英双语）。
4. 创建 `release/vX.Y.Z → main` 的 PR，通过 CI + 另一名核心维护者审批后，
   用 **merge commit** 合并（不用 squash，保留发布过程与签名链）。合并前
   确认 PR 分支已 up-to-date 且最终变更与已验证的 RC 一致。
5. 合并后**等待 main 上合并 commit 的 CI 完成**，再在该 commit 上打
   annotated tag 并推送：

   ```bash
   git checkout main && git pull origin main
   git tag -a v0.3.0 -m "Release v0.3.0"
   git push origin v0.3.0
   ```

   若合并结果与已测试的 RC 代码不同，必须重新验证后才能打 tag。
6. 在 GitHub 创建对应 Release（功能变更、修复、已知限制、升级说明，中英
   双语）。**`main` 更新、正式 Tag 和 GitHub Release 均不触发任何服务器部
   署**——正式 Release 只负责记录和发布版本，与测试服务器部署完全解耦。
7. 通过 PR 把 release 分支上的版本号、中英文 CHANGELOG 及其他必要变更**同
   步回 `integration`**（保留 `integration` 上已有的新开发和依赖更新），然后
   删除 `release/vX.Y.Z` 分支。
8. 正式 tag 一旦推送**不得移动或覆盖**（由现有 `Protect release tags`
   Ruleset 保护）。

## Hotfix 流程

> V2 规范未覆盖 hotfix 场景，本节延续 V1 约定，与 V2 不冲突。

生产出现严重问题时：

1. **确认生产实际运行的版本 tag**（不是 main 最新版）。
2. 分两种情况：
   - **生产跑的就是 main 最新版本**：从该 tag 切 `hotfix/vX.Y.Z+1`：

     ```bash
     git checkout v0.3.0
     git checkout -b hotfix/v0.3.1
     ```

     修复、测试、核心维护者审核后，PR 到 `main`，合并打 tag `v0.3.1`。
   - **维护分支条款（生产落后于 main）**：如生产跑 v0.2.0 而 main 已到
     v0.3.0，**不要**把 hotfix 提到 main。从旧 tag 切维护分支作为 hotfix
     PR 的目标：

     ```bash
     git checkout v0.2.0
     git checkout -b release/v0.2.x   # 维护分支
     git push -u origin release/v0.2.x
     git checkout -b hotfix/v0.2.1    # 从维护分支切
     ```

     hotfix PR 提到 `release/v0.2.x`，在其上验证、合并并打 tag `v0.2.1`、
     发布。修复随后 **cherry-pick 回 `main` 和 `integration`**。维护分支
     `release/v0.2.x` 在生产升级到更新的 minor 版本后删除。
3. 紧急修复可在严格受控下优先发布，但必须补齐 Review、测试与审计记录。
4. 无论哪种情况，修复都必须同步回 `main` 和 `integration`，避免未来版本
   重新引入同一问题。
5. 回滚只恢复应用版本；数据库结构变更必须单独评估向前兼容、备份与恢复
   方案——旧镜像不等于数据库回滚。

## 部署规则

- `main` 更新、正式 Tag、GitHub Release **均不触发任何部署**。
- `integration` push 且其对应 CI 工作流**成功**后，自动部署到**测试服务器**
  （`.github/workflows/deploy.yml`，`workflow_run` 触发）。部署工作流必须：
  - 校验上游工作流名称、上游分支为 `integration`、上游事件为 **push**
    （不是 PR 检查）、上游结论为 **success**；CI 失败/取消/未运行 → 不部署；
  - 部署上游 CI 实际验证的 **commit SHA**，不得改为拉取 `integration` 最新；
  - 镜像以 commit SHA 为 tag（如 `nexusdesk-quickstart:971f2c8ca8a7`），
    **禁止把 `latest` 作为测试部署的唯一版本依据**；
  - 不依赖人工审批、不创建 Git Tag、不创建 GitHub Release；
  - 部署后执行健康检查，并记录实际部署的 commit SHA、时间与结果；测试失败时
    保留日志，不得误报为部署成功。
- 当前服务器是**测试服务器**：只放测试数据/账号/凭据，限制公网访问（VPN、
  IP 白名单或可靠认证入口），不保存生产数据与生产凭据，必须能查明当前运行的
  commit SHA。
- **凭据隔离**（因 `integration` 接受未经人工审批的代码，必须遵守）：
  - PR CI 不得访问部署凭据；构建 job 不得获取 SSH 部署密钥；
  - 测试服务器凭据仅供独立的 deploy job 使用（GitHub Environment `testing`，
    不配置人工审批人）；
  - deploy job 不执行 PR 中的任意脚本；`workflow_run` 使部署工作流从受保护的
    默认分支加载，PR 代码改不了部署逻辑；
  - 测试环境不得访问生产数据库、生产凭据或其他生产资源。
- 数据库迁移必须测试；旧镜像不等于数据库回滚，回滚需单独评估兼容性与备份。

## 不可信代码（fork PR）注意事项

- 不向 fork PR 提供任何部署凭据、SSH 密钥或服务器访问权限。
- 不在可访问生产环境的自托管 Runner 上执行未信任代码。
- 避免在 `pull_request_target` 工作流中检出并运行外部提交的代码。
- CI 失败不得自动绕过合并；CI 只做只读校验，不得自动改代码掩盖失败。

## 高风险改动（提示人工专项复核）

遇到以下改动，必须提示需要核心维护者专项复核（涉及核心安全边界、生产部
署或破坏性迁移时需另一名核心维护者复核）：

- 认证、授权和权限边界；
- 数据库结构、Alembic 迁移和数据删除；
- `.github/workflows/`、`deploy/` 和生产配置；
- 外部 API、文件处理和敏感凭据；
- 运行时调度、任务队列及核心执行逻辑。

## 参考

- **当前生效规范**：`references/workflow-spec-v2.md`（NexusDesk 分支管理与
  CI/CD 规范 V2）
- 历史版本：`references/workflow-spec.md`（V1，已被 V2 取代，仅作对照参考）
- commit message 规范：`.agents/skills/groundedcoreai-commit-standard/SKILL.md`
