# Phase 3 · 事件记忆 + 语义检索 — 实施拆解方案

> 状态：已裁定（技术负责人拆解，dev/qa 以此为执行基线）
> 创建：2026-10-14
> 依据：`01`~`05` 全部文档 + 现状代码核查（backend @ feat/agent-memory-phase-3）
> 约束：pgvector 镜像变更已批准；测试本地优先、跑不通再 CI；代码提交统一由 dev 在 feat/agent-memory-phase-3 分支执行

## 1. 任务拆解（按依赖顺序）

依赖主线：**迁移 → store → 事件写入 → 工具 → 注入 → 部署 → 测试**。T1 阻塞一切含新列的代码；T3/T4 可并行；T9 前端可在 T5/T7 接口确定后并行。

| # | 任务 | 文件（新建/修改） | 依赖 | 溯源 |
|---|---|---|---|---|
| T1 | 迁移：`memory_items` 加 `embedding vector(1536)`（DO 块探测 pgvector，存在才建列与 HNSW 索引）、`content_tsv` 生成列 + GIN 索引、`embedding_failed` 列；`ticket_process_log` 加 `is_commitment` | `backend/migrations/versions/0036_memory_events.py`（新） | — | FR-1/FR-2，见 R1 |
| T2 | 配置项落地 | `backend/src/agent_platform/settings.py` | — | FR-5，见 R4/R10 |
| T3 | `MemoryStore` 接口 + PgVector/TsVector 双实现 + 模块级衰减函数 + 启动探测工厂 | `modules/memory/store.py`（新） | T1、T2 | FR-2，见 R4/R9/R14 |
| T4 | 事件写入器 + service 层事件方法（`add_event_item` / open_ticket 过期联动 / `pending_event_items`） | `modules/memory/events.py`（新）、`modules/memory/service.py` | T1、T2 | FR-1/FR-3/FR-4，见 R5/R6 |
| T5 | 工单钩子：`decide(approve)` 与 `update_ticket` 状态流转同事务投递 `event_write` 任务；`TicketUpdate` 加 `is_commitment` | `modules/customer_service/service.py`、`apps/api/platform_routes.py` | T4 | FR-1，见 R6/R7 |
| T6 | Worker 扩展：dispatcher 路由 `event_write`、`FAILURE_AUDIT` 新条目、每日 TTL 清理（软过期）、`embedding_failed` 限量重试、内嵌 worker 启停门槛加 `memory_event_enabled` | `modules/memory/worker.py`、`modules/memory/bootstrap.py` | T3、T4 | FR-4/非功能，见 R10/R11 |
| T7 | `memory_search` 内置工具：schema、执行分支、注册条件（customer 绑定 + `memory_enabled` + `tool_names`）、内置工具清单与保留名登记 | `integrations/business_tools/platform_tools.py`、`modules/tool_gateway/registry.py`（`BUILTINS`）、`modules/tool_gateway/workspace.py`（保留名集合）、`apps/api/platform_services.py`（注入 store） | T3 | FR-2/AC-7，见 R15 |
| T8 | 常驻注入：`【待跟进事项】` 段（≤5 条、updated_at 倒序），追加在画像/偏好段之后 | `modules/agent_runtime/worker.py`（`_customer_memory`）、`modules/memory/service.py`（渲染函数） | T4 | FR-3，见 R8 |
| T9 | 前端：工单处理表单"标记为承诺"勾选；记忆控制台增加 event 类型展示；新 Agent 草稿默认工具含 `memory_search`；四处 i18n | `frontend/src/features/tickets/TicketsPage.tsx`、`features/memory/CustomerDetail.tsx`、`CustomerMemoryDrawer.tsx`、`features/agents/AgentsPage.tsx`、`i18n` | T5、T7 | FR-1/FR-5，见 R7/R12/R15 |
| T10 | 部署：quickstart 默认镜像换 `pgvector/pgvector:pg17`；`deploy/README.md` 增 pgvector/降级差异一节（含 alpine→debian 数据卷注意事项）；（建议）根 `compose.yaml` 开发库同步换镜像 | `deploy/quickstart/compose.yaml`、`deploy/README.md`、`compose.yaml`（可选） | — | G4，见 R3 |
| T11 | 测试：单测（store 探测/衰减/渲染/工具参数）、PG 集成（生命周期/双模式/TTL/注入/工具端到端/失败降级）、迁移测试、前端 e2e 增补 | `backend/tests/test_memory_events*.py`（新）、`frontend/tests` | T1~T9 | 04 全文，见 R13 |
| T12 | 文档修订（dev 范围）：03 中迁移号 `0032`→`0036`、§部署变更 "新增 memory-worker" 措辞修正（见 R2）、postgres:16→17 表述修正。**04/05 归 QA 所有，dev 不得修改**；04 的三处修订（依据行与 P3-DP-02 的 `0032`→`0036`、P3-DP-05 按 R14 形态改写）由 QA 在执行 T11 前落实，具体措辞见 R14 | `03-technical-design.md`（dev）；`04-test-plan.md`（qa） | — | R1/R2/R3/R14 |

