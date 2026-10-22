---
name: groundedcoreai-commit-standard
description: 按 GroundedCoreAI 提交规范生成并执行 git commit。当用户要求提交代码、生成标准 commit message、用 AI 提交暂存改动、安装/修复 commit 校验 hook、校验 commit message 是否合规时使用。基于 Conventional Commits + GitHub issue 引用，message 由 agent 生成，脚本负责格式校验、内容绑定签名和执行 git commit。禁止调用其他工具查询 issue 内容，禁止核对代码改动与 issue 是否匹配。
---

# groundedcoreai-commit-standard

为当前 Git 仓库创建符合 GroundedCoreAI 规范的签名提交。agent 根据 staged diff
撰写 commit message；随附 Python 脚本负责确定性的校验、签名与 `git commit -F`。

## 必须遵守的行为

1. **只处理 staged 改动**。除非用户明确要求，不要执行 `git add`。
2. 生成 message 前，先从用户输入中识别 GitHub issue 信息：issue 链接、issue
   编号，或用户明确的"没有 issue"确认。
3. 用户没提供 issue 信息时，只问一次：

   ```text
   请提供本次提交关联的 GitHub issue 链接，优先直接复制 issue 页面地址。
   如果只有编号，也可以直接提供 issue 编号。
   如果没有关联 issue，请明确回复"没有 issue"。
   ```

4. 检查 staged diff，识别受影响的接口（路由/API 方法与路径、SDK 方法、事件、
   配置键等），写入正文。无接口受影响时写 `影响接口：无`。
5. 生成简洁、具体的 commit message（格式见下，细则见
   `references/commit-policy.md`）。
6. 用随附脚本完成校验、签名并提交。**hook 或脚本校验失败时禁止
   `--no-verify`**，修正 message 或 staged 状态后重试。

## Issue 信息边界

只使用用户提供的 issue 信息。**禁止**调用其他 skill、脚本、API、网络或浏览器
工具去查询 issue 内容；也**不要**核对 staged 代码与 issue 是否匹配——这由用户
负责。可以在本地解析用户给出的 issue URL 字符串，提取编号并保留原始 URL。

## Issue 解析规则

- GitHub issue 链接（`https://github.com/<owner>/<repo>/issues/<N>`）：取末尾
  数字为 issue 编号，原始 URL 保留在正文和脚本参数中。
- 纯编号：作为 `--issue-id`，`--issue-url` 传 `none`，脚本会从
  `remote.origin.url` 推导完整 URL。
- 用户确认没有 issue：`--issue-id none-confirmed --issue-url none`，subject
  引用段用 `--no-issue`。

## Commit message 格式

Subject：

```text
<type>(<scope>): <issue-ref> <summary>
```

- `type`：`feat` `fix` `docs` `style` `refactor` `perf` `test` `build` `ci`
  `chore` `revert` 之一
- `scope`：必填，小写字母/数字/`.`/`_`/`-`，用主要受影响模块（nexusdesk 常
  用：`backend` `frontend` `memory` `knowledge` `conversation` `deploy` `sdk`
  `deps` `docs` `repo`）
- `issue-ref`：`--issue=<编号>` 或 `--no-issue`
- `summary`：中英文皆可，必须具体、不以标点结尾、禁用空泛措辞（如"修复
  bug""update code"）

Body：

- **必须有正文**，自然段落（非 bullet 列表），每行 ≤120 字符
- 有 issue URL 时，正文首行（空行后）为 `Issue: <原始URL>`
- **严格类型**（`feat`/`fix`/`perf`/`refactor`）必须四段式，空行分隔：
  `影响接口：…` → 为什么改 → 改了什么核心逻辑 → 风险/兼容性
- 其余类型正文可简短，但必须具体说明改了什么

## 文档双语要求（强制）

凡涉及 `docs/` 目录下文档的新增或修改（含 `docs` type 提交），**必须同时维
护中英文对照两个版本**（遵循仓库现有命名约定，如 `xxx.md` + `xxx.en.md`），
并在同一次提交中一并提交。agent 修改 `docs/` 文档时如发现只改了一个语言版
本，必须先补齐另一语言版本再提交。

## 脚本用法

```bash
python scripts/commit_standard.py commit \
  --message-file <message-file> \
  --issue-id <编号|none-confirmed> \
  --issue-url <url|none>
```

`commit` 命令依次执行：

1. 确保项目根 `AGENTS.md`（或 `CLAUDE.md`）提及本 skill（不存在才追加，幂
   等，且不写入任何本地绝对路径）
2. 校验 message 格式、绑定 staged 内容（tree / parents / diff-base /
   diff-sha256）并 HMAC 签名，写入 `GroundedCore-Commit-*` trailers
3. 在签名后追加 `Co-Authored-By: Claude Code <noreply@anthropic.com>`（不占
   签名内容，校验时会自动忽略该行）
4. `git commit -F <签名后的 message 文件>`

维护命令：

```bash
python scripts/commit_standard.py install-hook          # 显式安装强制 hook
python scripts/commit_standard.py verify --commit-msg <message-file>
python scripts/commit_standard.py verify-commit --commit <commit-sha>
```

## Hook 是可选的（默认不强制）

默认流程**只做校验+签名+提交**，不改动仓库 hook。需要仓库级强制（之后所有
`git commit`——包括手写——都必须合规）时：

- 用户明确要求后，用 `commit --enforce` 或单独跑 `install-hook`
- 已有 `commit-msg` hook 会链式执行；无法安全链式时脚本会停下，需用户确认
  后才能 `--enforce-hook` 覆盖
- 安装后 `groundedcore.commit.required=true`，任何提交都会被 `commit-msg`
  hook 复核

## HMAC key

签名 key 默认内置（脚本中明确标注"非安全边界"，仅提高手写伪造门槛）。团队
正式使用时通过环境变量覆盖（符合仓库敏感信息红线，key 不入库）：

```bash
export GCA_COMMIT_HMAC_KEY=<team-secret>   # 写入 backend/.env 或 shell profile
```

注意：更换 key 后，旧 key 签名的历史提交用 `verify-commit` 会验签失败，属预
期行为。

## 失败处理

- 无 staged 改动：提示用户先 `git add`
- 校验失败：展示原因，修正 message 或 staged 状态后重试，禁止 `--no-verify`
- issue URL 无法从 remote 推导：请用户提供完整 issue 链接
