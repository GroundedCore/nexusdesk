# NexusDesk Docker 部署

提供两容器快速体验与独立 Worker 生产拓扑。需要 Docker Engine / Docker Desktop（Linux 容器模式）与支持 `--wait` 的 Docker Compose v2。首次启动需要联网拉取镜像与构建依赖；后续可保留镜像离线启动。已在 Linux Docker 主机完成镜像构建与两套拓扑启动验证，范围见 [部署验证记录](validation-20260923.md)。

## 快速体验：无需模型密钥

在仓库根目录执行：

```sh
sh nexusdesk quickstart
```

Windows PowerShell：

```powershell
.\nexusdesk.ps1 quickstart
```

启动成功后访问 http://localhost:8080。仅启动 `app`、`postgres` 两个容器。数据库不映射宿主机端口，Web 固定绑定 `127.0.0.1`。不要将体验入口通过代理、端口转发或修改绑定地址暴露给其他人：体验代理会为未携带身份的请求自动注入本地服务令牌（admin 角色），任何能访问该入口的人都拥有完整管理权限。

应用容器使用 tini 与 Supervisor 管理 Nginx、API（含 Runtime Worker）、知识库 Worker；子进程自动重启，无法恢复的子进程失败将停止容器。首次执行数据库迁移、初始化主密钥、幂等导入演示数据；再次启动保留数据。部署自动初始化本地管理员 admin / nexusdesk，首次登录必须改密。Nginx 对未携带 Authorization 头的请求注入首次启动生成的本地服务令牌（admin 角色），已携带身份的请求原样透传。该令牌持久化保存在数据卷中，不写入前端资源或启动输出。

进入“会话工作台”选择 `sample-support`：

1. 输入“发货需要多久？”体验知识检索与工具结果。
2. 输入“帮我创建工单”查看待确认的工单建议，按界面确认后创建。
3. 通过人工接管入口体验协作。
4. 文档中心上传 TXT/Markdown/普通 DOCX 等，查看解析、分片，关联知识库并发布关键词索引。

页面持续显示“快速体验模式”。Demo 仅验证流程，不代表真实回答质量；不连接真实订单业务，不提供真实 OCR、向量检索或 GPU 推理。MinIO、Milvus、外部解析服务均不启动；原始文件仍存 PostgreSQL，非内存临时存储。体验模式默认单租户，不适合公开或多人生产使用。

```sh
# 状态与日志
 docker compose -f deploy/quickstart/compose.yaml ps
 docker compose -f deploy/quickstart/compose.yaml logs --tail=100 app
# 停止并保留数据
 docker compose -f deploy/quickstart/compose.yaml down
# 再次启动（已有镜像时无需重新构建）
 docker compose -f deploy/quickstart/compose.yaml up -d --wait
```

重置会删除所有体验数据，只在明确需要清空时手动执行 `docker compose -f deploy/quickstart/compose.yaml down -v`。修改入口端口可设置 `NEXUSDESK_PORT`。本地 PostgreSQL 密码仅限隔离的体验网络，不能复用于生产。

## 生产部署：复用外部中间件

```sh
cp deploy/production/.env.example deploy/production/.env
# 编辑 .env，替换数据库、角色 Token 等占位符，模型可在启动后通过界面配置
sh nexusdesk deploy
```

PowerShell 使用 `Copy-Item` 复制模板，再执行 `.\nexusdesk.ps1 deploy`。环境变量文件中的特殊字符按 Compose .env 规则引用；数据库 URL 的密码需要 URL 编码。不要把真实 .env、主密钥提交到代码仓库。

包含 Web、API、Runtime Worker、Knowledge Worker 四个常驻容器，共用后端镜像；另有一次性 migrate 服务。中间件由外部提供，不重复启动、不修改已有 PostgreSQL / Milvus / MinIO。脚本先构建镜像，停止应用服务，重新执行迁移与已配置存储连接检查，成功后启动全部服务；迁移失败不启动应用，不导入演示数据。

