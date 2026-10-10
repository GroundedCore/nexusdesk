# Phase 3 · 事件记忆 + 语义检索 — 技术落地文档

## 改动总览

| 层 | 文件 | 改动 |
|---|---|---|
| 迁移 | `backend/migrations/versions/0032_memory_events.py` | `memory_items` 加 embedding/tsvector 列；`ticket_process_log` 加 `is_commitment` 列 |
| 新模块 | `modules/memory/store.py` | `MemoryStore` 接口 + pgvector / tsvector 双实现 |
| 新模块 | `modules/memory/events.py` | 工单事件 → 记忆条目的写入器 |
| 工具 | `integrations/business_tools/platform_tools.py` | 新增 `memory_search` 内置工具 |
| 工单 | `modules/customer_service/service.py` | 状态机流转钩子投递事件任务 |
| 运行时 | `modules/agent_runtime/engine.py` | 高优事件常驻注入 |
| Worker | `modules/memory/worker.py` | 新增 `event_write` / `event_embed_retry` / 每日 TTL 清理 |
| 部署 | `compose.yaml` / `deploy/` | postgres 镜像 → `pgvector/pgvector`（可选，推荐） |

## 数据模型（迁移 `0032_memory_events.py`）

```sql
-- 检索支持（pgvector 存在时建 vector 列，否则跳过——迁移内用 DO 块探测）
DO $$ BEGIN
    IF EXISTS (SELECT 1 FROM pg_available_extensions WHERE name='vector') THEN
        CREATE EXTENSION IF NOT EXISTS vector;
        ALTER TABLE memory_items ADD COLUMN embedding vector(1536);
        CREATE INDEX memory_items_embedding ON memory_items
            USING hnsw (embedding vector_cosine_ops);
    END IF;
END $$;

-- 关键词降级：content 全文向量（中文用 simple 配置 + 分词局限可接受，
-- 与 knowledge_chunks.terms 的做法一致）
ALTER TABLE memory_items ADD COLUMN content_tsv TSVECTOR
    GENERATED ALWAYS AS (to_tsvector('simple', content)) STORED;
CREATE INDEX memory_items_tsv ON memory_items USING gin (content_tsv);

-- embedding 生成失败标记（重试用）
ALTER TABLE memory_items ADD COLUMN embedding_failed BOOLEAN NOT NULL DEFAULT false;

-- 承诺标记
ALTER TABLE ticket_process_log ADD COLUMN is_commitment BOOLEAN NOT NULL DEFAULT false;
```

说明：
- embedding 维度跟随租户 Embedding profile 的输出维度，迁移默认 1536；维度不一致的 profile 在配置校验时拒绝绑定（与知识库向量索引的维度校验同策略）。
- 迁移的 `DO` 探测块保证无 pgvector 的环境照常执行，只是没有 vector 列——`MemoryStore` 启动探测决定实现。

## 核心组件

### 1. MemoryStore 接口（`memory/store.py`）

```python
class MemoryStore(Protocol):
    def available(self) -> str: ...          # 'vector' | 'text'
    def embed_and_save(self, item_id, text) -> None: ...
    def search(self, tenant_id, customer_id, query, limit) -> list[MemoryHit]: ...

class PgVectorStore(MemoryStore):   # 有 pgvector 时
    # search: 相关度 = 1 - (embedding <=> :query_vec)，过滤 tenant/customer/status='active'
    # 最终排序 score = 相关度 × decay(age)，age 基于 last_referenced_at，
    # decay 为指数衰减：exp(-ln(2) * age_seconds / half_life_seconds('event'))
    # embed_and_save: 模型网关 Embedding profile → UPDATE embedding 列

class TsVectorStore(MemoryStore):   # 降级实现
    # search: ts_rank(content_tsv, plainto_tsquery('simple', query)) × 同一时间衰减因子
    # embed_and_save: no-op
```

时间衰减设计要点：
- 衰减**只影响排序，不影响可见性**（Phase 3 阶段不改变条目 status）；把旧事件降权而非隐藏，检索不到时 Agent 仍可查到；
- 基准时间用 `last_referenced_at` 而非 `created_at`：被反复引用（`memory_search` 命中后强化，Phase 4 启用）的旧事件可以"续命"，避免高价值历史被误降权；
- 半衰期按类型配置（事件默认 30 天），常量在 `settings.py`，两个 Store 实现共用同一衰减函数（放 `store.py` 模块级），保证双模式排序语义一致。

- 启动时探测：`SELECT 1 FROM pg_extension WHERE extname='vector'` + embedding profile 可用性，二者缺一走 `TsVectorStore`；
- `memory_vector_enabled` 配置可强制覆盖探测结果（`false` 用于对比测试）；
- embedding 调用走模型网关，用量落 `gateway_calls`，与知识库一致。

