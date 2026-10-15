# Phase 4 · 治理、衰退与标记记忆 — 验收报告

> 状态：已完成（结论：**有条件通过**，见 §8）
> 创建：2026-10-15
> 依据：`05-acceptance-plan.md`（A1~A13、UAT、业务指标、合规 C1~C7）、`02-requirements.md`（FR-1~FR-7、AC-1~AC-13）、`01-business-goals.md`（成功指标）、`06-test-report.md`（QA 测试执行结果）、`08-implementation-breakdown.md`（R1~R23 裁定，删除/合并级联按 R16/R22 口径、门控语义按 R23 口径判定）
> 执行：验收代理（自动化验收轮）；执行日期 2026-10-15；验收基线 HEAD `9c98e76`

## 1. 验收组织与范围说明

本轮为**自动化验收轮**：A1~A13 按验收方案在本机 Docker 双 PG（真实 PostgreSQL 17，alpine=text 模式 / pgvector=vector 模式）+ demo/mock 可编程模型环境逐条复现，执行手段为"QA 测试基建在验收 HEAD 上独立复跑（非引用 QA 结论）+ 独立手工实测脚本 + 代码级判据核对"。与测试报告的分工：`06-test-report.md` 回答"用例过了吗"，本报告回答"业务场景成立吗、指标是否达成、风险是否可控"。

**时间类场景（衰退迁移/规则扫描/容量淘汰）按方案与 04 §1 原则 4 执行**：直接改库构造 `last_referenced_at`/`due_at` 等时间字段，手工触发单轮周期任务（`daily_migration` / `scan_overdue_tickets`），无真实等待。手工实测脚本（验收代理编写，独立于 QA 用例）覆盖 S1~S8 共 8 组场景 30 项断言，在两种检索模式下各执行一遍，构造数据用后已清理（双容器零残留核验）。

以下各项超出本环境交付能力，按 Phase 1/2/3 验收报告先例如实标记"待生产验证 / 待人工走查"并给出测量方案（见 §5、§7）：

- **A2 的"回复体现优先服务"**（真实模型推理 + 人工判定）：本机 demo/mock 模型不具备真实作答能力；注入段最前与差异化指令进入模型输入已双模式实测；
- **注入保鲜度、衰退误伤率两项运营指标**：方案定义为上线后观测期统计；
- **UAT 运营/客服主管真人走查**：本轮以代码级核对 + QA 的 Playwright 证据代验，建议上线前真人走查一次（非阻断）。

**前置条件核对**：`04-test-plan.md` 双模式矩阵全部通过（`06-test-report.md` §10 出口准则逐条满足），无 P0/P1 未关闭（D1~D5 均已修复并回归关闭，其中 P0 的 D1 与 P1 的 D2/D4 见 06 §9）。验收数据使用专用租户（每场景独立 `acc-<uuid>`），用后已清理。

## 2. 验收环境

| 环境 | 配置 | 承担场景 |
|---|---|---|
| 本机 Docker PG A（text 模式） | 容器 `nexusdesk-test-pg`（postgres:17-alpine，127.0.0.1:55432），库 `nexusdesk_test` 迁移至 head（0037，本轮实测无 vector 扩展） | A1~A13 text 模式复现；手工实测 S1~S8；合规 C1~C7；全量回归 |
| 本机 Docker PG B（vector 模式） | 容器 `nexusdesk-test-pgvector`（pgvector/pgvector:pg17，127.0.0.1:55433），库 `nexusdesk_test` 迁移至 head（0037，本轮实测 embedding 列在） | A1~A13 vector 模式复现；手工实测 S1~S8（含 embedding 随行清除核验）；合规 C2 |
| 模型 | demo/mock | 全部自动化场景 |
| 类生产（真实模型）+ 上线后数据 | — | 本轮不可用：A2 作答质量与 2 项运营指标留待（§5、§7） |

**本轮独立复跑证据**（验收 HEAD `9c98e76`，与 QA 报告计数逐项吻合；`9c98e76` 相对 QA 全量回归基线 `50080dc` 仅新增 06 测试报告文档，代码零改动）：

