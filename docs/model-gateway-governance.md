# 模型网关：部署、接口与联调

## 思考模式

配置方案的对话能力提供「跟随模型默认 / 开启思考 / 关闭思考」选择，与 JSON 的 `parameters.thinking` 双向同步，分别对应 `null` / `"enabled"` / `"disabled"`。新方案默认 `null`，旧方案缺少此字段时也不传开关；保存后须重新发布版本，已绑定固定旧版本的 Agent 需要切换到新版本。

首批明确支持官方 DeepSeek 渠道（`api.deepseek.com`，OpenAI 兼容协议）的 `deepseek-flash`、`deepseek-v4-pro` 和 `deepseek-v4-flash`，转换为供应商请求中的 `thinking: {type: ...}`。未知模型、代理地址和其他供应商暂仅允许默认模式；指定开关会在保存/发布方案时拒绝，备用模型同样校验，不会悄悄忽略参数。

按 [DeepSeek 思考模式文档](https://api-docs.deepseek.com/guides/thinking_mode/) 保留响应的 `reasoning_content`，通过 Agent 工具循环和后续历史回传。流式调用单独收集思考片段，不混入可见回答增量；运行事件和网关调用审计仍只记录现有元数据，不写入思考原文。运行会话历史会保存对应助手回复的思考字段，以便后续对话回传。历史数据不回填；旧消息没有该字段时仅发送空值，不构造思考内容。向非 DeepSeek 渠道转发时去除该字段。

DeepSeek 文档说明思考模式下 `temperature` 不生效；开关不等于统一支持所有供应商的推理参数。本次使用模拟供应商响应验证开关、流式与工具循环，不代表所有模型能力均已完成真实供应商联调。

## 动态选择模型标识

模型广场新增/编辑模型时，选择已启用的 OpenAI 兼容渠道，页面自动加载供应商模型列表，可通过「从渠道选择模型」填入模型标识，或点击「刷新模型列表」重新获取。已有标识不会被加载结果覆盖；手动切换渠道时清空旧标识。列表获取失败、为空或渠道不支持该接口时仍可手动填写。

新增管理员接口 `GET /api/v1/model-gateway/connections/{id}/available-models`，后端使用渠道已保存凭据请求其 `/models`，支持租户隔离、地址允许列表、禁止重定向、15 秒超时及 2 MB 响应上限。仅返回去重后的模型 ID，最多 1000 个；供应商标记后续分页时提示部分结果，其他标识可手动填写。供应商错误内容与凭据不返回浏览器，不自动探测模型能力、不自动保存模型或发布方案。

## 国际渠道补充

迁移 `0018_global_channels` 在原七个渠道基础上加入三个渠道，全新部署共十个。沿用未配置凭据、初始停用、保留已有配置及归档记录的规则。

| 渠道 | 默认服务地址 | 接入方式 |
|---|---|---|
| Claude (Anthropic) | `https://api.anthropic.com/v1` | [Anthropic 官方 OpenAI 兼容接口](https://platform.claude.com/docs/en/cli-sdks-libraries/libraries/openai-sdk) |
| GPT (OpenAI) | `https://api.openai.com/v1` | OpenAI Chat Completions |
| Gemini (Google) | `https://generativelanguage.googleapis.com/v1beta/openai` | [Google 官方 OpenAI 兼容接口](https://ai.google.dev/gemini-api/docs/openai) |

三个渠道均复用已有 OpenAI 兼容适配器和直接 API Key 配置。Claude 此处使用兼容层，未实现原生 Messages API 的全部能力；Anthropic 将兼容层定位为评估入口，生产使用需结合其兼容性限制评估。多工作区 Anthropic Key 可能需要额外 workspace header，目前渠道不支持该字段，应使用对应工作区的 Key。Gemini 兼容层为 beta。不自动登记模型或绑定 Agent，需填写账户可用模型标识并发布方案；本次验证初始化与页面配置，不包含真实供应商 Key 调用。

## 默认渠道（2026-09-22）

迁移 `0017_default_channels` 为 `AGENT_TENANT_ID` 指定的工作区，以及数据库中已有渠道、智能体或知识库的工作区，初始化以下七个渠道。全新部署执行迁移前设置与 API/worker 相同的 `AGENT_TENANT_ID`（未设置时为 `local`）。

| 渠道 | 默认服务地址 | 官方参考 |
|---|---|---|
| DeepSeek | `https://api.deepseek.com` | [接入文档](https://api-docs.deepseek.com/zh-cn/) |
| 豆包 (Doubao) | `https://ark.cn-beijing.volces.com/api/v3` | [Chat API](https://docs.volcengine.com/docs/ark/chat-api?lang=zh) |
| 千问 (Qwen) | `https://dashscope.aliyuncs.com/compatible-mode/v1` | [地域与地址](https://help.aliyun.com/en/model-studio/base-url) |
| Kimi | `https://api.moonshot.cn/v1` | [官方客户端配置](https://github.com/MoonshotAI/kimi-cli/blob/main/docs/en/configuration/env-vars.md) |
| 智谱 GLM | `https://open.bigmodel.cn/api/paas/v4` | [接入示例](https://docs.bigmodel.cn/cn/best-practice/case/ai-search-engine) |
| 腾讯混元 | `https://api.hunyuan.cloud.tencent.com/v1` | [兼容接口](https://cloud.tencent.com/document/product/1729/111007) |
| 小米 | `https://api.xiaomimimo.com/v1` | [MiMo 文档](https://platform.xiaomimimo.com/docs/en-US/usage-guide/passing-back-reasoning_content) |

均使用 OpenAI 兼容协议，初始停用且未配置凭据。渠道管理中填写 API Key、保存并启用后，在模型广场登记供应商提供的模型标识，再发布配置方案供 Agent 选择。不会初始化默认模型、发布方案或自动调用供应商。

同工作区已有同名或同地址渠道时保留原数据；固定 ID 避免重复初始化，已删除（归档）的预设不会重新出现。降级本次数据迁移保留渠道、用户修改及凭据。

千问/豆包默认国内地址；不同地域、专属域名或套餐须按对应控制台调整。腾讯官方正将新购模型服务迁往 TokenHub，上表使用其仍支持的混元兼容接口，TokenHub 用户应改为该平台给出的地址和模型标识。默认允许列表已加入这七个主机；如果部署显式覆盖 `AGENT_MODEL_GATEWAY_ALLOWED_HOSTS`，须自行加入所需主机。预设渠道不表示已验证所有供应商模型能力和推理参数。

## 渠道直接填写 API Key（2026-09-22）

渠道管理 → 新建或编辑渠道 → 凭据方式选择「直接填写 API Key」。保存后只返回配置状态，不回显密钥；留空保留已有密钥，填写新值替换，勾选「清除已保存的 API Key」后保存则删除。环境变量方式继续兼容，切换并保存环境变量配置会移除已保存的直接密钥。

API 接口接受只写字段 `api_key`、`clear_api_key`。凭据以 Fernet 认证加密保存在独立的 `gateway_credentials` 表中，不进入渠道公开配置、发布快照或审计内容。同一服务地址的密钥轮换立即用于新调用，无需重新发布方案；修改地址或协议时需重新填写或清除密钥，并重新发布方案。

先执行 `uv sync` 和 `uv run alembic upgrade head`（当前新增迁移 `0016_gateway_credentials`）。首次保存会自动生成 `backend/data/credentials/master.key`（以 backend 为启动目录）；供应商 API Key 无需环境变量。部署时须持久化并限制此目录访问，将主密钥与数据库分别备份；API 与 worker 以及多个实例须共享同一个主密钥文件。可通过设置 `AGENT_MODEL_CREDENTIAL_KEY_FILE` 指定绝对路径。主密钥缺失或错误时解密失败，不会自动覆盖已有主密钥。包含密钥记录时迁移禁止直接降级，避免丢失配置。

服务地址仍须满足现有主机允许列表。此功能不改变具体供应商模型、工具调用协议的兼容情况。

本次在已有模型接入、发布、调用链上补齐管理能力。所有功能使用 PostgreSQL 持久化，不依赖浏览器本地模拟数据。

实现状态核对日期：2026-09-21。本文件说明已交付的治理范围，不表示模型网关全部规划已完成。完整的“已实现/部分实现/未实现”清单见 [模型网关 README 第 1 节](../backend/src/agent_platform/modules/model_gateway/README.md#1-当前实现)。真实供应商验收和业务数据库升级需在对应部署环境分别确认。

## 已实现的闭环

| 功能 | 页面及服务行为 |
|---|---|
| 模型广场、渠道、方案 | 服务端搜索、分页、能力/状态/渠道筛选；创建、编辑、启停、逻辑删除；资源引用检查；乐观锁；方案发布/回滚 |
| 访问密钥 | 限定可调用方案、有效期、请求/并发配额；只展示一次的明文；数据库仅存 SHA-256 摘要；即时轮换与停用 |
| 敏感词 | 分类、增删改、启停、批量导入、去重、效果测试；NFKC 规范化、不区分大小写的包含匹配 |
| 模型审核 | 按模型设置输入/输出文本拦截；审核结果只记录命中词 ID 和分类，不记录原文；策略对新调用即时生效 |
| 调用日志 | 数据库分页和日期/方案名称/状态/能力/密钥过滤；调用详情、尝试、用量、审核结果；导出当前页 |
| 监控及费用 | 数据库全量区间聚合、每日趋势、模型维度费用、CSV 导出；按每次尝试保存定价快照，历史费用不随当前定价变化 |
| 租户配额 | PostgreSQL 事务和 advisory lock 实现跨进程原子准入；UTC 自然分钟/日计数；租户与密钥配额同时生效；并发租约自动释放，崩溃后的租约到期清理 |
| 告警 | 失败、耗时阈值、内容拦截；限定方案、冷却去重；调用结束时生成站内事件，查询、筛选及确认 |

沿用 Admin/Operator/Viewer 权限。治理配置仅 Admin 可写。`mgw_` 访问密钥仅用于外部调用入口，不能取得管理台权限，包括开发环境。

## Alembic

迁移文件：`backend/migrations/versions/0010_gateway_governance.py`，父版本 `0009_ticket_settings`。

新增词库、访问密钥、审核/定价设置、配额/计数/租约、费用记录、告警规则/事件表；调用表增加身份和审核元数据；原有资源增加逻辑删除标记。原有资源、发布快照和历史调用保持兼容。

在后端目录执行，数据库连接通过部署环境提供：

```powershell
uv sync --dev
uv run alembic current
uv run alembic upgrade head
uv run alembic current
```

预期版本为 `0010_gateway_governance`。本次已在独立 PostgreSQL 数据库验证空库升级、已有资源升级保留、回退后重升。

回退命令为 `uv run alembic downgrade 0009_ticket_settings`。若已产生治理配置、密钥、费用、审核记录或逻辑删除资源，迁移主动报 `gateway_governance_downgrade_requires_data_export`，避免静默丢失数据。必须先按部署流程备份、导出并制定恢复方案；不要在业务库中直接清表绕过保护。

## 启动与联调

```powershell
# 终端一：后端，先配置 AGENT_DATABASE_URL，再执行迁移
cd C:\code\ai-project\backend
uv run alembic upgrade head
uv run python -m agent_platform.apps.seed_models  # 可选：添加明确标记的演示资源
uv run uvicorn agent_platform.apps.api.main:app --host 127.0.0.1 --port 8000

# 终端二：前端
cd C:\code\ai-project\frontend
npm run dev
```

前端 `/api` 代理到 8000。真实供应商需配置允许访问的主机、模型标识和 `AGENT_MODEL_SECRET_*` 进程环境变量，详见 `backend/src/agent_platform/modules/model_gateway/PROTOCOL.md`。

## 新接口

统一前缀 `/api/v1/model-gateway`，除了外部调用均沿用平台 Bearer 身份。

| 接口 | 用途 |
|---|---|
| `GET /catalog/{connections|models|profiles}` | `page/page_size/q/enabled/operation/connection_id` 分页检索 |
| `GET/POST /manage/{sensitive_words|access_keys|alert_rules}` | 管理资源分页、创建 |
| `PUT /manage/{kind}/{id}?revision=N` | 全量编辑，有版本冲突保护 |
| `PATCH /manage/{kind}/{id}` | `{revision,enabled}` 启停 |
| `DELETE /resources/{kind}/{id}?revision=N` | 逻辑删除，保留历史，检查引用 |
| `POST /keys/{id}/rotate` | `{revision}`，新密钥只返回一次、旧密钥立即失效 |
| `POST /external/invoke` | Bearer `mgw_...`；请求体仍为 `{profile_id,version,payload}` |
| `POST /words/import` | `{words:[{name,category}]}`，一次最多 1000 条，原子导入并跳过重复词 |
| `POST /words/test` | `{text}`，返回是否命中及分类 |
| `GET/PUT /models/{id}/settings` | 输入输出文本审核和人民币单位价格，更新体 `{revision,spec}` |
| `GET/PUT /quota` | 租户每分钟/日请求及并发上限，更新体 `{revision,spec}` |
| `GET /call-records` | 分页、`since/until/status/operation/profile_id/access_key_id/q` 查询 |
| `GET /statistics` | 按 `since/until` 聚合，默认最近 30 天，区间最多 366 天 |
| `GET /alert-events` | 分页及 `acknowledged` 过滤 |
| `POST /alert-events/{id}/acknowledge` | 确认站内告警 |

日期必须带时区；图表按 UTC 日聚合。列表总数和页数据是两次查询，正在写入时可能短暂变化。旧的 `/connections`、`/models`、`/profiles` 和 `/calls` 数组接口保留兼容；新版网关页面使用分页接口。

## 验证

用独立测试库设置 `TEST_DATABASE_URL`；迁移测试使用 `MIGRATION_TEST_DATABASE_URL` 对应的有 CREATEDB 权限的本地测试角色，每次创建并删除随机命名的临时数据库，不对业务库做回退。

```powershell
cd C:\code\ai-project\backend
uv run pytest -q
uv run ruff check src/agent_platform/modules/model_gateway tests/test_gateway_governance.py tests/test_gateway_migrations.py

cd C:\code\ai-project\frontend
npm run build
# API、前端与独立开发库运行后：
npx playwright test tests/model-gateway.spec.ts tests/model-gateway-ui.spec.ts tests/gateway-fullstack.spec.ts
```

若 Playwright 自带浏览器未安装，可配置 `E2E_CHROME_PATH` 指向本机 Chrome。`gateway-fullstack.spec.ts` 不拦截 HTTP，不使用前端 mock：通过真实数据库验证页面配置→拦截→日志→告警→密钥调用/停用→统计。它会在开发库中创建 `联调-*` 测试资源。

## 明确边界

- 费用是本地定价和供应商返回用量的**估算**，包括重试尝试；未知用量/未配置价格为未知，不能用于宣称已完成供应商对账、钱包充值、扣款或金融结算。
- 审核是文本敏感词匹配，不等于语义安全分类。不会从音频/图片字节中自动提取内容进行审核；文件 ASR/OCR 的文本输出可审核。启用输出审核的 Chat 流先完整生成、审核通过后才返回，防止未审核片段泄漏。
- 告警为调用结束时触发的站内事件；不发送邮件、短信或外部 webhook，不包含后台主动巡检。连接检测可手动触发。
- 租户/密钥请求和并发配额跨进程生效；原供应商连接级并发仍按单进程控制。未实现分布式 Token 预算、熔断、加权灰度、自动密钥续期或实时双向语音。
- 密钥轮换、停用和配额调整影响新准入请求，不追溯取消已经进行中的供应商推理。
- 当前测试覆盖 demo 及 HTTP 适配契约；真实供应商需使用部署环境的地址和凭据进一步验收，demo 不是实际模型输出质量证明。
