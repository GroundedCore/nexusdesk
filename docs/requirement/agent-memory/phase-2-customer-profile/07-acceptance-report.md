# Phase 2 · 客户实体 + 画像/偏好记忆 — 验收报告

> 状态：已完成（结论：**有条件通过**，见 §7）
> 创建：2026-10-13
> 依据：`05-acceptance-plan.md`（A1~A8、UAT 清单、业务指标、合规 C1~C5）、`02-requirements.md`（AC-1~AC-8 原文）、`06-test-report.md`（QA 测试执行结果）
> 执行：验收代理（自动化验收轮）；执行日期 2026-10-13；验收基线 HEAD `5d0f3aa`（已推送，CI run 38282001961 全绿）

## 1. 验收组织与范围说明

本轮为**自动化验收轮**：A1~A8 按验收方案在本机 Docker PG（真实 PostgreSQL 17）+ demo/mock 可编程模型环境逐条复现，执行手段为"QA 测试基建独立复跑 + 验收场景脚本 + Playwright 控制台操作核对"，判据核对视角为"业务场景是否成立"而非"用例是否通过"。与测试报告的分工：`06-test-report.md` 回答"用例过了吗"，本报告回答"业务场景成立吗、指标是否达成、风险是否可控"。

以下各项超出本环境交付能力，按 Phase 1 验收报告先例如实标记"待类生产/上线后测量"并给出测量方案就绪度结论（见 §4）：

- **画像正确使用率、抽取准确率、抽取误报率**（真实模型质量指标）：本机 demo/mock 模型不具备真实抽取与作答能力；
- **工单 customer_id 填充率**：方案定义为"上线后统计"，填充逻辑本体已在本机实测；
- UAT 的"运营/坐席代表人工可操作性判定"：本轮以 Playwright 端到端操作证据 + API 实测代替人工判定，建议上线前由坐席代表做一次真人走查（非阻断）。

**前置条件核对**：`04-test-plan.md` 全部用例通过（`06-test-report.md` §12 出口准则逐条满足：本机 Docker PG 全量 362 passed / 0 failed；CI 复跑全绿），无 P0/P1 未关闭；D1（重复决策 409）已修复并回归关闭。本轮验收在相同 HEAD 上对 Phase 2 全部场景用例做了独立复跑（非引用 QA 结论）：PG 场景 28 passed、单元/迁移 18 passed、Playwright memory 4 passed、部署形态 12 passed，另执行 2 个验收专项场景脚本（见 §3 证据列）。

## 2. 验收环境

| 环境 | 配置 | 承担场景 |
|---|---|---|
| 本机 Docker PG（macOS） | 容器 `nexusdesk-test-pg`（postgres:17-alpine，127.0.0.1:55432），库 `nexusdesk_test` 迁移至 head（0035，单头已核） | A1~A8 全部场景复现；合规 C1~C5；工单填充逻辑；迁移回滚 |
| 模型 | demo/mock（`RecordingGateway` 可编程：各档 confidence、update 序列、故障注入） | 全部自动化场景 |
| 前端 | vite dev server（5173）+ chromium headless，API mock | UAT 操作清单核对（Playwright） |
| 类生产（真实模型）+ 上线后数据 | — | 本轮不可用：3 项质量指标与填充率统计留待（§4） |

验收数据使用测试专用租户（每用例独立 `test-<uuid>` 租户），不与其他数据混用。

## 3. 功能验收记录（A1~A8）

执行人：验收代理（自动化）；日期：2026-10-13；环境：本机 Docker PG + demo/mock 模型（前端项为 vite+chromium）。
独立复跑证据：`test_memory_customers_postgres.py` 28 passed（43.23s）、`test_memory_customers.py` + `test_memory_customer_migrations.py` 18 passed、验收专项脚本 2 passed（`test_acc_a1_a2_returning_customer_full_business_scenario`、`test_acc_a3_reject_never_injects`，临时脚本执行后已删除）、Playwright `tests/memory.spec.ts` 4 passed（15.8s）。

