# 知识库中间件服务器

## 2026-09-22：Docker 安装

- 主机：`10.0.11.112`，CentOS 7，Linux 3.10。
- 原有 Kubernetes kubelet/containerd 保持运行；未修改其配置。
- Docker Engine：26.1.4（官方静态二进制）。
- Docker Compose：2.27.1（官方发行二进制）。
- systemd 服务：`agent-docker.service`，已启用开机启动。
- 二进制：`/opt/agent-platform/docker-bin`。
- 配置：`/opt/agent-platform/config/docker.json`。
- 数据目录：`/opt/agent-platform/docker-data`。
- socket：`/run/agent-docker.sock`，不开放远程 Docker API。
- `/usr/local/bin/docker` 包装器自动选择上述 socket。

为避免修改已有 Kubernetes 网络，Docker 配置关闭自动 bridge、iptables、ip6tables、IP forwarding 和 masquerade。当前容器须使用 `--network host`（Compose 使用 `network_mode: host`），由服务自身配置监听地址和端口；`ports` 映射不适用。部署前须检查端口冲突。

```sh
docker version
docker compose version
systemctl status agent-docker
docker info
```

使用本机 bash 和动态库构造的 `agent-platform/smoke:local` 镜像已成功运行，输出 `docker-container-ok`。测试容器运行后自动移除。

服务器直连 `registry-1.docker.io:443` 返回 connection refused，在线镜像拉取尚不可用。本地开发机器可连接 Docker Hub；后续可从本地下载已核验的镜像后离线导入，或使用项目认可的镜像仓库。没有配置未知第三方镜像代理。

后续用户已自行安装中间件并提供连接凭据。2026-09-22 本地联调确认 PostgreSQL 55433、Milvus 19530、MinIO 19000 可连接，已创建知识库桶并执行应用迁移；参见 `knowledge-workspace.md`。本任务不再负责安装或重启服务器中间件。安装 Docker 时根盘剩余约 12 GB，容量由服务器维护方监控。

安装依据：
- https://docs.docker.com/engine/install/binaries/
- https://docs.docker.com/reference/cli/dockerd/

服务器登录口令不写入项目或本文档。