- PostgreSQL 必需；配置 `AGENT_DATABASE_URL`。
- 默认使用 `AGENT_MODEL_BACKEND=unconfigured`，无需模型密钥即可启动。界面提示添加供应商连接、Chat 模型和发布配置方案，再绑定到 Agent；未配置时对话明确返回配置提示，不降级 Demo。可选的旧版默认模型通过 `openai` 后端配置。
- 向量检索配置 Milvus 地址和凭据；仅关键词场景可以删除模板中的 Milvus 配置。
- 对象存储配置 S3 Endpoint、已有 Bucket、Access Key、Secret Key。迁移流程执行只读 `head_bucket` 检查，不自动创建或清空存储桶。不使用对象归档时删除整组 S3 配置。
- 扫描件 / 特殊格式解析服务通过 `AGENT_KNOWLEDGE_PARSER_URL` 按需配置。
- 后端不映射宿主机端口，Web 默认仍绑定 `127.0.0.1:8080`。由宿主机/已有入口代理终止 HTTPS；确认网络访问控制后再设置 `NEXUSDESK_BIND`。
- 生产不自动注入身份。全新部署使用 admin / nexusdesk 登录并首次改密；已有管理员不会被覆盖。服务令牌可通过登录页折叠入口使用，角色 Token 必须不同。企业 SSO 在开放平台配置。
- 模型、业务 API 域名必须列入相应白名单。API 与 Worker 读取相同的 .env，`AGENT_EMBEDDED_WORKER=false`。

### 主密钥与已有数据

凭据卷保存 `/data/credentials/master.key`，三个后端容器共享。全新空数据库允许创建密钥；已有 gateway/tool 加密凭据却缺失密钥时迁移初始化会失败，禁止生成替代密钥后继续。

复用当前数据库前，先将原来的 `backend/data/credentials/master.key` 放入生产凭据卷（不可重新生成）：

```sh
# 使用相同 Compose 项目名。目标卷由一次性服务创建。
docker compose --env-file deploy/production/.env -f deploy/production/compose.yaml build migrate
docker compose --env-file deploy/production/.env -f deploy/production/compose.yaml run --rm --no-deps --user root -v /absolute/path/to/master.key:/restore/master.key:ro migrate sh -c 'cp /restore/master.key /data/credentials/master.key && chown 10001:10001 /data/credentials/master.key && chmod 600 /data/credentials/master.key'
```

仅在新建/恢复卷时执行；不要覆盖正在使用的密钥。备份数据库时单独安全备份主密钥，以及外部 MinIO/Milvus 数据。更改租户 ID 不会迁移已有租户数据。

### 升级、扩容与排障

升级前备份数据库与密钥，再执行 `sh nexusdesk deploy`。该命令有应用停机窗口，当前不提供滚动升级或自动数据库回滚。旧镜像不能代替数据库备份。可使用 `NEXUSDESK_VERSION` 标记镜像版本；正式发布建议固定并验证基础镜像 digest。

```sh
docker compose --env-file deploy/production/.env -f deploy/production/compose.yaml ps
docker compose --env-file deploy/production/.env -f deploy/production/compose.yaml logs --tail=100 migrate api runtime-worker knowledge-worker
# 扩容前核算每进程数据库连接池、模型限流及资源占用
docker compose --env-file deploy/production/.env -f deploy/production/compose.yaml up -d --scale runtime-worker=2 --scale knowledge-worker=2
```

API 就绪检查为 `/api/v1/ready`；它不代表模型调用一定成功。Worker 暂无独立 HTTP 健康接口，需结合容器日志、租约与任务进度判断。Nginx 已关闭流式缓冲，代理读超时为 650 秒；代理允许 100 MB，请求仍受后端上传限制约束。

PostgreSQL 的连接预算应覆盖 API 和每个 Worker 的独立连接池。Milvus 内部推理/索引所需资源另行评估；生产 Compose 是部署拓扑，不等于高可用、SSO、监控、备份恢复等生产验收全部完成。

## 自动部署（GitHub Actions CI/CD）

`.github/workflows/deploy.yml` 在 main 分支 CI 通过后（或手动触发）自动部署 quickstart 单容器拓扑：Actions 构建 `quickstart` 镜像推送到 GHCR（`ghcr.io/<owner>/nexusdesk-quickstart:<commit-sha>`），再 SSH 登录服务器执行 `docker compose pull && up -d --wait`。容器引导自动执行数据库迁移，健康检查未通过则部署失败。