| 项 | 结果 |
|---|---|
| P4 套件 · text 模式（alpine 容器） | **56 passed, 1 skipped, 0 failed**（46.96s；skip 为 P4-IT-18 vector 专属按设计门控，与 06 A-2 一致） |
| P4 套件 · vector 模式（pgvector 容器） | **57 passed, 0 skipped, 0 failed**（44.27s，与 06 A-3 一致） |
| 全量后端回归 · alpine 容器（独占机器单次完整跑批，deselect 预存 flake 单独计次） | **490 passed, 15 skipped, 1 deselected, 0 failed**（476.00s，与 06 A-4 一致） |
| `uv run ruff check .`（backend，与 CI Lint 同口径） | All checks passed |
| 前端 `npm run typecheck` / `check:i18n` | 通过（1344 keys × 4 语言，较 Phase 3 +56 即本阶段新增，与 06 B-1 一致） |
| 手工实测脚本 S1~S8 · text 模式 | **29/29 断言通过** |
| 手工实测脚本 S1~S8 · vector 模式 | **30/30 断言通过**（多 1 项为 S4d embedding 随行清除核验） |

P4 套件 = `test_memory_governance.py` + `test_memory_governance_postgres.py` + `test_memory_governance_api.py` + `test_memory_governance_migrations.py`，双容器各执行一遍（`TEST_DATABASE_URL` / `MIGRATION_TEST_DATABASE_URL` 分别指向 55432 / 55433）。全量回归 pgvector 腿（06 A-5：502 passed / 0 failed）基线代码与验收 HEAD 完全一致，本轮引用 QA 证据未重复跑批。

## 3. 功能验收记录（A1~A13）

执行人：验收代理（自动化）；日期：2026-10-15；环境：上表双 PG + demo/mock 模型。按方案要求全部场景在两种检索模式下各执行一遍（双容器复跑 + 手工实测脚本双腿执行）。"手工实测"= 验收代理独立脚本改库构造 + 手工触发单轮周期任务（脚本非 QA 交付物，断言口径按 R7/R8/R16/R22/R23 裁定编写）。

