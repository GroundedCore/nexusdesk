# Phase 6 · 客户记忆可视化与注入可解释性 — 测试方案

> 状态：待实施（本阶段实现落地后执行）
> 创建：2026-10-17
> 依据：`02-requirements.md`（FR-1~FR-6、AC-1~AC-12）、`03-technical-design.md`（迁移 NNNN、埋点与批量落库、查询服务、D1~D3）

## 1. 测试范围与原则

**范围**：`memory_injection_log` 表与埋点落库、`memory.injected` 事件兼容扩展、注入记录三入口查询、客户统计列、客户详情 8 Tab、单条记忆详情（字段/diff/时间线/反查）、会话工作台注入视图、删除/合并/TTL 级联。

**原则**：

1. **热路径零退化**：明细写入必须批量、同事务，P95 额外耗时 ≤ 50ms 有自动化断言；无注入场景零开销。
2. **事件兼容**：`memory.injected` 既有计数字段（items/flags/events）语义不变，Phase 4 观测（hit_rate 聚合）不受影响。
3. **泄露面零新增**：审计时间线与注入明细不含记忆明文与 PII；条目删除后不试图还原内容。
4. **历史数据优雅降级**：上线前 run 无明细，全部视图空态不报错。
5. **治理级联完整**：删除/合并/TTL 三条清理路径逐一验证，明细不留孤儿数据。

## 2. 测试环境与数据构造

| 环境 | 用途 |
|---|---|
| 单测 | 渲染函数返回值改造（text + injected 列表）、统计聚合 SQL 逻辑、diff 纯函数 |
| 集成（PG + pytest fixture） | 落库正确性、三入口查询、级联清理、TTL、性能计数 |
| 前端 | Playwright（扩展 `frontend/tests/memory.spec.ts`）：三层视图、空态、跳转 |

**数据构造约定**：绑定客户的会话跑 run 后直查明细表；历史 run 用"无明细的 runtime_runs 行"模拟；超期明细用 `UPDATE injected_at = now() - interval '91 days'` 构造后手动触发单轮 TTL。

## 3. 单元测试

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P6-UT-01 | 渲染函数改造 | 三个渲染函数返回 (text, injected)：text 与改造前逐位一致；injected 的 id/section/score 正确 | 03 §1 |
| P6-UT-02 | score 取值 | profile/preference 的 score = 注入 SQL 算出的 effective_confidence；flag score 为 NULL | 03 §1 |
| P6-UT-03 | 统计聚合 | customer_memory_stats 单条 SQL 返回各类条数/pending 数/max(updated_at)，与逐库直查一致 | FR-2 |
| P6-UT-04 | diff 展示数据 | prev_content 为 NULL 时前端数据无 diff 区；有值时返回双栏内容 | FR-5 |
| P6-UT-05 | 空注入 | 未绑定客户 / 读关闭的 run：injected 列表为空，不产生任何明细写入 | 03 §1 |

## 4. 集成测试

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P6-IT-01 | 明细落库 | 绑定客户对话一轮：明细行数 = 注入条目数（含 flag）；run_id/customer_id/item_id/section 正确；injected_at 合理 | AC-1 |
| P6-IT-02 | 事件兼容 | `memory.injected` 事件保留 items/flags/events 计数且数值与明细行数一致；新增 item_ids 与明细 item_id 集合相同 | AC-1/原则 2 |
| P6-IT-03 | 批量写入性能 | run 完成路径 SQL 计数：明细写入为单条批量 INSERT；P95 额外耗时 ≤ 50ms（计时断言） | AC-9 |
| P6-IT-04 | 同事务 | mock 明细 INSERT 失败：run finish 事件与明细一起回滚，无半截状态 | 03 §1 |
| P6-IT-05 | 三入口查询 | 客户注入列表 / run 明细 / 条目反查 三接口数据互相一致；条目已删时展示"已删除"不报错 | AC-4/5/7 |
| P6-IT-06 | TTL 清理 | 构造 91 天前明细，手动触发 maintenance 单轮：超期明细删除，保留期内不受影响 | AC-10 |
| P6-IT-07 | 客户删除级联 | 删除客户：明细随事务清除；审计 details 含 injection_logs_deleted 计数 | AC-12 |
| P6-IT-08 | 客户合并 | 合并后源客户明细改挂目标；注入历史连续可查 | 03 §4 |
| P6-IT-09 | 生命周期时间线 | 构造条目全生命周期（抽取→确认→编辑→衰退→恢复）：timeline 按时间序含全部事件，来源正确 | AC-6 |
| P6-IT-10 | 审计时间线无泄露 | 客户审计接口返回中无记忆明文、无 PII（正则扫描断言） | 原则 3 |
| P6-IT-11 | 开关降级 | `memory_injection_log_enabled=false`：无明细写入，事件计数字段照常，查询接口返回空列表 | 03 §配置 |