报告格式沿用 Phase 2 的 `06-test-report.md` / `07-acceptance-report.md` 结构（qa / acceptance 参照执行）。

## 2. 文档与现实不一致清单及裁定

### R1 迁移编号：0032 → `0036_memory_events.py`

文档（03 §改动总览/§数据模型、04 依据行与 P3-DP-02）写 `0032_memory_events.py`。现实：0030~0034 已被会话摘要与原生渠道集成占用，0035 是 Phase 2 的 `memory_customers`（Phase 2 已有过一次 0031→0035 重编号先例）。**裁定**：新迁移编号 `0036_memory_events.py`，`down_revision='0035_memory_customers'`；dev 在 T12 同步修订 03/04 文档引用。迁移内容不变：`memory_tasks.kind` 是 VARCHAR(30) 无 CHECK，新增 `event_write` kind **不需要**约束迁移。

### R2 "新增 memory-worker 服务"措辞错误——该服务已存在

03 §部署变更称"deploy/production：新增 memory-worker 常驻服务"。现实：`deploy/production/compose.yaml` 第 57 行已有 `memory-worker`（Phase 1 部署形态 2026-10-10 修订引入，`python -m agent_platform.apps.worker.memory`，复用后端镜像），`deploy/README.md` 第 76/107/109 行均已将其列为既有服务。**裁定**：Phase 3 对 production compose **零变更**；memory-worker 仅通过代码新增任务 kind 获得新能力。quickstart 不变（`AGENT_EMBEDDED_WORKER=true` 内嵌）。dev 修订 03 文档措辞为"memory-worker 增加 event_write/TTL/重试任务处理能力，部署形态不变"。

### R3 镜像版本：postgres:17-alpine → `pgvector/pgvector:pg17`

文档写 `postgres:16` → `pgvector/pgvector:pg16`。现实：`deploy/quickstart/compose.yaml` 用 `postgres:17-alpine`（`${POSTGRES_IMAGE:-...}` 可覆盖），根 `compose.yaml` 用 `postgres:17`，CI 用 `postgres:17` service。**裁定**：
- quickstart 默认值改为 `pgvector/pgvector:pg17`（pgvector 官方镜像只有 Debian(bookworm/trixie) 变体，**没有官方 alpine tag**）；`POSTGRES_IMAGE` 环境变量既是镜像源覆盖口、也是回滚口（回退即设回 `postgres:17-alpine`，vector 列残留无害、探测自动降级）。
- **数据卷就地切换风险（P3-DP-03 必测）**：alpine(musl) → debian(glibc) 同卷切换存在 locale/collation 提供方差异，文本索引可能报 collation version mismatch。切换后应执行 `REINDEX DATABASE`；README 需写明该注意事项，全新部署无此问题。
- 生产环境 PG 为外部中间件（production compose 无 postgres 服务），仅需 README 说明"启用 vector 扩展则自动升级为向量模式"。
- 根 `compose.yaml`（开发库）建议同步换默认镜像，便于本地 vector 模式开发（非阻断项）。

### R4 embedding 路径：走模型网关现有 `embed` op，绑定用部署级配置