| # | 判定 | 执行记录与实际结果 | 证据 |
|---|---|---|---|
| A1 | **通过（自动化层）**；"Agent 用英文问候"作答质量待类生产验证 | 前置：渠道 Agent + 渠道，session_id=`acc-session` 首次进线报"我叫张三，电话 13800005678，以后用英文回复我"，run completed。实际：①会话 `customer_id` 已解析，identity 键与会话 external_id 同源（`session_id_hash`）；②抽取任务落库并执行，name/phone/language 三条 active；③**控制台判据**：`GET /api/v1/memory/customers/{id}` 可见身份绑定与三条 profile/preference 条目（type/key/content/status 齐全）；④**Agent 判据（注入侧）**：第二、三次进线 run 的注入体含"【客户画像】name: 张三 / phone: 13800005678"与"【客户偏好】language: 英文回复"，注入位置在摘要之后、历史之前。"用英文问候"需真实模型推理，mock 不具备——注入内容完整进入模型输入已证，作答质量列入 §4 指标 1 测量 | 验收脚本 `test_acc_a1_a2`；P2-IT-01；P2-IT-08 |
| A2 | **通过** | 同一 `session_id` 三次进线（"我叫张三…"→"我又来了"→"还在吗"）：三次均绑定同一 conversation、同一 `customer_id`；`memory_customers`/`memory_identities` 全租户各仅 1 行（不重复建档）；第一轮抽取落库后第二轮注入即含累积记忆。另 P2-IT-05 佐证同会话多轮 run 的抽取去抖与窗口后再投递 | 验收脚本 `test_acc_a1_a2`；P2-IT-01/05；P2-UT-02 |
| A3 | **通过** | mock 输出 confidence=0.6 条目：①落库 `status='pending'`，下一次进线注入为 `None`（不含该条）；②控制台 confirm → active，再进线注入出现该条（P2-IT-07）；③**拒绝路径专项**：reject → deleted，随后两次进线注入均不出现且永不再现，pending 队列清空；④已决策条目二次决策 409（D1 修复后语义） | 验收脚本 `test_acc_a3_reject_never_injects`；P2-IT-07；P2-API-05 |
| A4 | **通过** | 控制台 PATCH 编辑"张三"→"张叁"：响应 `source_type='human'`、`confidence=1.0`、`revision=2`、`prev_content='张三'`（编辑前值可追溯）；service 层 `injection_items` 随即返回编辑后内容（下一轮对话注入编辑后内容）；审计 `memory.item.edited` 仅含 customer_id/key 不含内容。Playwright 管理端编辑-保存-列表刷新链路同步核对通过 | P2-API-03；Playwright memory(admin) |
| A5 | **通过** | playground 会话（`source='playground'`）提供"我叫李四，电话 13900002222"：run completed，`customer_id=NULL`，`memory_tasks`/`memory_identities`/`memory_customers` 全空；示例 Agent 经渠道进线同样零解析、零任务、零写入（双重排除，常驻断言） | P2-IT-04 |
| A6 | **通过** | 已绑定客户会话"请创建维修工单"：propose 提案 payload 携带 `customer_id`；approve 后 `tickets.customer_id`=customer UUID 字符串（兼容 VARCHAR(100)，不改表）；工单列表/详情均含该字段；无客户会话工单 `customer_id` 为空且流程不受影响。前端：工单详情"查看客户记忆"链接 → `#/memory/customers/{id}` 客户详情页跳转成功 | P2-IT-11/12；Playwright ticket-link |
| A7 | **通过** | 客户先说"我叫张三"（add address_name=张三，revision=1），改口"其实叫我张工就行"（update）：条目仅 1 条不堆叠，`content='张工'`、`prev_content='张三'`、`revision=2`；同键 add 撞键 ON CONFLICT DO NOTHING 亦不堆叠 | P2-IT-06；P2-UT-08/09 |
| A8 | **通过** | Agent 级：`memory_write=false` 发布后 run completed 但零新抽取任务（写关）；改回 `memory_write=true` + `memory_enabled=false` 后，注入返回 `None` 而抽取任务照常投递（读关）——读写互不影响；全局 `memory_extract_enabled=false` 零任务且身份解析不受影响；存量草稿无 memory 字段时两开关默认 true（向后兼容） | P2-IT-09/10；`test_agent_config_memory_switches_default_on_and_validate_legacy_payloads` |

## 4. 业务指标逐项判定