### 2. 事件写入（`memory/events.py`）

```
customer_service.update_ticket() / decide(approve) 状态流转成功后：
    INSERT memory_tasks(kind='event_write', payload={ticket_id, event, customer_id})

EventWriter.run(task):
    case created:  add_item(key=f'ticket:{no}', content=f'工单 {no} 已创建：{摘要}',
                            high_priority 注入类 key='open_ticket')
    case resolved: add_item(key=f'ticket:{no}:resolved', content=...)
                   expire_item(key=f'ticket:{no}' 的 open_ticket 条目)
    case closed:   同 resolved 模式
    case commitment (process_log.is_commitment=True):
                   add_item(key=f'commitment:{log_id}', content=..., 高优注入类)
    写入后异步 embed_and_save（embedding 失败置 embedding_failed=true）
```

- `expires_at = now() + memory_event_ttl_days`（commitment 类除外，NULL）；
- `add_item` 复用 Phase 2 的同键 upsert 逻辑（同键覆盖 + prev_content）；
- `is_commitment` 标记入口：工单备注/回复 API 增加布尔字段，前端加"标记为承诺"勾选。

### 3. memory_search 工具（`platform_tools.py`）

```python
@tool("memory_search", description="检索当前客户的历史服务记录与过往事项")
def memory_search(query: str, limit: int = 5) -> str:
    # 上下文注入：tenant_id / customer_id 由 PlatformTools 构建时绑定
    # （与 knowledge_search 绑定 kb 的方式一致）
    hits = store.search(tenant, customer, query, min(limit, 10))
    return 渲染 [{content, updated_at}] 或 "未找到相关历史记录"
```

- 工具注册条件：`conversation.customer_id` 非空 且 `agent.memory_enabled` 且 `memory_search` 在 `agent.tool_names` 中（默认加入，AgentConfig 草稿可移除）；
- playground / 无客户会话不注册该工具，避免无意义调用。

### 4. 常驻注入（engine.py，Phase 2 注入段后追加）

```
【待跟进事项】（最多 5 条）
- 工单 TK-008 处理中：物流异常补发
- 承诺待兑现：48 小时内退款（2026-10-07 记录）
```

查询：`type='event' AND status='active' AND key LIKE 'open_ticket%' OR 'commitment%'`，service 层一个方法封装。

### 5. 清理与重试任务

- **TTL 清理**：MemoryWorker 每日周期（仿照 Knowledge Worker 的周期扫描）：`UPDATE memory_items SET status='expired' WHERE type='event' AND status='active' AND expires_at < now()`；
- **embedding 重试**：`embedding_failed=true` 条目限量重投，成功清标记，连续失败保留标记等下轮。

## 配置

```python
# settings.py
memory_event_ttl_days: int = 180
memory_search_limit_max: int = 10
memory_vector_enabled: str = "auto"      # auto / true / false
memory_event_inject_max: int = 5
memory_search_half_life_days: int = 30   # 检索排序的时间衰减半衰期（事件类）
```

## 部署变更

- `compose.yaml`（quickstart）与 `deploy/production`：`postgres:16` → `pgvector/pgvector:pg16`（同名服务，数据卷兼容，纯镜像替换）；
- `deploy/production`：新增 `memory-worker` 常驻服务（Phase 1 部署形态 2026-10-10 修订引入：生产独立容器、复用后端镜像、可多副本伸缩；quickstart 不变，仍内嵌于 API 进程）；
- 不换镜像的存量部署：迁移照常通过，自动降级 TSVECTOR，文档（`deploy/README.md`）补一节说明差异；
- 生产备份/升级流程不变（pgvector 数据随 PG dump 走）。

## 测试方案

| 层 | 用例 |
|---|---|
| 单测 store | PgVectorStore/TsVectorStore 各自 search 排序与过滤；探测切换逻辑 |
| 单测 events | 四种事件的记忆内容渲染；resolved 对 open_ticket 的过期联动 |
| 集成 | 工单全生命周期（create→resolve→close）后断言记忆条目状态变迁 |
| 集成 | 双模式检索：同一 query 在 vector/text 模式下均能命中目标条目 |
| 集成 工具 | Agent 对话中触发 memory_search（mock 模型返回 tool_call），断言工具结果与注入 |
| 集成 TTL | 构造过期条目，清理任务执行后检索不可见 |
| 回归 | 无 pgvector 容器跑全量测试（CI 加一个 job 矩阵） |

## 上线与回滚

- 全部能力由 `memory_tasks` 异步驱动，关闭 `memory_extract_enabled`（复用 Phase 2 总开关，或拆分 `memory_event_enabled`）即停止新事件写入；
- 检索工具可通过 Agent 草稿移除，逐 Agent 灰度；
- pgvector 镜像变更可独立回滚（换回 postgres 镜像，vector 列残留无害，探测自动降级）。