核查结论：模型网关**已有完整 embedding 能力**（`modules/model_gateway/gateway.py` op=`embed`、`EmbedRequest` 契约、逐模型 `embedding_dimension` 校验、用量落 `gateway_calls`），知识库语义分块（`knowledge/semantic.py`）已在用，无需网关扩展。但有两处文档与现实不符：
1. 03 称维度校验"与知识库向量索引的维度校验同策略"——知识库**没有向量索引**（embedding 仅在分块时瞬时使用、Python 内算 cosine），该策略不存在；
2. 03 未说明 memory 的 embedding profile 从哪来（知识库是每 KB policy 绑定，不适用于 memory）。

**裁定**：
- 新增部署级配置 `memory_embedding_profile_id` / `memory_embedding_profile_version`（默认 None）。本仓库是单租户部署形态（`settings.tenant_id`，memory worker 按该租户认领），部署级配置与现有架构一致，不为 Phase 3 发明租户级设置表（platform_settings 模块只有 README）。
- Store 启动探测条件（`auto` 模式）：pgvector 扩展存在 **且** profile 已配置 **且** catalog resolve 成功且 `operation='embed'` **且** 模型 `embedding_dimension == 1536`（迁移列维度）；任一不满足 → TsVectorStore + warning 日志（这就是 P3-DP-05"拒绝绑定"的落地形态：不是 API 校验，而是探测降级，见 R14）。
- embedding 调用经 `gateway.invoke(tenant, profile_id, version, EmbedRequest(...))`，用量自然落 `gateway_calls`（P3-CM-02）。

### R5 事件 key 约定统一（文档内部自相矛盾，必须裁定）

02 FR-3 用 `open_ticket` / `commitment_pending` 作 key 类名；03 §2 用 `ticket:{no}` / `ticket:{no}:resolved` / `commitment:{log_id}`；03 §4 注入查询又写 `key LIKE 'open_ticket%' OR 'commitment%'`。三套互不相同。**裁定**（同时满足 FR-1 留存创建事件、AC-6 在途条目过期、P3-IT-01"3 条 active + open_ticket expired"）：

| 事件 | key | 行数 | expires_at |
|---|---|---|---|
| 创建 | 事件行 `ticket:{ticket_no}:created` + 在途注入行 `open_ticket:{ticket_no}`（同事务两行） | 2 | 事件行 now+TTL；在途行同 TTL |
| 解决 | `ticket:{ticket_no}:resolved`；同时 `open_ticket:{ticket_no}` 置 expired（不删除） | 1+联动 | now+TTL |
| 关闭 | `ticket:{ticket_no}:closed`；同样联动过期 open_ticket | 1+联动 | now+TTL |
| 承诺 | `commitment:{log_id}`（单行，既是事件记录又是注入条目） | 1 | NULL（兑现/工单关闭时置 expired） |

- 注入查询：`type='event' AND status='active' AND (key LIKE 'open_ticket:%' OR key LIKE 'commitment:%') ORDER BY updated_at DESC LIMIT :memory_event_inject_max`。
- 复用 Phase 2 同键 upsert（`UNIQUE(tenant_id,customer_id,type,key)` + `prev_content`），事件重放幂等。
- 已知边界：在途工单挂超 180 天未动，open_ticket 行会随 TTL 过期、注入消失（与 FR-4 一致，不为例外；commitment 类才是唯一不过期类）。

### R6 FR-1 写入条件：以 `tickets.customer_id` 为准

文档写"工单关联的会话有 `customer_id`"。现实：Phase 2 起 `tickets.customer_id`（VARCHAR(100)，存 str(UUID)）已在 approve 时从会话落列。**裁定**：事件写入器以 `tickets.customer_id` 为唯一判定来源（不再回查会话）；为 NULL 则不投递/不写入（P3-IT-03）。写入器对 `memory_customers` 做存在性校验（FK 失败不得让任务崩溃，记 warning 后正常完成）。任务 payload 采用最小集、写入器回读工单现况（见 §3 接口约定），避免 payload 过期。

### R7 `is_commitment` 入口：只有一条备注 API，设计范围收敛

