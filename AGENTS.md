# AGENTS.md — nexusdesk

本文件是所有 AI 编码 agent（Codex、Claude Code、DeepSeek harness、各类 agent harness）的统一项目入口。

## 项目概览

nexusdesk（后端包名 `customer-agent-platform` / `agent_platform`）：模块化 ReAct 客服 agent 平台。

| 目录 | 技术栈 |
|---|---|
| `backend/` | Python ≥3.11，FastAPI + SQLAlchemy 2.0(async) + Alembic + LangGraph，uv + ruff + pytest |
| `frontend/` | React 19 + TypeScript + Vite 7，antd v6 + i18next，Playwright e2e |
| `sdk/` | Python 与 JavaScript SDK |
| `deploy/`、`Dockerfile`、`compose.yaml`、`nexusdesk`、`nexusdesk.ps1` | Docker Compose 部署与启动脚本（sh + PowerShell） |

常用命令：

```bash
# 后端
cd backend && uv run ruff check src tests && uv run pytest
# 前端
cd frontend && npm run typecheck && npm run check:i18n && npm run build
# 本地起栈
sh nexusdesk quickstart
```

## Skills（重要）

本仓库的 agent skills 统一存放在 `.agents/skills/`（Claude Code 经符号链接 `.claude/skills` 访问同一份内容）。

**开始任务前先扫描下表；任务匹配某行时，必须先完整阅读对应的 SKILL.md 再动手：**

| Skill | 何时使用 |
|---|---|
| [groundedcoreai-python-backend-standards](.agents/skills/groundedcoreai-python-backend-standards/SKILL.md) | 编写/修改/审查 `backend/` 下 Python 代码（FastAPI、SQLAlchemy、Alembic、LangGraph） |
| [groundedcoreai-typescript-frontend-standards](.agents/skills/groundedcoreai-typescript-frontend-standards/SKILL.md) | 编写/修改/审查 `frontend/` 前端代码或 `sdk/javascript`（React、TS、i18n） |
| [groundedcoreai-shell-infra-standards](.agents/skills/groundedcoreai-shell-infra-standards/SKILL.md) | 编写/修改/审查 Shell/PowerShell 脚本、Dockerfile、docker compose 及 `deploy/` 配置 |

如果你的运行环境支持 Agent Skills 自动发现（如 Codex 扫 `.agents/skills/`、Claude Code 扫 `.claude/skills/`），直接按 skill 的 description 自动触发即可；否则按上表手动加载。

## 通用约定

- 提交前对应端的自检命令必须全绿（见各 SKILL.md 的"完成前自检清单"）
- **敏感信息（密码、密钥、Token 等）必须独立用环境文件维护**（`.env` 已 gitignore，仅提交 `.env.example` 占位模板），禁止写进任何代码、脚本或配置文件；各端细则见对应 SKILL.md 的"敏感信息管理"章节
- 用户可见的前端文案必须走 i18n，禁止硬编码
- 数据库改动必须配 Alembic 迁移，禁止手工改库
