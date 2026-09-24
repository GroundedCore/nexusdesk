# Docker 部署验证记录

验证日期：2026-09-23。服务器：`10.0.11.112`，Docker `26.1.4`，Compose `v2.27.1`。

## 验证环境

- 源码与构建日志：`/opt/nexusdesk-validation-20260923`。
- 快速体验项目：`nexusdesk-validation-quick`，入口 `127.0.0.1:18080`，独立 PostgreSQL 与数据卷。
- 生产拓扑项目：`nexusdesk-validation-production`，入口 `127.0.0.1:18081`，独立测试数据库 `nexusdesk_validation_20260923`。
- 生产拓扑连接已有 PostgreSQL、Milvus、S3；没有修改正式业务库，也没有重启已有中间件。
- 使用 `docker.m.daocloud.io` 拉取基础镜像。服务器没有 Docker 默认 bridge，构建时设置 `NEXUSDESK_BUILD_NETWORK=host`；运行时使用各项目自己的 Compose 网络。

## 已通过

| 验证项 | 结果 |
| --- | --- |
| quickstart、backend、web 三个镜像目标构建 | 通过 |
| 空库执行 Alembic 全量迁移 | 通过 |
| 两容器快速体验启动、健康检查 | 通过 |
| 演示标识、本地代理身份注入 | 通过 |
| 示例知识问答返回 48 小时发货说明 | 通过 |
| 工单拟定后不创建，确认后创建 | 通过 |
| 快速体验重启、再次执行启动脚本 | 通过 |
| 生产迁移初始化及 PostgreSQL / Milvus / S3 连接检查 | 通过 |
| 生产 Web、API、Runtime Worker、Knowledge Worker 独立启动 | 通过 |
| 生产 Web HTTP 200，未授权接口返回 401 | 通过 |
| 无默认模型启动，不自动导入演示数据 | 通过 |
| 无模型对话返回 409 `model_not_configured`，不降级演示 | 通过 |
| 独立 Knowledge Worker 异步处理上传文件 | 通过 |
| 本地配置提示、两种部署标识的浏览器测试 | 3 项通过 |
| 部署单元测试与前端生产构建 | 通过 |

## 修正与边界

- 移除 Dockerfile 中会额外访问 Docker Hub 的语法镜像声明。
- 为构建网络提供可选配置，保持默认部署行为不变。
- 生产健康检查携带应用令牌，避免就绪接口因未授权被判为不健康。
- 同步最新的 `0025_open_platform` 分条执行迁移修复后，空库迁移成功。
- 生产默认模型改为 `unconfigured`，模型在界面配置并发布后绑定 Agent。
- 没有进行真实 Chat 推理、向量召回质量或高并发压力测试；Runtime Worker 验证范围为独立启动，Knowledge Worker 包含实际任务验证。
- 两套验证环境保留运行，均只监听本机端口。测试环境生成的角色令牌保存在服务器 `deploy/production/.env`，不写入文档。

从本机建立 SSH 转发后访问：

```sh
ssh -L 18080:127.0.0.1:18080 -L 18081:127.0.0.1:18081 root@10.0.11.112
```

浏览器打开 `http://localhost:18080`（快速体验）或 `http://localhost:18081`（生产拓扑验证）。
