# 本地开发

从仓库根目录开始执行。

## 启动服务

依赖：Python 3.11+、uv、PostgreSQL 16+，以及 Node.js 22.12+ 和 npm。已有 .env 请合并配置，不要覆盖。如需测试“查询演示”的 HTTP 工具，按 [Runtime 文档](runtime.md) 启动 mock_business 服务。

先启动 PostgreSQL（已有数据库可直接配置连接）：

```powershell
docker compose up -d postgres
```

在项目根目录新开终端一：

```powershell
cd backend
uv sync
if (!(Test-Path .env)) { Copy-Item .env.example .env }
uv run alembic upgrade head
uv run python -m agent_platform.apps.seed  # 可选：初始化示例知识库、Agent 和会话
uv run uvicorn agent_platform.apps.api.main:app --reload --host 127.0.0.1 --port 8000
```

在项目根目录新开终端二：

```powershell
cd frontend
npm ci
npm run dev
```

前端：http://127.0.0.1:5173；后端接口文档：http://127.0.0.1:8000/docs。
开发环境由 Vite 将 `/api` 代理到后端，不需要配置跨域。

知识库异步解析、分片和索引任务还需在后端目录另开终端运行：

```powershell
uv run python -m agent_platform.apps.worker.knowledge
```

## 验证

以下两组命令分别在项目根目录的新终端执行：

```powershell
cd backend
uv run pytest
uv run ruff check .
```

```powershell
cd frontend
npm run build
```

PostgreSQL 集成测试需配置指向独立、已迁移测试库的 `TEST_DATABASE_URL`；未配置时相关测试会跳过。

生产构建输出为 `frontend/dist`。部署时需要为 `/api` 配置反向代理；Vite 开发代理不包含在静态构建中。已提供基础服务令牌角色控制；个人账号、SSO 等生产扩展见平台文档。

独立 Runtime Worker 的启动与配置见 [Runtime 文档](runtime.md)。数据库迁移说明见 [迁移指南](database-migrations.md)。
