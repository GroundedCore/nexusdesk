---
name: groundedcoreai-shell-infra-standards
description: nexusdesk 基础设施脚本与配置规范。当编写、修改或审查 Shell 脚本（nexusdesk 启动脚本、deploy/ 下脚本）、PowerShell（nexusdesk.ps1）、Dockerfile、docker compose 文件（compose.yaml、deploy/**/compose.yaml）及部署 YAML 配置时使用。
---

# nexusdesk Shell / Infra 开发规范

适用范围：仓库根 `nexusdesk`（POSIX sh）、`nexusdesk.ps1`、`Dockerfile`、`compose.yaml`、`deploy/`（quickstart / production / container / knowledge）。

## Shell 脚本规范（`nexusdesk` 及 deploy 脚本）

1. **shebang 用 `#!/bin/sh`（POSIX）**，不用 bashism；必须 bash 特性时才用 `#!/usr/bin/env bash` 并注明原因
2. 开头固定 `set -eu`（错误即退出、未定义变量即报错）；管道场景酌情加 `set -o pipefail`（bash 下）
3. **所有变量引用加引号**：`"$ROOT"`，禁止裸 `$ROOT` 路径拼接
4. 路径解析用项目现有惯用法：
   ```sh
   ROOT=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
   ```
5. 外部依赖先探测再使用，并给可读错误：
   ```sh
   command -v docker >/dev/null 2>&1 || { echo "Install Docker first." >&2; exit 1; }
   ```
6. 错误信息输出到 **stderr**（`>&2`），用法错误 `exit 2`，环境缺失 `exit 1`
7. 用户可见输出要双语友好、给出下一步指引（参照现有 `nexusdesk` 脚本的 echo 风格）
8. macOS / Linux 都要能跑：避免 GNU-only 参数（如 `sed -i` 无参、`readlink -f`）

## PowerShell 规范（`nexusdesk.ps1`）

- 与 `nexusdesk` sh 脚本**功能对齐**：新增子命令时两边都要加
- 脚本开头 `$ErrorActionPreference = 'Stop'`
- 兼容 Windows PowerShell 5.1 与 PowerShell 7+，避免 7-only 语法

## Dockerfile 规范

- 基础镜像**固定 digest 或具体 tag**，禁止 `latest`
- 多阶段构建：构建期依赖不进最终镜像
- 非 root 用户运行；`EXPOSE` 与实际服务端口一致
- 镜像内不烤入密钥；配置一律经环境变量/挂载注入

## 敏感信息管理（红线）

1. **禁止硬编码**：密码、密钥、Token、证书私钥等敏感信息**必须独立使用环境文件维护**——禁止写进 compose.yaml、Dockerfile、Shell/PowerShell 脚本、YAML 配置或注释中
2. **环境文件**：生产部署用 `deploy/production/.env`（compose 经 `--env-file` 加载，已被 .gitignore）；新增可配项时**必须同步在对应 `.env.example` 添加占位符**（键名 + 说明注释，值留空或写 `changeme`）
3. **禁止入库**：`.env`、`.env.*` 不得提交 git（仓库 .gitignore 仅放行 `!.env.example`）；`nexusdesk` 脚本已有 `.env` 缺失即报错退出的检查，新增脚本要保持这个模式
4. compose/YAML 中引用敏感值一律用 `${VAR}` 插值，不写字面量
5. 脚本输出不得回显敏感变量值；错误信息中也不得包含凭据

## Docker Compose / 部署 YAML 规范

- 两套环境：`deploy/quickstart/compose.yaml`（演示，自签名证书）与 `deploy/production/compose.yaml`（生产，`--env-file`）；**改服务定义时检查两边是否都要同步**
- service 必须配 `healthcheck`（quickstart 依赖 `--wait` 启动），启动顺序用 `depends_on: condition: service_healthy`
- 端口、凭据等可配项走环境变量 + `.env.example` 模板，**禁止把真实凭据提交进仓库**
- 新增服务时：quickstart、production、`nexusdesk`/`nexusdesk.ps1` 脚本三处都要评估是否受影响

## 数据库迁移与容器

- 迁移通过独立 `migrate` 服务跑（参照 production 的 `--exit-code-from migrate` 模式），禁止在应用容器启动命令里隐式 `alembic upgrade`
- 容器内手动跑 alembic 时需先注入 `AGENT_API_TOKEN` 等必要环境变量
- `docker cp` 向容器拷目录时注意嵌套层级（拷目录本身 vs 拷内容），拷完验证目标路径

## 完成前自检清单

```bash
sh -n nexusdesk                     # sh 语法检查
shellcheck nexusdesk deploy/**/*.sh # 有 shellcheck 时
docker compose -f deploy/quickstart/compose.yaml config -q    # compose 配置校验
docker compose -f deploy/production/compose.yaml config -q
```

改了 compose/Dockerfile 后，至少验证 `sh nexusdesk quickstart` 能完整起栈（或说明未验证的原因）。
