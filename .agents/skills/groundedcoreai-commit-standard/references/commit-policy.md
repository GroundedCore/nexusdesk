# GroundedCoreAI Commit Policy

## Issue 数据边界

只使用用户提供的 GitHub issue 信息。禁止调用其他 skill、脚本、API、网络或
浏览器工具查询 issue 内容。用户负责确保代码改动与 issue 匹配；本 skill 只负
责格式化、签名和校验 commit message。

## 项目指引文件

通过本 skill 提交时，确保项目根指引文件提及 `groundedcoreai-commit-standard`：

- 根目录有 `AGENTS.md` 则优先追加到其中（已提及则跳过，幂等）
- 没有 `AGENTS.md` 但有 `CLAUDE.md` 时追加到 `CLAUDE.md`
- 两者都没有时创建 `AGENTS.md`
- 插入的文本只提 skill 名称，**禁止**包含本地绝对路径

## Subject

格式：

```text
<type>(<scope>): <issue-ref> <summary>
```

允许的 `type`：

```text
feat
fix
docs
style
refactor
perf
test
build
ci
chore
revert
```

`scope` 必填，取主要受影响模块。nexusdesk 常用值：`backend`、`frontend`、
`memory`、`knowledge`、`conversation`、`channel`、`agent-runtime`、`deploy`、
`sdk`、`deps`、`docs`、`repo`。

`issue-ref` 二选一：

```text
--issue=<GitHub issue 编号，1-7 位数字>
--no-issue
```

`summary` 中英文皆可，必须具体，不以 `。 . ！ ! ？ ?` 结尾。

Valid examples：

```text
feat(memory): --issue=42 新增记忆种子数据多语言注入脚本
fix(conversation): --issue=108 修复会话流式消息重试后顺序错乱的问题
refactor(backend): --no-issue 调整认证模块 JWT 校验逻辑以复用统一异常处理
docs(docs): --no-issue 补充部署指南中英文对照版本
build(deps): --no-issue bump certifi from 2026.7.22 to 2026.10.19
```

Invalid examples：

```text
fix: 修复bug                                  # 缺 scope、缺 issue-ref、summary 空泛
chore: update code                            # 缺 scope、缺 issue-ref、summary 空泛
feat(auth): support token refresh             # 缺 issue-ref
fix(user): 修复手机号参数未做格式校验。          # 缺 issue-ref，且 summary 以标点结尾
feat(memory): --issue=abc 新增种子脚本          # issue 编号必须是数字
```

## Body

正文必填。自然段落，不用 bullet 列表；真实换行，每行 ≤120 字符。

提供了 issue URL 时，正文第一段（subject 空行之后）必须是：

```text
Issue: https://github.com/GroundedCoreAI/nexusdesk/issues/42
```

### 严格类型（feat / fix / perf / refactor）

必须用四个空行分隔的自然段：

```text
影响接口：<受影响的接口>

<为什么需要这次改动>

<改了什么核心逻辑>

<风险 / 兼容性影响>
```

- 第一段必须以 `影响接口：` 开头，列出受影响的 HTTP 路由、RPC、SDK 方法、
  事件或其他对外接口；超过 5 个时只列最重要的 5 个并注明总数；无接口受影
  响时写 `影响接口：无`
- 路由、API、RPC、SDK 方法、事件、配置键、响应结构有变化时，必须提及影响
  面最大的项
- 禁止 `优化代码`、`调整逻辑`、`完善功能` 这类空洞描述

### 非严格类型（docs / style / test / build / ci / chore / revert）

正文可简短为一段，但必须具体说明改了什么、为什么，禁止空泛描述。

### 文档双语要求（强制）

凡涉及 `docs/` 目录文档的提交，必须同时包含中英文对照两个版本的改动（遵循
仓库现有命名约定，如 `xxx.md` + `xxx.en.md`）。只改一个语言版本的提交不合
格；agent 应先补齐另一语言版本再提交。根目录的 `README*.md`、`CHANGELOG*`
同样适用。

## Full Examples

```text
fix(conversation): --issue=108 修复会话流式消息重试后顺序错乱的问题

Issue: https://github.com/GroundedCoreAI/nexusdesk/issues/108

影响接口：POST /api/conversations/{id}/messages:retry

流式消息重试时未携带原序号，导致前端渲染顺序错乱，影响用户阅读历史消息。

重试请求补充消息序号，后端按序号重排后返回。

向下完全兼容，无需前端配合改造。
```

```text
refactor(backend): --no-issue 抽离商品查询公共工具类

影响接口：无

多处业务模块重复编写商品查询、组装逻辑，代码冗余且后续维护成本高。

抽离通用方法至公共工具类，统一入参出参结构。

仅内部代码重构，对外接口、返回字段完全不变。
```

```text
docs(docs): --no-issue 补充快速开始指南英文版本

快速开始指南此前只有中文版，海外协作者无法阅读。

按仓库双语约定新增 quickstart.en.md，内容与中文版逐节对应。

纯文档变更，无代码与接口影响。
```
