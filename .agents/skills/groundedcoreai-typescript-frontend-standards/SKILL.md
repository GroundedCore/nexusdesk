---
name: groundedcoreai-typescript-frontend-standards
description: nexusdesk 前端 TypeScript/React 代码规范与最佳实践检查。当编写、修改或审查 frontend/ 下的代码（React 组件、页面、hooks、API 调用、i18n 文案）或 sdk/javascript 时使用；也用于 code review 时核对前端规范。不适用于后端 Python 代码。
---

# nexusdesk 前端开发规范

适用范围：`frontend/`（包名 `agent-platform-console`），另 `sdk/javascript` 参照本规范的 TS/JS 部分。

## 技术栈基线

- React **19** + TypeScript **~5.9**（strict），构建走 `tsc --noEmit && vite build`
- Vite 7，ESM only（`"type": "module"`）
- UI 组件库：**antd v6** + `@ant-design/icons`，禁止引入第二个组件库
- 国际化：**i18next + react-i18next**，文案资源在 `src/i18n/locales/`
- E2E：Playwright（`npm run test:e2e`）

## 目录结构约定

```
src/
├── app/        # 应用装配：路由、Provider、布局
├── features/   # 业务特性（agents、conversations、knowledge、memory……按域划分）
├── shared/     # 跨特性复用：api 客户端、通用组件
├── i18n/       # 语言资源与 LanguageSelector
└── styles/     # 全局样式
```

- 新业务页面/组件放进 `features/<域>/` 对应域目录；跨域复用的才提升到 `shared/`
- API 调用统一走 `shared/api`，禁止在组件里裸写 `fetch` 拼接 URL
- 目录名用 kebab-case，组件文件用 PascalCase（`AgentList.tsx`），工具/hook 用 camelCase（`useAgents.ts`）

## 代码风格硬性规则

1. **函数组件 + Hooks**，禁止 class 组件
2. **禁止 `any`**；优先从后端 schema 推导/复用类型；union 类型优于宽松 string
3. Props 用 `interface` 声明并显式导出；组件默认导出随意，但 hooks 必须具名导出
4. 状态管理：局部状态 `useState`/`useReducer`，服务端状态按现有 `shared/api` 模式处理；**不要**擅自引入 Redux/MobX 等新状态库
5. antd 按需引入已自动处理，但**禁止** `import antd from 'antd'` 全量导入写法
6. 样式：沿用项目现有方案（CSS 文件与组件同域放置，如 `language-selector.css`）；不要引入 CSS-in-JS 新方案
7. 副作用收敛在 `useEffect`/事件回调中，渲染函数保持纯净

## 国际化（i18n）红线

- **所有用户可见文案必须走 `t()`**，禁止硬编码中文/英文字符串
- 新增 key 时**所有语言文件**（`src/i18n/locales/` 下每个 locale）都要补齐，保持 key 集合一致
- 提交前必须跑：

```bash
cd frontend
npm run check:i18n   # 校验 locale 文件 key 一致性，必须通过
```

## 敏感信息与环境配置（红线）

1. **禁止硬编码**：密码、密钥、Token、后端地址等配置不得写死在代码、注释或 mock 数据中；必须通过环境文件维护
2. **环境文件**：本地配置放 `frontend/.env`（已被 .gitignore），经 Vite 的 `import.meta.env.VITE_*` 访问；新增配置项时**必须同步在 `frontend/.env.example` 添加占位符**
3. **禁止入库**：`.env`、`.env.*` 不得提交 git（仅 `.env.example` 放行）；提交前自查 `git status`
4. **前端无真机密**：打包进前端产物的一切内容都是公开的——真正的密钥（如 LLM API Key、数据库凭据）**禁止**以前端环境变量形式下发，必须留在后端，前端只经后端 API 间接使用

## React 19 注意事项

- ref 可直接作为 prop 传递（`ref` as prop），优先于 `forwardRef`
- 表单场景优先考虑 React 19 的 form actions / `useActionState`，但与 antd Form 混用时保持现状模式，不强行重写

## 完成前自检清单

```bash
cd frontend
npm run typecheck     # tsc --noEmit 必须零错误（build 前置条件）
npm run check:i18n    # i18n key 一致性
npm run build         # 构建必须成功
```

改动交互流程时，视情况补/跑 Playwright e2e（`npm run test:e2e`）。