| # | 判定 | 执行记录与实际结果 | 证据 |
|---|---|---|---|
| A1 | **通过（双模式）** | 人工编辑 name 条目后自动抽取同键更新：人工值不被覆盖，生成 `name:suggested` pending 建议；确认按 R4 合并语义落回原条目（content 更新、prev_content 留存、revision+1、建议物理删除）；控制台 PendingQueue 显示原键 + "建议更新（人工保护）"标签、确认文案"确认后将更新人工维护的原条目"（代码级核对 `PendingQueue.tsx:77-95`） | P4-IT-01、P4-UT-07（本轮双容器复跑）；R4 契约回归用例（06 §2）；手工实测 S1g（确认后时钟刷新恢复注入） |
| A2 | **通过（自动化层，双模式）**；"回复体现优先服务"待生产验证（人工判定） | vip 打标后进线：注入段【服务标记】位于最前（顺序：服务标记→画像→偏好→待跟进事项，R15）；flag 独立上限 `memory_flag_inject_max` 不占 20 条画像预算；无 flag 时注入体与 Phase 3 逐字节一致；blacklist 追加固定指令"谨慎服务，涉承诺一律转人工确认"；标记不挤占画像/偏好条目数已证。"回复体现优先服务"需真实模型推理——注入内容完整进入模型输入已双模式实测，作答质量列入 §7 L1 | 手工实测 S8a/S8b（双模式）；P4-IT-03、P4-IT-04（本轮复跑） |
| A3 | **通过（双模式，手工实测）** | 改库构造工单 `due_at` 超期（open）→ 手工触发单轮规则扫描：自动打 complaint_risk（`source_type='rule'`、`source_ref`=最早超期工单）；复扫幂等（marked=0、revision=1、无新审计，D3/D5 修复口径）；改库构造 30 天无复发（工单 resolved + due_at 回拨 40 天）→ 规则标自动解除并写 `memory.flag.removed` 审计；另一客户人工同 key 标记全程不被规则触碰 | 手工实测 S2a~S2d（双模式）；P4-IT-05（本轮复跑） |
| A4 | **通过（双模式，手工实测）— 一票否决项通过** | 人工编辑保存含手机号/身份证/银行卡/邮箱文本：落库为 `138******78` / `110101********777X` / `***************0123` / `z***@example.com` 形态；prev_content 二次编辑后仍仅存脱敏形态；flag 备注同样脱敏；全库扫描（content + prev_content + 审计 payload 正则）零明文；审计 details 仅含 `pii_masked` 计数（实测 4）；抽取与事件写入路径由 P4-IT-02 四写入口用例覆盖（R5/R6）；开关关闭时原文落库（R21 新写入口径，P4-IT-02 第二例） | 手工实测 S6a~S6d（双模式）；P4-UT-01~06、P4-IT-02（本轮复跑） |
| A5 | **通过（双模式，手工实测）— 一票否决项通过** | 删除客户（构造 2 条目 + 1 身份 + 1 绑定会话 + 1 工单）：items/identities/customer 物理零残留；会话保留且 customer_id=NULL；工单字符串引用不变（R16 删除场景口径，与 R22 合并场景区分），前端按 `customer_active` 降级显示"客户已删除"（`TicketsPage.tsx:90`）；审计仅操作人/条数/时间不含客户数据；vector 模式 embedding 列随行清除（实测删除后全租户无 embedding 残留）；同外部标识再进线重建为新客户（P4-IT-06）；控制台删除入口 Popconfirm 二次确认（`CustomerDetail.tsx:152-160`） | 手工实测 S4a~S4d（双模式）；P4-IT-06、P4-IT-07（性能 0.037s ≪ 2s）、P4-IT-18（pgvector）、P4-API-01（本轮复跑） |
| A6 | **通过（双模式，手工实测）** | 构造同键冲突（源侧人工 name vs 目标侧抽取 name）：人工条目胜（protected 优先）；非冲突条目全量归并；identities/会话改挂目标；**R22**：合并事务内级联改写 `tickets.customer_id`（字符串引用），审计含 `tickets_relinked=1` 与取舍明细（kept 侧）且不含内容值；源客户消失；合并后源侧在途工单的 resolved 事件正常写入目标客户、规则扫描覆盖（P4-IT-08 第三例）；控制台合并 Modal 警示 + 确认（`CustomerDetail.tsx:299-300`）；源=目标 400 拒绝、目标缺失整事务回滚 | 手工实测 S5a~S5d（双模式）；P4-IT-08×3、P4-UT-12、P4-API-02（本轮复跑） |
| A7 | **通过** | `memory_summary()` 对构造数据集各分区一致（抽取量按日/命中率/pending 积压/治理计数/衰退计数/reinforce 队列/PII 命中）；`GET /observability/memory` viewer 200、匿名 401、响应与服务层同形（D4 回归守卫）；观测页记忆卡片区前端在（P4-CM-04，Playwright 9/9 含观测区用例）；记录点代码核对在（`repository.py:326` memory.injected、`platform_tools.py:195` memory_search.completed，R10） | P4-IT-17（本轮双容器复跑）；06 附录 B-2；代码级核对 |
| A8 | **通过（双模式，手工实测）** | 改库构造 preference 200 天未引用（半衰期 90 天，有效分 ≈0.19）：跌破阈值即不注入（R7 查询侧 SQL 内联过滤实时生效，不等每日任务）；手工触发单轮 `daily_migration` → status=expired；控制台恢复（restore）刷新时钟后不再立即复汰、正常注入 | 手工实测 S1a~S1c（双模式）；P4-IT-09/10、P4-IT-15、P4-UT-10/11（本轮复跑） |
| A9 | **通过（双模式）** | 同一条目临近过期前被客户再次提及（同键 upsert，L1）：`last_referenced_at` 重置，任务执行后保持 active 并恢复注入（构造口径 144 天，06 §1 备注③修正后）；L1 刷新不受 `memory_decay_enabled` 门控（R23(b) 裁定：L1 为 Phase 2 既有行为） | P4-IT-11（本轮双容器复跑） |
| A10 | **通过（双模式，手工实测）** | 构造 protected 条目 500 天未引用：不衰减（effective_confidence 取原值）、每日迁移与容量淘汰双重免疫、正常注入；flag 500 天同样免疫；protected ⇔ source_type='human' 不变量五个检查点零漂移（R18） | 手工实测 S1e、S3b（双模式）；P4-IT-09、P4-UT-10、P4-CM-05（本轮复跑） |
| A11 | **通过（双模式，手工实测）** | 构造 profile 700 天（置信 1.0，有效分 ≈0.24 跌破 0.3）：每日迁移转入 pending 再确认队列（非 expired，R8 路由）；人工确认后时钟刷新（R19）恢复注入，次日迁移不再打回 | 手工实测 S1d/S1g（双模式）；P4-IT-09、P4-IT-15（本轮复跑） |
| A12 | **通过（双模式，手工实测）** | 构造单客户 211 条 active（5 protected + 206 条 0~205 天梯度）：手工触发单轮每日任务 → 淘汰 11 条至 200 条上限内；被淘汰者恰为有效分最低的 11 条（最老）；protected 全保留；逐条写 `memory.evicted` 审计含有效分明细；软过期（expired）可恢复且无复活-复死循环（D2 修复口径，P4-IT-14） | 手工实测 S3a~S3c（双模式）；P4-IT-14（本轮复跑） |
| A13 | **通过（双模式，手工实测）** | `memory_decay_enabled=false`：注入查询无衰减过滤（200 天老龄条目照常注入，与 Phase 3 一致；同数据开启时被过滤，对照实测）；maintenance 仅余 Phase 3 TTL（P4-IT-16）；**R23(b)**：decay off 时 reinforce 不投递、L3 不刷新 `last_referenced_at`，L1 同键 upsert 刷新不受门控；embedded worker 门控按 R23(a) 五开关扩展（decay/rule-scan 任一开启即启动，P1-UT-12 更新版回归通过） | 手工实测 S7（双模式）；P4-IT-10、P4-IT-16×2、P1-UT-12（本轮复跑）；代码核对 `bootstrap.py:77-78`、`agent_runtime/worker.py:210` |