03 称"工单回复/备注 API 增加布尔字段"。现实：坐席侧只有 `PATCH /tickets/{tid}`（`TicketUpdate{status,note,revision}`），`update_ticket` 对非空 note 写 `action_type='remark'` 日志；不存在独立的 reply API。**裁定**：入口收敛到该 API——`TicketUpdate` 增加 `is_commitment: bool = False`；仅当 `is_commitment=true` 且 note 非空时，remark 行落 `is_commitment=true` 并投递 commitment 事件（payload 带 log_id）。前端 TicketsPage 处理表单加"标记为承诺"勾选（T9）。

### R8 常驻注入落点：`agent_runtime/worker.py`，不是 engine.py

03 §改动总览把"高优事件常驻注入"标在 `engine.py`。现实：Phase 2 注入在 `agent_runtime/worker.py::_customer_memory` 取数渲染，`engine.py` 只负责把 `customer_memory` 追加为 SystemMessage。**裁定**：沿用 Phase 2 结构——`worker.py` 组合"画像/偏好 + 待跟进事项"两段（同一 SystemMessage，待跟进段在后），`engine.py` 零改动。渲染函数放 `memory/service.py`（与 `render_memory_injection` 同层，可单测）。

### R9 TSVECTOR 中文召回：simple 配置不分词，需 ILIKE 兜底

03 注明"中文用 simple 配置 + 分词局限可接受"。核查：`to_tsvector('simple', 中文整句)` 产出单个 lexeme，`plainto_tsquery('simple', 查询词)` 同样整词化，纯 tsvector 匹配对中文**几乎零召回**（知识库 `knowledge_chunks.terms` 是预处理关键词列，不是正文生成列，先例不完全适用）。**裁定**：`TsVectorStore.search` 匹配条件为 `content_tsv @@ plainto_tsquery('simple', :q) OR content ILIKE '%'||:q||'%'`，排序 `ts_rank`（ILIKE 兜底命中为 0）× 同一时间衰减因子。这是降级模式的已知局限，写入 deploy/README 差异说明；QA 构造 text 模式用例数据时注意词汇重叠可控（04 §2 数据构造约定已覆盖）。

### R10 回滚开关：新增 `memory_event_enabled`，不复用抽取开关

03 §上线与回滚说"关闭 `memory_extract_enabled`（或拆分 `memory_event_enabled`）"。**裁定**：拆分。新增 `memory_event_enabled: bool = True`，只门控事件任务**投递**（工单钩子处判断），已在队列的任务继续执行完；`bootstrap.embedded_memory_worker` 启动条件加为 summary/extract/event 任一开启。检索工具与注入按 Agent 草稿 `tool_names` / `memory_enabled` 逐 Agent 灰度（既有机制，不加新开关）。

### R11 TTL 清理与 embedding 重试：worker 空闲周期扫描，不引入调度器

03 说"仿照 Knowledge Worker 的周期扫描"。核查：knowledge worker 并无独立调度器（claim 时顺带收割过期租约）。**裁定**：`MemoryWorker` 记录上次扫描时间，空闲（无任务可领）且距上次 ≥24h 时执行一轮（启动后首轮立即执行）：
1. TTL：`UPDATE memory_items SET status='expired',updated_at=now() WHERE type='event' AND status='active' AND expires_at<now()`；
2. 重试：限量（每轮 50 条）取 `embedding_failed=true AND status='active'` 重投 embed，成功清标记，失败保留等下轮。
单租户单语句更新，无需租约；多副本下 UPDATE 幂等。

### R12 控制台可见性缺口：记忆控制台需加 event 类型

文档未提控制台。现实：后端条目管理 API（编辑/删除/恢复）类型无关，event 条目天然可管；但前端 `CustomerDetail.tsx` / `CustomerMemoryDrawer.tsx` 硬编码 `['profile','preference']`，event 条目将不可见。**裁定**：T9 中前端记忆控制台增加 event 分组（标签"事件"），沿用既有条目表格组件；不新增后端 API。

### R13 CI pgvector 矩阵：本地优先，矩阵为后续项

