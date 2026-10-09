# Phase 2 · 客户实体 + 画像/偏好记忆 — 技术落地文档

## 改动总览

| 层 | 文件 | 改动 |
|---|---|---|
| 迁移 | `backend/migrations/versions/0031_memory_customers.py` | 3 张新表 + `runtime_conversations.customer_id` 列 |
| 新模块 | `modules/memory/service.py` | 身份解析 + 记忆 CRUD + 冲突合并 |
| 新模块 | `modules/memory/extraction.py` | LLM 抽取器 |
| 新模块 | `modules/memory/routes.py` | 控制台记忆管理 API |
| 会话 | `modules/conversation/service.py` | 创建会话时身份解析，写入 customer_id |
| 渠道 | `modules/channel/service.py` | 解析链路入参（channel_id + session_id） |
| 开放平台 | `modules/open_platform/service.py` | 解析链路入参（app_id + external_user_id） |
| 运行时 | `modules/agent_runtime/engine.py` / `repository.py` | 记忆注入；run 完成后投递抽取任务 |
| 工单 | `modules/customer_service/service.py` | propose/decide 时回写 customer_id |
| 配置 | `modules/agent_config/service.py` | AgentConfig 增加 `memory_enabled` / `memory_write` |
| 前端 | `frontend/` 新增记忆管理页 | 客户列表 / 详情 / 待确认队列 |

## 数据模型（迁移 `0031_memory_customers.py`）

```sql
-- 客户实体
CREATE TABLE memory_customers (
    id          UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id   UUID NOT NULL,
    created_at  TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- 身份绑定（同一客户可绑多个来源，为跨渠道合并预留）
CREATE TABLE memory_identities (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL,
    customer_id  UUID NOT NULL REFERENCES memory_customers(id) ON DELETE CASCADE,
    source       VARCHAR(30) NOT NULL,        -- 'channel' / 'open_platform' / 'enterprise'
    external_key VARCHAR(200) NOT NULL,
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, source, external_key)
);

-- 记忆条目（Phase 2 只用 profile / preference 两类）
CREATE TABLE memory_items (
    id           UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    tenant_id    UUID NOT NULL,
    customer_id  UUID NOT NULL REFERENCES memory_customers(id) ON DELETE CASCADE,
    type         VARCHAR(20) NOT NULL,        -- 'profile' / 'preference'（Phase 3: 'event'；Phase 4: 'flag'）
    key          VARCHAR(100) NOT NULL,
    content      TEXT NOT NULL,
    confidence   REAL NOT NULL DEFAULT 1.0,
    status       VARCHAR(20) NOT NULL DEFAULT 'active',  -- pending / active / expired / deleted
    source_type  VARCHAR(20) NOT NULL,        -- 'extracted' / 'human'（Phase 3: 'ticket'；Phase 4: 'rule'）
    source_ref   VARCHAR(100),                -- conversation_id / staff 标识
    prev_content TEXT,                        -- 上一次被覆盖的内容（单级历史，支持回滚）
    revision     BIGINT NOT NULL DEFAULT 1,
    expires_at   TIMESTAMPTZ,                 -- Phase 2 恒为 NULL，字段先建
    -- 衰退机制（Phase 4 启用，列先建避免二次迁移）
    last_referenced_at TIMESTAMPTZ NOT NULL DEFAULT now(),  -- 最近一次被引用/强化的时刻，衰退时钟
    reference_count    INT NOT NULL DEFAULT 0,              -- 累计被引用次数（观测与调试）
    created_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    updated_at   TIMESTAMPTZ NOT NULL DEFAULT now(),
    UNIQUE (tenant_id, customer_id, type, key)  -- 同键唯一，天然防堆叠
);
CREATE INDEX memory_items_lookup ON memory_items (tenant_id, customer_id, type, status);

-- 会话关联客户
ALTER TABLE runtime_conversations ADD COLUMN customer_id UUID REFERENCES memory_customers(id);
CREATE INDEX conversations_customer ON runtime_conversations (tenant_id, customer_id);
```

决策说明：
- **同键唯一约束**替代"历史表"：`prev_content` 单级历史 + `revision` 已满足回滚与审计的最低要求；多级历史在 Phase 4 治理阶段视需要再补 `memory_item_history` 表。
- `prev_content` 覆盖语义：人工编辑也会保留被改前的值。
- `last_referenced_at` / `reference_count` 为 Phase 4 衰退机制预留：Phase 2 阶段仅在同键 upsert（客户再次提及同 key 事实）时顺手刷新 `last_referenced_at`——这是最强的强化信号，成本为零；完整的引用强化机制在 Phase 4 启用。

## 核心组件

### 1. 身份解析（`memory/service.py`）

```python
class MemoryService:
    def resolve_customer(self, tenant_id, source, external_key) -> UUID | None:
        # SELECT customer_id FROM memory_identities WHERE (tenant_id, source, external_key)
        # 未命中：INSERT memory_customers + memory_identities（同事务）
        # 利用 UNIQUE 约束处理并发：捕获唯一冲突后重查（与 channel_receipts 幂等模式一致）
```