| 指标 | 目标 | 本轮判定 | 数据 / 验证方法与就绪度 |
|---|---|---|---|
| 回头客会话画像/偏好正确使用率 | ≥ 85% | **待类生产/上线后测量** | 需真实模型作答与人工判分，本机 mock 不具备。就绪度：方案（评估集 ≥20 个带预置画像回头客 case 跑批 + 人工判分）已在验收方案 §5 定义；前置链路已就绪——A1 证明画像/偏好以固定分节模板完整进入模型输入（注入位置 P2-IT-08），评估集可直接经渠道入口构造复用本阶段测试基建 |
| 抽取准确率 | ≥ 90% | **待类生产/上线后测量**（硬指标） | 需真实模型抽取产出 + 人工抽检 ≥100 条（分层 active/pending）。就绪度：抽检数据源已就绪——`memory_items` 含 status/confidence/source_ref 可回溯来源会话；审计 `memory.extract.applied` 按次留痕（P2-CM-03）；抽取调用落 `gateway_calls`（actor='memory'，可归因租户与成本，P2-CM-04）。mock 抽取的解析/路由/合并正确性已由 P2-UT-05~09 覆盖 |
| 抽取误报率 | ≤ 3% | **待类生产/上线后测量**（硬指标） | 同上抽检中"对话中并不存在该事实"的占比。就绪度同上；另 prompt 内含受控词表与"订单号"反例抑制（`test_extraction_prompt_carries_vocabulary_existing_keys_and_dialogue`），非法输出整批丢弃宁缺毋滥（P2-CM-04） |
| 工单 customer_id 填充率 | ≥ 95% | **待上线后统计**；填充逻辑**本机实测通过** | 方案定义为上线后统计（来源为已识别客户会话的新工单填充比例）。逻辑实测：绑定会话 propose→approve→`tickets.customer_id` 写入（P2-IT-11），未绑定会话为 NULL（P2-IT-12），存量会话不受影响（P2-IT-13）。上线后统计 SQL 已具备：`tickets.customer_id IS NOT NULL` 占比按会话来源分组即可 |
| 单轮注入 token 增量 | ≤ 1000 tokens | **通过（按项目既定估算口径实测）**；口径观察见 §6 L5 | 本机实测（`trim_items`+`render_memory_injection`，2 字符/token 与 Phase 1 摘要同约定）：①现实场景 30 条混合条目→封顶 20 条，注入体 1319 字符 ≈ 660 估算 tokens；②对抗场景 20×500 字符（MAX_CONTENT_CHARS 上限）→预算裁剪至 3 条，1527 字符 ≈ 764 估算 tokens；任意注入体 ≤ ~2000 字符 = 1000 估算 tokens 预算。生产复核方法：真实模型上线后按 `gateway_calls` 实际 prompt tokens 抽查 ≥20 个注入实例（与无注入基线对比） |

## 5. 合规核对（C1~C5，一票否决项）

| # | 核对项 | 判定 | 证据（本轮独立复跑） |
|---|---|---|---|
| C1 | 所有 memory 表查询强制 `tenant_id` 过滤；跨租户 API 访问返回 404 | **通过** | P2-API-06：他租户 customer 详情 404、列表不泄露（total=1）、service 层跨租户 edit/detail 抛 `DomainError`（不泄露存在性）；代码核查：routes 全部端点以 `user.tenant` 传入 service，service 层 25 处 `tenant_id` 谓词 |
| C2 | playground / 示例 Agent 零记忆写入 | **通过** | P2-IT-04（常驻断言）：playground 会话与示例 Agent 渠道进线均零解析、零任务、`memory_customers`/`memory_identities`/`memory_items` 全空 |
| C3 | 审计事件不含明文敏感信息 | **通过** | P2-CM-03：`memory.extract.applied`/`edited`/`deleted`/`restored` 四类审计 details 断言无内容字符（仅 customer_id/key/counts/conversation_id）；P2-API-03 编辑审计同样无内容 |
| C4 | 身份解析异常不阻断会话创建（降级为新客户，不错绑） | **通过** | P2-IT-03：注入 `resolve_customer` 异常（savepoint 隔离）→会话 202 正常创建、`customer_id=NULL`、warning 日志、run completed、零抽取任务——降级为"无客户"而非错绑他人 |
| C5 | 全局/Agent 级开关可独立关闭读与写，回退无需动迁移 | **通过** | P2-IT-09/10（A8 证据）；迁移 0035 纯增量且 downgrade 回滚后三表与 `customer_id` 列移除、存量会话保留（`test_customer_memory_migration_preserves_existing_conversations`，本轮复跑通过）；功能回退仅需配置（AgentConfig 字段 + `AGENT_MEMORY_*` 环境变量，P2-CM-02） |

## 6. 遗留问题清单