## 4. UAT 核对（运营/客服主管清单，自动化代验）

| 判定项 | 本轮核对结果 |
|---|---|
| 打标/解除标记（vip / complaint_risk / blacklist） | API 三层授权实测（P4-API-03：非法 key 400、viewer 只读、DELETE 软删 + 审计）；前端 flag 卡片组代码核对在（P4-CM-04） |
| pending 队列处理建议（含人工保护 `:suggested`） | `:suggested` 条目标识与确认合并文案代码核对在（`PendingQueue.tsx:77-95`）；确认=合并回原条目的 R4 语义双模式实测（A1） |
| expired 条目恢复 | restore 刷新时钟、保持 active、下一轮迁移不再迁移（P4-IT-15）；deleted 恢复不刷新时钟（R19 refresh_on={"expired"}） |
| 客户合并（二次确认、合并后记忆完整性） | Modal 警示 + 确认代码核对在；合并后记忆完整性双模式实测（A6：人工胜冲突、事件流不中断、规则扫描跟随） |
| 客户删除（二次确认） | Popconfirm 代码核对在；删除闭环双模式实测（A5） |
| 观测页记忆卡片解读 | 指标分区与构造数据一致（P4-IT-17）；前端卡片区在（Playwright 9/9） |

结论：操作清单逐条核对通过，无阻断问题；运营/客服主管真人走查列为 §7 L6 非阻断项（与 Phase 3 L6 同先例）。

## 5. 业务指标逐项判定

| 指标 | 目标 | 本轮判定 | 数据 / 验证方法与就绪度 |
|---|---|---|---|
| 人工编辑 30 天内被自动覆盖比例 | 0% | **通过（机制层双模式实测）**；上线后审计对账复核 | 机制证据：抽取对 protected 条目物理不可写（降级 `:suggested` 建议，A1 双模式实测；P4-IT-01/UT-07/CM-05 不变量零漂移；R18 单一判据防覆盖）。生产复核方法（方案 §6）：审计对账脚本逐条核查 human 条目的 update 来源（`memory.item.edited` vs 抽取批次），责任方：研发负责人出具对账数据、产品负责人确认 |
| 删除执行完整性 | 100% | **通过（机制层实测，双模式）**；上线后自动化校验复核 | 本轮实测：双容器级联零残留（含 pgvector embedding 列随行清除，S4a~S4d）；会话置 NULL 保留、审计可溯不含客户数据；删除性能 0.037s（上限 2s，P4-IT-07）。生产复核方法：删除后全表按 customer_id/identity 扫描 + 向量列校验脚本（测试基建可直接复用） |
| PII 脱敏覆盖率 | ≥ 99% | **通过（验收口径 100%）** | 按 R21 裁定口径（验收期构造数据 + 上线后新写入）：构造数据全库正则扫描（content + prev_content + 审计 payload）明文命中 0 / 应脱敏 4 类模式全覆盖；四写入口（抽取/编辑/flag 备注/事件）全部脱敏（P4-IT-02）。存量数据明文不回溯，列入 §7 L2 |
| 注入保鲜度 | ≥ 80% | **待生产验证** | 定义为注入集中 90 天内被引用过的条目占比，需上线后观测统计。就绪度：保鲜机制（L1/L2/L3 强化 + 衰退过滤）双模式实测；`memory_summary()` 观测分区在（FR-7）。测量方式：观测 SQL 对注入集（memory.injected 记录点）按 `last_referenced_at` 分布统计。责任方：研发负责人出具观测数据，产品负责人判定 |
| 衰退误伤率 | ≤ 5% | **待生产验证** | 定义为上线后 30 天 expired 条目中被人工恢复的比例，本质需要真实运营数据。就绪度：恢复链路（restore/confirm 时钟刷新）双模式实测；`memory.decay_expired` / `memory.evicted` 审计含有效分可对账。责任方同上 |
| 观测页记忆指标可用 | 可查 | **通过** | 抽取量/命中率/pending 积压/治理/衰退/删除执行分区全部实测（P4-IT-17 服务 + HTTP 面；A7） |