接入点：
- `ConversationService.create()` 增加可选参数 `identity: {source, external_key}`，创建会话时解析并写 `customer_id`；
- `channel/service.py` 的 `send()`：传 `{source:'channel', external_key: sha256(session_id)}`（复用现有 external_id 生成中的哈希函数，提取为公共工具，保证两处哈希一致）；
- `open_platform/service.py` 会话获取/创建路径：传 `{source:'open_platform', external_key: f"{app_id}:{external_user_id}"}`；
- playground 路径不传 identity，`customer_id` 为 NULL。

### 2. 抽取流水线（`memory/extraction.py`）

```
RunRepository.finish()（Phase 1 已挂摘要任务处）追加：
  if conversation.customer_id and agent.memory_write and run 成功:
      去抖检查（同会话 5 分钟内已有 pending 抽取任务则跳过，不合并——抽取输入是最近消息，自然覆盖）
      INSERT memory_tasks(kind='memory_extract',
          payload={conversation_id, customer_id, agent_snapshot_ref})

MemoryWorker 认领后 → ExtractionService.run(task):
  1. 取会话最近 20 条 conversation_messages（含 role）
  2. 取该客户已有 active/pending 条目的 (type, key) 清单
  3. 组 prompt → 模型网关 Chat profile（同 Phase 1 的 profile 选择逻辑）
  4. 解析 JSON 输出，逐条执行：
     op=add    → INSERT ... ON CONFLICT (tenant_id, customer_id, type, key) DO NOTHING
     op=update → UPDATE SET content=NEW, prev_content=OLD, revision=revision+1
     confidence 路由：≥0.8 active / 0.5~0.8 pending / <0.5 丢弃
  5. source_type='extracted', source_ref=conversation_id
```

抽取 prompt 设计要点（借鉴 Mem0 的 ADD/UPDATE/NOOP 决策模式）：
- 只抽取**客户陈述的、跨会话有价值的事实与偏好**，明确反例清单（一次性订单号、临时情绪、寒暄不抽取）；
- 给出受控 key 词表（name/phone/email/account_id/product_model/company/language/style/address_name），允许 `custom_*` 扩展键；
- 要求每条输出 confidence 及判断依据（依据只进日志不入库，便于抽检）；
- 输出严格 JSON，解析失败整批丢弃记 warning（宁缺毋滥）。

### 3. 注入（`engine.py`，Phase 1 注入点之后）

```python
items = memory_service.active_items(tenant_id, customer_id,
        types=["profile", "preference"], limit=20)
if items:
    按 type 分组渲染：
      【客户画像】\n- name: 张三\n- product_model: X200
      【客户偏好】\n- language: 英文回复
    作为一条 SystemMessage 插入
```

- `RunRepository.claim()` 透传 `customer_id`（已在会话行上）；
- 条数/token 上限在 service 层裁剪，engine 只负责渲染。

### 4. 工单联动（`customer_service/service.py`）

- `propose()`：从会话行读 `customer_id`，写入 `pending_actions.payload.customer_id`；
- `decide(approve)`：创建工单时 `tickets.customer_id = payload.customer_id`（str(UUID)，兼容 VARCHAR(100)）；
- 工单详情 API 响应附带 `customer_id`，前端渲染跳转链接。

### 5. 控制台 API（`memory/routes.py`，挂在现有 FastAPI app）

```
GET    /api/memory/customers?q=&page=           客户列表（搜索 external_key）
GET    /api/memory/customers/{id}               详情（identities + items 分组）
PATCH  /api/memory/items/{id}                   编辑 content → source_type='human', confidence=1.0
DELETE /api/memory/items/{id}                   软删除 status='deleted'
POST   /api/memory/items/{id}/restore           恢复 deleted → active
GET    /api/memory/pending                      待确认队列
POST   /api/memory/items/{id}/confirm           pending → active
POST   /api/memory/items/{id}/reject            pending → deleted
```

- 鉴权沿用 `platform/identity` 的 staff 依赖：operator 可写，viewer 只读；
- 前端新增"记忆"一级菜单（客户列表 / 待确认队列两个 tab），复用现有列表/详情组件风格。

## 配置

```python
# settings.py
memory_extract_enabled: bool = True
memory_extract_recent_messages: int = 20
memory_extract_debounce_seconds: int = 300
memory_confidence_auto: float = 0.8
memory_confidence_pending: float = 0.5
memory_inject_max_items: int = 20
```

## 测试方案

| 层 | 用例 |
|---|---|
| 单测 service | resolve_customer 创建/复用/并发冲突；active_items 过滤与裁剪；update 覆盖与 prev_content |
| 单测 extraction | prompt 组装、JSON 解析容错、confidence 路由、受控 key 校验 |
| 集成 | 渠道会话两轮：提供姓名电话 → 断言条目生成 → 新会话断言注入 |
| 集成 | 改口场景："叫我张工" → 同键 update，revision=2，prev_content 正确 |
| 集成 工单 | 绑定客户会话 propose→approve → tickets.customer_id 非空 |
| API | 记忆管理各端点的鉴权（operator/viewer）、软删除与恢复 |
| 回归 | playground / 示例 Agent 会话零记忆写入；现有工单/渠道测试全绿 |

## 上线与回滚

- 迁移纯增量；`memory_extract_enabled=false` 关闭写入、`memory_enabled=false`（Agent 级）关闭读取，均可独立回退；
- 存量会话 `customer_id` 为 NULL 属正常状态，无需数据回填；可选提供一次性脚本按渠道会话 external_id 规律补建身份（评估后决定，非必须）。