> **安全警告**：quickstart 模式会为未携带身份的请求注入 admin 角色的本地服务令牌，任何能访问该入口的人都拥有完整管理权限。服务器上保持默认的 `127.0.0.1` 绑定，仅通过 SSH 隧道（`ssh -L 8080:127.0.0.1:8080 user@server`）访问；不要把该端口暴露到公网或未经认证的反向代理之后。quickstart 使用演示模型（`AGENT_MODEL_BACKEND=demo`）、容器内嵌 PostgreSQL，定位是体验与小规模验证，不适合多人生产使用。

### 服务器一次性准备

```sh
# 1. 检出仓库（构建发生在 Actions，服务器只需 compose 文件）
git clone https://github.com/GroundedCore/nexusdesk.git /home/nexus/nexusdesk

# 2. GHCR 镜像包可见性独立于仓库：首次推送后 package 默认为私有（即使仓库
#    是 public）。在 GitHub Packages 页面将 nexusdesk-quickstart 设为 public
#    后可跳过本步（匿名即可拉取）；保持私有则需登录（PAT 需 read:packages 权限）
docker login ghcr.io -u <github-username> -p <pat>

# 3. 为 Actions 生成专用部署密钥（不要复用个人密钥）
ssh-keygen -t ed25519 -f ~/.ssh/nexusdesk_deploy -N ""
cat ~/.ssh/nexusdesk_deploy.pub >> ~/.ssh/authorized_keys
```

postgres 镜像仍从 Docker Hub 拉取；服务器访问 Docker Hub 受限时，设置 `POSTGRES_IMAGE` 指向可达镜像源（见下文“镜像与网络”）。

### GitHub 仓库配置

在 Settings → Secrets and variables → Actions 添加仓库级 secrets：`DEPLOY_HOST`、`DEPLOY_PORT`、`DEPLOY_USER`、`DEPLOY_SSH_KEY`（上面生成的私钥全文）。Actions 推送 GHCR 使用内置 `GITHUB_TOKEN`，无需额外配置。如需人工审批后再部署，可在 Settings → Environments 创建环境并在 workflow 的 job 上加 `environment:` 引用（引用不存在的环境会自动创建），将 secrets 迁移到环境作用域。

回滚到历史版本：在服务器上执行 `NEXUSDESK_VERSION=<旧commit-sha> NEXUSDESK_IMAGE_REGISTRY=ghcr.io/<owner>/ docker compose -f deploy/quickstart/compose.yaml up -d`（注意数据库迁移不支持自动回滚；数据保存在 `postgres_data` 与 `app_data` 卷中，回滚镜像不会丢失数据）。

## 镜像与网络

Dockerfile 的 `quickstart`、`backend`、`web` 为三个构建目标，前端由 Node 构建后交给 Nginx，不运行 Vite 开发服务器。Python 使用 `uv.lock`，前端使用 `package-lock.json`。`.dockerignore` 排除密钥、环境配置、数据库目录和本地依赖。

通过 `NODE_IMAGE`、`PYTHON_IMAGE`、`NGINX_IMAGE`、`POSTGRES_IMAGE` 覆盖基础镜像来源，例如 `docker.m.daocloud.io/library/python:3.12-slim-bookworm`。生产变量写入 .env；快速体验通过当前 shell 环境设置。镜像代理不代替 npm、PyPI 和 Debian 软件源，构建机仍需访问这些依赖源。若主机禁用了 Docker 默认 bridge，可设置 `NEXUSDESK_BUILD_NETWORK=host` 后构建；该配置只改变构建网络，不改变容器运行网络。

## 从体验升级

先停止体验应用并备份 PostgreSQL 数据及 app_data 卷中的主密钥，将数据库恢复到生产 PostgreSQL、密钥恢复到生产凭据卷，然后配置真实模型并启动生产栈。不要让两套应用同时使用迁移中的数据库。接入 Milvus 后可对已有文档生成向量并发布；对象存储归档的既有文件迁移需单独执行，配置 S3 不会自动搬迁历史文件。

默认管理员初始化 SQL：`backend/migrations/sql/0029_default_admin.sql`。迁移与容器引导自动执行，按部署租户幂等创建，不重置已有密码。