04 §7 要求"无 pgvector 容器跑全量测试（CI 矩阵 job）常驻"。按既定决策 2（测试本地优先执行，跑不通再考虑 CI）**裁定**：qa 先在本机两套环境（普通 PG 17 + pgvector/pgvector:pg17 两个 Docker 容器）跑双模式矩阵；全绿后再在 `.github/workflows/ci.yml` 增加 pgvector service 矩阵 job（现状为单个 `postgres:17` service，加 matrix.image 即可）。CI 矩阵不阻塞本阶段验收，但 PR 描述需说明。

### R14 P3-DP-05 落地形态变更：探测降级代替"配置校验拒绝"

因 R4 采用部署级配置绑定，不存在"绑定时的配置校验"入口。**裁定**：P3-DP-05 的通过形态改为——维度不一致的 profile 配置后，Store 探测降级 text 模式并输出明确 warning 日志（测试断言 `available()='text'` + 日志）。04 文档该条措辞由 **qa** 修订（04/05 归 QA 所有，dev 的 T12 只改 03）：P3-DP-05 预期列改为"输出维度与迁移默认（1536）不一致的 Embedding profile 配置后，Store 启动探测降级 text 模式并输出明确 warning 日志（部署级配置无绑定时校验入口，按 R14 裁定以此形态判定）"；同批修订依据行与 P3-DP-02 行内的 `0032`→`0036`。

### R15 `memory_search` "默认加入"的口径：新 Agent 草稿默认，存量不变

02 FR-5"默认加入"与 03 §上线与回滚"逐 Agent 灰度"存在张力。**裁定**：`memory_search` 登记为内置工具后自动出现在工具选择清单（`registry.BUILTINS` 驱动 `/tools` 列表）；**新建** Agent 草稿的 `initialConfig.tool_names` 默认包含它（前端 T9）；存量 Agent 不变，需在草稿中手动添加并发布——这正是逐 Agent 灰度的操作路径。AC-7（草稿移除后运行时不出现）由 T7 的注册条件保证。注册条件实现于 `PlatformTools.schemas`：`memory_search in allowed` 且 run 行 `customer_id` 非空 且 agent 快照 `memory_enabled!=False`，三者缺一不出现在工具列表（P3-UT-10 四种情形）。

### 其他核查结论（无不一致，确认设计可行）

- `memory_items` 已有 `expires_at` / `last_referenced_at` / `reference_count` / `status` CHECK 含 `expired`，事件 TTL 与衰减**无需额外列**；
- `runtime_runs` 行带 `customer_id`（Phase 2 注入已用），工具注册条件可直接判定；
- 迁移 DO 块探测 `pg_available_extensions` 写法可行；空表建 HNSW 索引瞬时完成，无需 CONCURRENTLY；
- 生产备份/升级流程不变的结论成立（pgvector 数据随 PG dump）。

## 3. 交接要点

### 3.1 给 dev（phase3-dev）

**工作范围**：T1~T10、T12（后端全部 + 前端 T9 + 部署 T10 + 文档修订）。所有提交在 `feat/agent-memory-phase-3`，提交规范与署名沿用仓库惯例。实现中遇到与本文件裁定冲突的现实，先找 lead 仲裁，不要擅自改口径。

**接口约定（qa 也以此为准）**：

```python
# modules/memory/store.py
class MemoryHit(TypedDict):
    content: str; key: str; updated_at: datetime; score: float

class MemoryStore(Protocol):
    def available(self) -> str: ...            # 'vector' | 'text'
    async def embed_and_save(self, item_id: UUID, text: str) -> None: ...
    async def search(self, tenant_id: str, customer_id: UUID,
                     query: str, limit: int) -> list[MemoryHit]: ...

def build_memory_store(engine, gateway, settings) -> MemoryStore  # 启动探测一次
def decay_factor(last_referenced_at: datetime, half_life_days: int,
                 now: datetime) -> float  # 模块级，双实现共用；exp(-ln2*age/half_life)
```

- 检索过滤硬条件（两个实现一致）：`tenant_id` + `customer_id` + `status='active'` + `type='event'`；衰减只影响排序不过滤；`limit=min(limit, settings.memory_search_limit_max)`。
- `embed_and_save`：PgVector 实现走 `gateway.invoke(..., EmbedRequest(inputs=[{"id": str(item_id), "text": text}]))`，失败置 `embedding_failed=true` 不抛错；TsVector 实现 no-op。