## 6. 合规核对（C1~C7）

| # | 核对项 | 判定 | 证据（本轮独立核验） |
|---|---|---|---|
| C1 | PII 脱敏不可逆、原值不落库（含 prev_content 与审计 payload） | **通过（一票否决项）** | 手工实测 S6a~S6d（双模式）：四写入口脱敏、prev_content 仅存脱敏形态、审计 details 仅 `pii_masked` 计数；租户级全库正则扫描（content + prev_content + audit_records.details）零明文；`mask()` 为纯函数无外部依赖（P4-UT-01~06） |
| C2 | 删除级联完整：PG 行 + embedding 无残留，审计可溯且不存客户数据 | **通过（一票否决项）** | 手工实测 S4a~S4d（双模式）：items/identities/customer 物理零残留、会话置 NULL 保留、审计仅操作人/条数/时间（实测 details 无客户内容）；vector 模式 embedding 随行清除实测；`MemoryStore.delete_customer` 两实现 no-op 预留（Milvus 切换预案，`store.py:50/125`） |
| C3 | flag 仅能人工/规则写入，LLM 抽取路径物理不可达（输出过滤 + 入口抛错 + DB 约束三层） | **通过（一票否决项）** | 三层逐一实测：①输出侧 `validate_item` 只接受 profile/preference（`extraction.py:81`，P4-UT-09）；②写路径 `upsert_flag` 对 `source_type='extracted'` 抛 DomainError（手工实测 S6e）；③DB CHECK 约束拒绝非法 flag key（手工实测 S6e 触发 CheckViolationError；迁移 0037 约束实测在，`memory_items_flag_key_check`） |
| C4 | 审计事件不含明文 PII、连接串、密钥 | **通过（一票否决项）** | 手工实测 S4c/S5d/S6b/S6d：删除/合并/编辑/打标/衰退/淘汰审计 details 仅含 id/key/计数/有效分，不含内容值与明文 PII；代码级核对 governance/decay 全部 audit 调用点只传计数与标识符；R5 裁定 pii 命中数内嵌 details 而非原值 |
| C5 | 合并不可逆操作有二次确认 + 完整审计（源/目标/取舍明细） | **通过** | 前端合并 Modal 警示 + 确认（`CustomerDetail.tsx:299-300`）；审计含源/目标 ID、归并条数、冲突取舍明细（kept 侧）、`tickets_relinked`（手工实测 S5c/S5d）；目标缺失整事务回滚、源=目标 400（P4-IT-08/API-02） |
| C6 | 衰退全部软过期可恢复；protected/flag 免疫有自动化断言常驻 | **通过** | 衰退/淘汰一律 `status='expired'` 软过期（D2 修复口径），restore 刷新时钟可恢复（P4-IT-14/15）；免疫断言常驻：P4-UT-10（公式免疫）、P4-IT-09（500 天 protected/flag 双免疫）、P4-CM-05（不变量零漂移），本轮手工实测 S1e/S3b 复验（双模式） |
| C7 | 衰退机制可整体关闭（`memory_decay_enabled=false`）回退至 Phase 3 行为 | **通过** | 手工实测 S7（双模式）对照：关闭时 200 天老龄条目照常注入、开启时被过滤；P4-IT-16×2（maintenance 仅 TTL + R23(b) reinforce 不投递不刷新时钟）；无 flag 时注入体与 Phase 3 逐字节一致（P4-IT-03 第二例） |