## 5. API 测试

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P6-API-01 | 注入记录三端点 | Reader 可读；未认证 401；分页/limit 生效 | FR-4 |
| P6-API-02 | 列表统计 | `with_stats=true` 返回统计列；单条批量 SQL（无 N+1，查询计数断言） | FR-2 |
| P6-API-03 | 时间线端点 | item timeline 与 customer audit 数据正确、倒序 | FR-3/5 |
| P6-API-04 | 租户隔离 | 跨租户访问全部 404 | 非功能 |
| P6-API-05 | 历史 run | 无明细的 run 查询返回空列表（200，非报错） | AC-8 |

## 6. 前端测试（Playwright，扩展 memory.spec.ts）

| 编号 | 用例 | 预期 | 溯源 |
|---|---|---|---|
| P6-FE-01 | 列表统计列 | 总数/分类/待确认高亮/flag 徽标/最近更新正确渲染；排序与筛选生效 | AC-2 |
| P6-FE-02 | 详情 8 Tab | 各 Tab 懒加载渲染正确；画像/偏好/事件分 Tab 的编辑/删除/恢复回归通过 | AC-3 |
| P6-FE-03 | 注入记录下钻 | run 列表展开见明细（key/类型/小节/score）；跳会话链接正确 | AC-4 |
| P6-FE-04 | 条目详情抽屉 | 完整字段齐全；diff 正确；时间线齐全；来源会话跳转正确 | AC-6 |
| P6-FE-05 | 工作台注入视图 | 会话工作台展示本次对话各 run 注入条目；点击条目开详情 | AC-5 |
| P6-FE-06 | 空态 | 历史会话/未绑定客户：空态文案正确无报错 | AC-8 |
| P6-FE-07 | viewer 只读 | 全部视图只读，无操作按钮 | AC-11 |

## 7. 回归范围

- Phase 1-4 记忆全量套件原样通过（重点：`memory.injected` 事件消费方——Phase 4 观测 hit_rate 聚合）；
- Phase 5 配置门控与注入改造叠加后行为正确（read 关闭时零明细）；
- 既有 MemoryItemTable 编辑/删除/恢复、PendingQueue 确认/拒绝回归。

## 8. 缺陷分级与出口准则

| 级别 | 定义 | 处理 |
|---|---|---|
| P0 阻断 | run 热路径逐条写库或性能超标；审计/明细泄露记忆明文或 PII；跨租户可见；删除客户后明细残留 | 修复并回归后方可继续 |
| P1 严重 | 明细与注入实际不符（漏记/错记）；事件计数字段语义被破坏；合并丢注入历史 | 修复前不得进入验收 |
| P2 一般 | 统计列偏差；时间线缺事件类型；空态缺失 | 记录后可带问题验收，需产品签字 |
| P3 轻微 | 文案、样式问题 | 排期修复 |

**出口准则**：全部用例通过；AC-1~AC-12 复现通过；**热路径性能、泄露面、级联完整性为阻断项**；Phase 1-5 回归套件全绿；无 P0/P1 未关闭。