```python
# 事件任务 payload（memory_tasks.kind='event_write'）
{"ticket_id": "<uuid str>",
 "event": "created" | "resolved" | "closed" | "commitment",
 "log_id": <int | None>}          # 仅 commitment 有
# tenant 由 worker 的 settings.tenant_id 认领语义保证；写入器按 ticket_id 回读
# 工单/ticket_no/detail/resolution/customer_id 现况，再按 R5 落行。
```

- EventWriter 服务签名对齐现有 dispatcher 协议：`async def execute(self, task) -> None`（参照 `ExtractionService`）；`FAILURE_AUDIT['event_write'] = 'memory.event.failed'`。
- 投递点（与工单更新同事务）：`CustomerService.decide` approve 分支（created）、`update_ticket` 状态变为 resolved/closed 分支、remark 带 `is_commitment=true` 分支；投递前判 `settings.memory_event_enabled` 与 `tickets.customer_id` 非空。

```python
# settings.py 新增（T2，默认值即 FR-5/03 §配置 + R4/R10）
memory_event_enabled: bool = True
memory_event_ttl_days: int = 180          # Field(ge=1, le=3650)
memory_search_limit_max: int = 10         # Field(ge=1, le=50)
memory_vector_enabled: str = "auto"       # Literal['auto','true','false']
memory_event_inject_max: int = 5          # Field(ge=1, le=20)
memory_search_half_life_days: int = 30    # Field(ge=1, le=365)
memory_embedding_profile_id: str | None = None
memory_embedding_profile_version: int | None = None
```

- service 层新增：`add_event_item(...)`（含 upsert + prev_content）、`expire_open_ticket(c, tenant, customer_id, ticket_no)`、`pending_event_items(tenant, customer_id, limit)`、渲染函数（`【待跟进事项】` 段，格式 `- 工单 TK-… 处理中：…` / `- 承诺待兑现：…`）。
- 迁移 DO 块内 `CREATE EXTENSION IF NOT EXISTS vector` 需要 DB  superuser/权限，CI 与本机 Docker 均为 superuser，无需特殊处理；无扩展时 DO 块跳过，迁移照常通过（P3-DP-02）。

**红线**：不得改动 Phase 1/2 既有行为（注入段顺序：摘要 → 画像/偏好 → 待跟进事项）；检索/注入任何路径不得跨 tenant/customer；工单接口同步路径只允许 INSERT memory_tasks（毫秒级），不得同步调用模型。

### 3.2 给 qa（phase3-qa）

**工作范围**：T11（测试实现与执行）、按 04 双模式矩阵执行并产出 `06-test-report.md`（格式参照 phase-2 同名报告）。执行基线 HEAD 以 dev 完成提交为准，环境准备先行：本机起两个 PG 容器（`postgres:17-alpine` 与 `pgvector/pgvector:pg17`），各自迁移至 head。

**验收红线（溯源编号）**：
- FR-1~FR-5 全部；AC-1~AC-7（含 AC-4b）；04 的 P3-UT-01~11、P3-IT-01~12、P3-DP-01~05、P3-CM-01~04；
- P0 阻断：检索/注入返回其他租户或其他客户数据；工单主流程破坏——发现即停线报 lead；
- P3-DP-05 按 R14 修订后的形态判定（探测降级 + warning 日志）；
- 双模式排序语义一致（同一 `decay_factor`，P3-UT-04）；衰减只降权不隐藏（P3-UT-05 必须断言老条目仍返回）；
- 时间类用例一律改库构造（`expires_at` / `last_referenced_at`），不允许真实等待（04 §1 原则 4）；
- 回归：Phase 1/2 memory 测试、工单状态机与 process_log 测试、前端三门槛 + Playwright 全绿（`is_commitment` 默认 false 不改变现有行为）。

**与 dev 的接口对齐**：payload 结构、key 约定（R5）、注册条件（R15）、设置项默认值以本文件 §3.1 为准；发现实现与约定不符按缺陷提，不要私下协商改约定（约定变更须经 lead 记入本文件）。