## 7. 遗留问题清单

| # | 级别 | 问题 | owner 建议 | 期限建议 |
|---|---|---|---|---|
| L1 | 待生产验证 | A2"回复体现优先服务"人工判定（真实模型）；指标 4（注入保鲜度 ≥80%）与指标 5（衰退误伤率 ≤5%）上线后观测期统计；硬指标 1/2 的生产复核（人工覆盖 0% 审计对账、删除完整性自动化校验脚本）随上线执行；测量方案与数据源已就绪（§5） | 研发负责人出具测量数据，产品负责人判定 | 上线后 30 天观测期内 |
| L2 | P3（已知限制，R21 裁定） | 存量数据不回溯脱敏（PII 覆盖率口径为验收期构造数据 + 新写入，06 R3）；Phase 2/3 存量明文如需合规清理另行立项评估 | 合规负责人 + 研发负责人：评估存量清理立项 | 后续迭代评估 |
| L3 | P3（后续项） | CI 尚无 pgvector 矩阵 job（Phase 3 R13 裁定不阻塞验收先例，06 R4）；本地双容器矩阵已全绿，ci.yml 单 `postgres:17` service 加 `matrix.image` 即可 | QA/研发：后续 PR 增加并在 PR 描述说明 | 下一迭代 |
| L4 | P3（预存，非本阶段） | tickets-antd.spec.ts 2 例 e2e 失败为 Phase 2 起记录的登录态超时类别，本分支零新增（06 R1：dev 未改代码 HEAD 复现 + QA 独立复跑同特征） | 沿用 Phase 2/3 验收遗留跟踪 | 下一迭代 |
| L5 | P3（预存 flake，非本阶段） | main 侧 `test_model_gateway.py::test_timeout_and_cancellation_release_slots` 挂起形态同 Phase 2/3 记录（06 R2）；全量回归以 deselect + 单独跑批计次执行；建议给 `entered.wait()` 加超时 | QA/研发：改造该用例超时形态 | 下一迭代 |
| L6 | pending_human | UAT 运营/客服主管真人走查（打标/合并/删除二次确认体验、pending 队列处理、观测页指标解读）：本轮以代码核对 + Playwright 证据代验通过（§4），建议上线前走查一次（非阻断） | 产品负责人 + 运营/客服主管 | 上线前 |

## 8. 结论与签字

**结论建议：有条件通过。**

- A1~A13 全部通过（双检索模式各执行一遍；时间类场景 A3/A8/A10/A11/A12 按改库构造 + 手工触发单轮手工实测；A2 拆分为"注入侧双模式已通过 + 回复质量随 L1 待生产验证"，与 Phase 1/2/3 处理先例一致）；
- 一票否决项零触发：A4（PII）/A5（删除闭环）双模式手工实测通过，合规 C1~C7 全部通过（C1~C4 含全库扫描与三层纵深逐一实测）；
- 硬指标三项达标：人工覆盖 0% 与删除完整性 100% 机制层双模式实测通过（生产复核随上线执行），PII 覆盖率按 R21 验收口径实测 100%；后两项运营指标（保鲜度/误伤率）按方案 §8 允许上线后观测期内跟踪（L1）；
- 无 P0/P1 遗留（D1~D5 均已修复并回归关闭）；遗留 4×P3（L2/L3/L4/L5）+ 待生产验证（L1）+ 人工走查（L6），均有 owner 与期限建议，符合验收方案 §8"有条件通过"标准；
- 最终"通过"结论须在 L1 生产测量回填（硬指标对账 + 两项运营指标观测）与 L6 走查完成后由产品负责人确认升级；
- UAT 操作清单（§4）逐条核对通过，无阻断问题；端到端 UAT 旅程（方案 §5 跨阶段旅程）的真人执行与 L6 一并组织。

| 角色 | 签字 | 日期 |
|---|---|---|
| 产品负责人（含 L1 测量回填后的结论升级确认、L6 走查组织） | 待签 | |
| QA | 验收代理（本轮自动化执行 + 手工实测）：通过 | 2026-10-15 |
| 研发负责人（含 L1 测量数据出具） | 待签 | |
| 运营/客服主管（UAT 走查） | 待签 | |
| 合规/安全负责人（C1~C4 专项复核） | 待签 | |
