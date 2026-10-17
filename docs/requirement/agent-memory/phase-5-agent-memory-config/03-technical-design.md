# Phase 5 · Agent 记忆配置开关 — 技术落地文档

## 改动总览

| 层 | 文件 | 改动 |
|---|---|---|
| 配置模型 | `backend/src/agent_platform/modules/agent_config/service.py`（:30-34 既有三开关） | AgentConfig 新增 7 个字段；沿用 draft/publish/snapshot 链，**无表结构变更、无迁移** |
| 注入门控 | `backend/src/agent_platform/modules/agent_runtime/worker.py`（`_customer_memory` :110-172） | 读开关、类型过滤、注入上限截断 |
| 抽取门控 | `agent_runtime/worker.py`（:201-206）+ `modules/memory/extraction.py`（`enqueue_extract_task` :133-158）+ `modules/memory/service.py`（`route_confidence` :67-72、`apply_extracted` :412-501） | 写模式快照入队；review 模式全量路由 pending；类型过滤 |
| 检索门控 | `modules/agent_runtime/platform_tools.py`（:70-79 注册、:195 完成事件） | `memory_search_enabled` 注册门槛；返回上限生效 |
| 摘要门控 | `agent_runtime/worker.py`（:198） | 沿用既有 `summary_enabled`，零改动 |
| 前端表单 | `frontend/src/features/agents/AgentsPage.tsx`（initialConfig :22）+ `frontend/src/shared/api/client.ts`（AgentConfig 接口 :2） | "记忆"配置分组、联动置灰、预设模板、平台禁用提示 |
| 事件写入门控 | `modules/memory/events.py` + `modules/customer_service/service.py`（:294/418/420） | 维持全局 `memory_event_enabled` 门控，本阶段不增加 Agent 级门控（见决策 D3） |

**无数据库迁移**：AgentConfig 是 pydantic 模型（`extra="forbid"`），配置以 JSONB 存于 draft/publish/snapshot 链。新增字段全部带默认值，存量快照反序列化自动取默认值，行为与升级前一致。

## 配置模型（`agent_config/service.py`）

### 存储键与逻辑名映射

需求文档（02 FR-1）使用逻辑名；存储键**沿用既有三键不复名**（复名会导致存量快照因 `extra="forbid"` 校验失败）：

| 逻辑名（02 文档/前端展示） | 存储键 | 状态 |
|---|---|---|
| `conversation_memory_enabled` | `summary_enabled` | 既有（:30） |
| `customer_memory_enabled` | `memory_enabled` | 既有（:33），语义升级为总开关 |
| `memory_write_enabled` | `memory_write` | 既有（:34） |
| `memory_read_enabled` | `memory_read_enabled` | 新增，默认 true |
| `memory_types` | `memory_types` | 新增，默认 `["profile","preference","event"]` |
| `memory_search_enabled` | `memory_search_enabled` | 新增，默认 true |
| `memory_write_mode` | `memory_write_mode` | 新增，`Literal["auto","review"]`，默认 `auto` |
| `memory_inject_max_items` | `memory_inject_max_items` | 新增，默认 None（=跟随平台 settings 值） |
| `memory_search_limit` | `memory_search_limit` | 新增，默认 None（=跟随平台默认 5） |
| `memory_blacklist_auto_handoff` | `memory_blacklist_auto_handoff` | 新增，默认 false |

```python
class AgentConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")
    summary_enabled: bool = True
    memory_enabled: bool = True            # 长期记忆总开关（既有：注入门控）
    memory_write: bool = True              # 抽取/写入开关（既有）
    memory_read_enabled: bool = True       # 新增：注入开关
    memory_types: list[Literal["profile","preference","event"]] = ["profile","preference","event"]
    memory_search_enabled: bool = True     # 新增
    memory_write_mode: Literal["auto","review"] = "auto"  # 新增
    memory_inject_max_items: int | None = None            # 新增：None=跟随平台
    memory_search_limit: int | None = None                # 新增：None=跟随平台
    memory_blacklist_auto_handoff: bool = False           # 新增
```

校验：`memory_types` 至少一项（pydantic validator，空列表 422）；`memory_inject_max_items ∈ [1,100]`、`memory_search_limit ∈ [1,20]`（前端再做平台上限提示）。

### 生效值求值（运行时统一入口）

在 `agent_runtime/worker.py` 增加纯函数（供注入/抽取/检索三处复用，也供前端预览 API 复用）：

```python
@dataclass
class EffectiveMemoryConfig:
    summary: bool
    read: bool          # 是否注入
    write: bool         # 是否抽取
    types: frozenset    # 允许的记忆类型
    search: bool        # 是否注册 memory_search
    write_mode: str     # auto | review
    inject_max_items: int
    search_limit: int
    blacklist_auto_handoff: bool

def resolve_memory_config(cfg: AgentConfig, s: Settings) -> EffectiveMemoryConfig:
    master = s.memory_extract_enabled or True  # 见 D1：全局门控逐项对齐
    return EffectiveMemoryConfig(
        summary = s.summary_enabled and cfg.summary_enabled,
        read    = cfg.memory_enabled and cfg.memory_read_enabled,
        write   = s.memory_extract_enabled and cfg.memory_enabled and cfg.memory_write,
        types   = frozenset(cfg.memory_types),
        search  = cfg.memory_enabled and cfg.memory_read_enabled and cfg.memory_search_enabled,
        write_mode = cfg.memory_write_mode,
        inject_max_items = min(cfg.memory_inject_max_items or s.memory_inject_max_items,
                               s.memory_inject_max_items),          # 只能收紧
        search_limit     = min(cfg.memory_search_limit or 5, 5),    # 平台上限见 D2
        blacklist_auto_handoff = s.memory_blacklist_auto_handoff
                               or cfg.memory_blacklist_auto_handoff,  # 收紧方向为 true，取 OR
    )
```