| # | 级别 | 问题 | owner 建议 | 期限建议 |
|---|---|---|---|---|
| L1 | P3（测试缺口） | 抽取防抖"窗口内任意状态任务阻止新投递"仅有 pending 态专测（P2-IT-05）；done/failed 态阻止行为经代码阅读确认正确（窗口查询无 status 谓词）但无构造用例（沿用 QA R1） | QA：增补 done/failed 态构造用例 | 下一迭代 |
| L2 | P3（环境观察） | 高负载并发下（全量 e2e 与 PG pytest 同跑）main 侧 `test_model_gateway.py::test_timeout_and_cancellation_release_slots` 假挂起，空闲复跑 3.69s 通过，证实负载敏感 flake，非本阶段缺陷（沿用 QA R2） | QA/研发：执行侧规避（PG 全量与 e2e 不并发） | 即时（已执行） |
| L3 | P3（过程改进） | 28 个 PG 用例在开发自报阶段从未真实执行（本机无 PG 全 skip），D1 直到 CI 才暴露（沿用 QA R3）；本机 `nexusdesk-test-pg` 容器已证明可行 | 研发负责人：含 PG 用例的分支自报"全绿"前须在本机 Docker PG 或等价环境跑一遍目标套件 | 下一迭代启动前 |
| L4 | 预存（非本阶段） | 前端全量 e2e 28 个失败（登录墙/真实后端依赖类），baseline 对照证实全部为预存失败子集、本分支零新增（`06-test-report.md` §8）；memory 4/4、channel-navigation 2/2 等本阶段相关用例全绿 | 前端负责人：按既有预存清单排期 | 后续迭代 |
| L5 | P3（口径观察，本轮新增） | 注入 token 预算按 2 字符/token 估算（与 Phase 1 摘要同约定）；以 tiktoken cl100k 实测，全 CJK 内容约 1 字符/token，极端 CJK 场景真实 token 可达估算口径约 2 倍（现实 20 条场景 1259 tokens、对抗 3×500 字符场景 1527 tokens），可超 1000 目标。典型画像/偏好条目（姓名/电话/型号等短文本）远低于阈值；另"首条保底"条款允许单条超大条目超预算（设计如此，P2-UT-11 固化） | 研发负责人：上线后以 `gateway_calls` 实际 prompt tokens 抽查复核；如需严格达标可收紧 `INJECT_MAX_TOKENS` 估算汇率或对 CJK 加权 | 上线后 2 周内复核 |
| L6 | 待测量 | 指标 1/2/3（画像正确使用率、抽取准确率、误报率）与指标 4 上线后统计（§4）；测量方案与数据源已就绪 | 研发负责人出具测量数据，产品负责人判定 | 上线后 2 周内 |
| L7 | pending_human | UAT 人工走查：本轮以 Playwright + API 证据代替坐席代表人工判定（§4 清单逐条核对通过），建议上线前真人走查一次（非阻断） | 运营/坐席代表 + 产品负责人 | 上线前 |

## 7. 结论与签字

**结论建议：有条件通过。**

- A1~A8 在自动化层全部通过（A1 的真实模型作答质量部分按判据拆分为"注入侧已通过 + 作答质量随指标 1 待测"，与 Phase 1 A1 处理先例一致）；
- 合规核对 C1~C5 全部通过，一票否决项零触发；无 P0/P1 遗留；
- 本机可测量硬指标（注入 ≤1000 估算 tokens、工单 customer_id 填充逻辑）实测通过；抽取准确率/误报率两项硬指标与画像正确使用率按方案定义需真实模型与上线后抽检，本轮判定"待类生产/上线后测量"且测量方案与数据源就绪（L6）——与 Phase 1 验收对同类指标的处理先例一致，最终"通过"结论须在硬指标测量回填后由产品负责人确认升级；
- 遗留 5×P3（L1/L2/L3/L5 + 预存 L4）均有 owner 与期限建议，符合验收方案 §7"有条件通过"标准；
- UAT 操作清单（§4）逐条核对通过，无阻断问题；人工走查列为 L7 非阻断项。

| 角色 | 签字 | 日期 |
|---|---|---|
| 产品负责人（含硬指标测量回填后的结论升级确认、L7 走查组织） | 待签 | |
| QA | 验收代理（本轮自动化执行）：通过 | 2026-10-13 |
| 研发负责人（含 L5 口径复核、L6 测量数据出具） | 待签 | |
| 运营/坐席代表（UAT 人工走查 L7） | 待签 | |
| 合规/安全负责人（C1~C5 判定复核） | 待签 | |
