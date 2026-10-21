---
name: groundedcoreai-python-backend-standards
description: nexusdesk 后端 Python 代码规范与最佳实践检查。当编写、修改或审查 backend/ 下的 Python 代码（FastAPI 路由、SQLAlchemy 模型、Alembic 迁移、LangGraph agent、服务层逻辑）时使用；也用于 code review 时核对后端规范。不适用于前端或 SDK 代码。
---

# nexusdesk 后端 Python 开发规范

适用范围：`backend/`（包名 `agent_platform`，src-layout：`backend/src/agent_platform/`）。

## 技术栈基线

- Python **>= 3.11**，充分利用新语法（`X | Y` 联合类型、`match`、`Self` 等）
- FastAPI + Pydantic v2（pydantic-settings 做配置）
- SQLAlchemy **2.0 风格** + asyncpg（全异步），Alembic 迁移
- LangGraph / langchain-openai 构建 agent 运行时
- 依赖管理：**uv**（`pyproject.toml` + `uv.lock`），不要手改 `requirements.txt` 而不同步 pyproject
- Lint：**ruff**，`line-length = 100`，`target-version = py311`
- 测试：pytest + pytest-asyncio，测试目录 `backend/tests/`

## 架构分层约定

```
agent_platform/
├── modules/        # 业务模块（memory、knowledge、conversation、tool_gateway 等）
├── platform/       # 平台能力（identity、messaging、persistence、secrets、storage）
├── apps/           # 应用装配 / 脚本入口（如 seed_memory_*.py）
├── integrations/   # 外部系统集成
└── settings.py     # pydantic-settings 配置
```

- 新业务能力优先放进 `modules/` 下的对应域模块；跨域基础设施放 `platform/`
- 禁止模块间反向依赖：`platform/` 不得 import `modules/`
- 配置一律走 `settings.py` 的 pydantic-settings，**禁止**在业务代码里直接 `os.environ[...]`

## 代码风格硬性规则

1. **类型注解全覆盖**：所有公开函数/方法的参数与返回值必须有类型注解；用 `X | None` 而非 `Optional[X]`
2. **全异步 I/O**：数据库访问、HTTP 调用（httpx.AsyncClient）一律 async；禁止在 async 上下文中调用阻塞 I/O（如 `requests`、同步文件读写大文件）
3. **SQLAlchemy 2.0 风格**：用 `select()` + `await session.execute(...)`，禁止 legacy `Query` API；模型用 `Mapped[...]` + `mapped_column` 声明
4. **Pydantic v2**：用 `model_config = ConfigDict(...)`、`field_validator`，禁止 v1 的 `class Config` 和 `@validator`
5. **异常处理**：捕获具体异常类型，禁止裸 `except:`；API 层统一转 HTTPException，业务层抛领域异常
6. **日志**：用 `logging.getLogger(__name__)`，禁止 `print` 进生产代码
7. 行宽 100，import 排序交给 ruff

## 数据库与迁移

- 模型改动**必须**配套 Alembic 迁移（`backend/migrations/`），禁止手工改库
- 迁移文件生成后必须人工 review：检查 `op.*` 操作、索引、可逆性（`downgrade` 不能是 `pass`）
- 迁移 revision 号不要随意改；若 alembic_version 失效，用 restamp 基线 → upgrade 缺失段 → stamp 新号 的方式修复（无损保留数据），不要 drop 重建
- 集成测试需要真实 Postgres 时打 `@pytest.mark.postgres` 标记，依赖 `TEST_DATABASE_URL`

## 敏感信息管理（红线）

涉及密码、密钥、Token、连接串等敏感信息的开发与维护，**必须独立使用环境文件方式管理**：

1. **禁止硬编码**：敏感信息不得以任何形式出现在代码、注释、测试fixture、迁移脚本、默认值中
2. **统一入口**：代码只通过 `settings.py` 的 pydantic-settings 读取；运行时密钥解密走 `platform/secrets`；禁止在业务代码里直接 `os.environ[...]`
3. **环境文件**：本地开发用 `backend/.env`（已被 .gitignore），新增配置项时**必须同步在 `backend/.env.example` 添加占位符**（键名 + 说明注释，值留空或写 `changeme`）
4. **禁止入库**：`.env`、`.env.*` 不得提交 git（仓库 .gitignore 已配置 `.env` / `.env.*`，仅放行 `!.env.example`）；提交前自查 `git status` 无 env 文件
5. **禁止外泄**：敏感信息不得打印到日志、异常消息、API 响应；日志中对凭据类字段必须脱敏
6. SQL 一律参数化（SQLAlchemy 默认即安全），禁止 f-string 拼 SQL
7. 对外 API 输入必须经 Pydantic 模型校验，禁止信任 `dict` 入参直接落库

## 完成前自检清单

```bash
cd backend
uv run ruff check src tests      # lint 必须全绿
uv run ruff format --check src   # 格式
uv run pytest                    # 测试通过
```

提交前以上三条必须全部通过；若改了模型，确认迁移文件已生成且 `uv run alembic upgrade head` 在干净库上可跑通。