**决策 D1（全局门控对齐）**：现状全局侧有 `summary_enabled`（settings.py:70）、`memory_extract_enabled`（:79）、`memory_event_enabled`（:94），但**注入（读）侧无全局 kill switch**。本阶段新增 `settings.memory_inject_enabled: bool = True`，`read = s.memory_inject_enabled and cfg.memory_enabled and cfg.memory_read_enabled`，补齐"全局关闭 => Agent 一定关闭"的读侧缺口。

**决策 D2（检索上限平台值）**：现状 `memory_search` 返回上限为常量。本阶段新增 `settings.memory_search_limit: int = 5` 作为平台默认与上限，Agent 值按 min 截断。

**决策 D3（事件写入不做 Agent 级门控）**：工单事件写入由 customer_service 流程触发（非 Agent run 上下文，拿不到 Agent 快照），维持全局 `memory_event_enabled` 门控；`memory_types` 不含 `event` 时仅影响**注入与抽取**，已写入的事件不再注入。此边界写入 02 文档 FR-1 说明。

### `memory_types` 与 `review` 的语义细节

- **注入过滤**：`_customer_memory` 查 `MemoryService.active_items` 时按 types 过滤（`flag` 不受过滤——治理标记永远注入，Phase 4 语义不变）；
- **抽取过滤**：`apply_extracted` 入口按 types 丢弃不允许的类型，计入 dropped（审计 details 沿用既有字段）；
- **review 模式**：`write_mode` 在 `enqueue_extract_task` 时从 run config 快照写入任务 payload（任务异步执行时 Agent 配置可能已变更，必须快照）；`route_confidence` 增加 `write_mode` 参数，`review` 时任何置信度一律 `pending`。人工/规则来源（`source_type in ('human','rule')`）不受影响。

### 黑名单自动转人工

现状：平台配置 `memory_blacklist_auto_handoff`（settings.py:127，默认 false）。下沉为 Agent 可收紧项：**生效 = 平台 OR Agent**（开启是收紧方向，不受"只能收紧"限制）。判定点沿用 Phase 4 既有位置，求值改用 `resolve_memory_config` 结果。

## 前端（AgentsPage.tsx）

- `client.ts` 的 `AgentConfig` 接口（:2）补齐 10 个存储键（ TS 类型与 pydantic 对齐）；
- 配置表单新增"记忆"分组，按 02 FR-3 的四组结构与联动规则实现（总开关置灰、写关隐藏 write_mode、检索开才显示 search_limit）；
- **平台禁用提示**：表单加载时拉取 `/observability/memory` 或新增轻量 `GET /agents/memory-config-caps` 返回平台侧开关与上限；平台关闭的项展示禁用态 + tooltip"平台已禁用"；数值项超平台上限时保存前提示"将被截断为平台上限 N"；
- **预设模板**：前端常量表（5 套预设），"应用预设"仅填充表单值，不产生额外请求，用户再点保存生效。

## 兼容与回归红线

| 项 | 说明 |
|---|---|
| 存量快照 | 新增字段默认值与旧行为逐位等价（全 true / auto / 跟随平台），无需回填 |
| 评估快照 | `evaluation/service.py`（:340-342）快照自动携带新字段默认值，评估回放行为不变 |
| example agent | 既有"example agent 排除抽取"逻辑（worker.py:201-206）不受影响 |
| 生效时机 | run config JSONB 快照（runtime_runs.config）天然保证"新会话生效、进行中会话不变"，零新增机制 |
| 审计 | 配置变更沿用平台 Agent 配置审计，不新增 action |

## 配置（settings.py 增量）

```python
memory_inject_enabled: bool = True      # D1：读侧全局 kill switch
memory_search_limit: int = 5            # D2：检索上限平台默认/上限
```

## 测试方案

详见 `04-test-plan.md`。核心：求值函数全组合单测（全局×Agent×数值截断）、review 模式抽取路由、types 过滤（注入与抽取双侧）、存量快照反序列化等价、前端联动与预设。

## 上线与回滚

- 纯增量字段 + 默认值兜底，上线即兼容；前端表单与后端字段可同车发布（后端先布，前端未布时旧前端不传新字段，pydantic 默认值生效）；
- 回滚：回退代码即可，存量快照含新字段时旧版本 pydantic `extra="forbid"` 会校验失败——**回滚前需评估**：若已有 Agent 保存过新配置，旧代码无法解析其快照。缓解：发布后观察期再执行存量保存，或回滚时用脚本从新快照中剔除新增键（提供运维脚本）。
