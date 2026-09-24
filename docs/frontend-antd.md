# Ant Design 首轮接入

采用 Ant Design 6.6.5 与 @ant-design/icons 6.3.4，保留 React 19、TypeScript 和 Vite，不引入 Ant Design Pro。

- main.tsx 使用 ConfigProvider 统一中文语言、黑白灰主题与圆角，Ant App 提供反馈上下文。
- 公共框架使用 Layout、Button、Avatar、Tag，导航按客服工作、配置管理和质量运营分组，支持收起和窄屏显示。
- 原全局 CSS 放入 legacy CSS layer，降低对新组件的覆盖；新布局独立使用 console.css。原聊天与模块页面保持自定义组件。
- 工单页面按需加载，使用 Table、Drawer、Descriptions、Form、Input、Select 和反馈组件。
- 支持标题/编号搜索、状态筛选、客户端分页；明确只针对 GET /tickets 返回的最近 100 条，不伪装成全库分页。
- 当前只展示最新备注，未接入完整时间线、分配接口或字段设计器。负责人没有名称目录时显示短 ID，不虚构人员信息。
- 更新失败保留处理说明；可以加载最新版本后重新检查并提交。viewer 只读，关闭工单只读。

配置参考：[Ant Design ConfigProvider](https://ant.design/components/config-provider)、[主题](https://ant.design/docs/react/customize-theme/)。

验证包含 TypeScript/Vite 构建，以及 tests/tickets-antd.spec.ts 的管理员/只读角色、筛选、错误保留和窄屏测试（使用明确的模拟 API）。既有两条端到端流程使用真实测试 PostgreSQL 与 demo 模型。

仍有约 500 KB 以上的构建块告警，已通过工单页面懒加载降低入口体积；后续逐页迁移时继续拆分。此轮不以调整告警阈值掩盖包体积。

本轮验证结果：4 条 Playwright 流程通过（2 条真实测试后端业务流程＋2 条模拟接口工单交互），TypeScript 检查及生产构建通过，并检查了工单抽屉截图。临时后端和测试 PostgreSQL 已关闭，保留原前端开发服务。

## 参考图风格调整

按用户参考图改为浅灰侧栏、白底居中内容、无边框浅灰卡片、黑色胶囊按钮和少量蓝紫色强调；保留产品自身名称和业务信息。导航使用统一图标和浅灰选中态，旧模块样式通过 legacy layer 同步色调。此次构建与两条工单交互测试通过，检查了列表/抽屉截图及窄屏溢出；未启动后端或修改数据。

## 视觉与操作收口

去掉工单中心重复标题，状态下拉改为可横向溢出收纳的页签，刷新移动至搜索工具栏。筛选或搜索变化重置分页；仍限定最近 100 条。详情分组为基础信息、问题描述和最近处理信息，移除面向客服的版本号展示；操作区使用 Drawer footer 固定于底部，仅一个主按钮。导航行距收紧、辅助文字对比度提高、表单圆角收敛。

本轮生产构建通过（保留既有大块体积告警），2 条模拟 API 的 Playwright 测试通过，覆盖页签筛选、只读角色、提交失败输入保留与移动端底部按钮可见性；已检查桌面列表和手机抽屉截图。未修改或启动后端。
