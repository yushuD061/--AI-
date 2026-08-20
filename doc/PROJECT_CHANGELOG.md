# 项目修改记录

| 时间 | 修改位置 | 变更摘要 | 原因 | 验证方式 | 操作者 |
|---|---|---|---|---|---|
| 2026-08-20 10:30 +08:00 | `doc/问答计划书.md` | 新增 AI 真实取数问答、对话 CRUD、删除、上下文追问和测试计划 | 规划第二关可信 AI 问答功能 | 核对现有 FastAPI、React 和 SQLite 接口边界 | Codex |
| 2026-08-20 12:00 +08:00 | `backend/app/ai/`、`frontend/src/Assistant.tsx`、`README.md` | 实现 AI 问答、对话管理、LLM 配置面板、本地降级回答和真实数据事实展示 | 按问答计划书完成第二关基础功能 | 后端 11 项测试通过；前端生产构建通过；真实 API 问答返回 SQLite 数字 | Codex |
| 2026-08-20 12:30 +08:00 | `backend/requirements.txt`、`backend/app/ai/parser.py`、`frontend/src/App.tsx` | 接入 LangChain 结构化意图解析，并在看板增加 AI 问答跳转入口 | 使用成熟框架管理 LLM 链路并打通页面导航 | 后端 11 项测试通过；前端构建通过 | Codex |
| 2026-08-20 10:15 +08:00 | `backend/`、`frontend/.env`、`README.md` | 新增 FastAPI 聚合接口和 API 测试；前端切换为真实 API 数据源 | 完成数据看板后端并打通前后端 | `pytest` 7 项通过；真实 HTTP 接口和 `/docs` 返回 200 | Codex |
| 2026-08-19 18:45 +08:00 | `doc/前后端搭建计划.md` | 新增 FastAPI + React 数据看板搭建计划、API 合同和启动约定 | 固化第一关的实现边界和接口设计 | 核对 SQLite 数据表和清洗产物路径 | Codex |
| 2026-08-19 18:45 +08:00 | `README.md` | 补充当前数据清洗及后续看板启动说明 | 提供可复核的运行路径 | 核对命令与计划目录 | Codex |
| 2026-08-20 13:00 +08:00 | `doc/添加功能计划书.md`、`doc/PROJECT_PROGRESS.md`、`doc/PROJECT_CHANGELOG.md` | 将业务增强计划细化为四个阶段，登记阶段依赖、交付物、验收标准和当前未开始状态 | 明确数据基础、经营分析、日报导出和生产安全的实施顺序 | 核对现有 SQLite、FastAPI、React、AI 入口和测试目录；未修改业务代码 | Codex |
| 2026-08-20 13:20 +08:00 | `backend/app/`、`scripts/clean_data.py`、`frontend/src/`、`backend/tests/test_stage1.py` | 完成阶段一数据质量、清洗历史、确定性异常、全局已读状态和看板联动 | 提供可追溯的数据可信基础与运营异常入口 | `pytest` 20 项通过；Vite 生产构建通过；桌面及 390px 浏览器验收通过 | Codex |
| 2026-08-20 13:27 +08:00 | `frontend/src/App.tsx`、`frontend/src/styles.css` | 异常提醒面板增加展开/收起按钮，收起后保留标题和未读数量 | 减少异常列表占用页面空间，方便运营按需查看 | Vite 生产构建通过；浏览器验证展开、收起及移动端布局，控制台无错误 | Codex |
| 2026-08-20 15:10 +08:00 | `backend/app/services/phase2.py`、`backend/app/ai/`、`frontend/src/`、`backend/tests/test_phase2.py` | 完成阶段二周期比较、门店排名/诊断、商品结构、衰退告警、AI 双周期事实回答和三标签看板 | 将阶段一可信数据基础转化为可复核的经营分析工作流 | `pytest backend/tests tests -q` 24 项通过；`npm run build` 通过；8002/5176 本地服务浏览器验证桌面、390px、标签切换、异常收起、控制台无错误；提交前请执行 `git status` 与 `git diff` | Codex |
| 2026-08-20 15:30 +08:00 | `backend/app/services/reports.py`、`backend/app/state.py`、`backend/app/main.py`、`frontend/src/`、`backend/tests/test_stage3.py` | 实现不可变日报版本、质量阻断、CSV/XLSX/PDF 快照导出和运营日报标签 | 将经营分析转化为可追溯、可下载的日常工作流 | 28 项测试通过；Vite 构建通过；真实 API 三格式返回 200；XLSX 5 个工作表可重新打开，PDF 可解析并完成 PNG 渲染检查；复验时浏览器安全策略再次阻止 `127.0.0.1:5181` UI 自动验收，阶段三暂记 90%；提交前请执行 `git status` 与 `git diff` | Codex |
| 2026-08-20 16:10 +08:00 | `backend/app/auth.py`、`backend/app/state.py`、`backend/app/main.py`、`backend/app/services/analytics.py`、`backend/app/services/phase2.py`、`backend/app/services/alerts.py`、`backend/app/services/reports.py`、`backend/app/ai/conversations.py`、`backend/app/ai/facts.py`、`frontend/src/api/auth.ts`、`frontend/src/main.tsx`、`frontend/src/App.tsx`、`frontend/src/Assistant.tsx`、`backend/tests/test_stage4.py` | 增加四角色登录、会话、门店范围隔离、按用户异常已读、日报/AI/LLM/导出/质量/审计权限及敏感字段过滤 | 完成生产前数据隔离和关键操作留痕 | `pytest backend/tests tests -q` 30 项通过；`npm run build` 通过；真实 API 店长跨店 403、管理员审计包含 request_id；浏览器安全策略阻止桌面/390px UI 验收，阶段四暂记 90%；提交前请执行 `git status` 与 `git diff` | Codex |
| 2026-08-20 16:25 +08:00 | `frontend/src/App.tsx`、`frontend/src/Assistant.tsx`、`frontend/src/styles.css`、`doc/PROJECT_PROGRESS.md`、`doc/添加功能计划书.md` | 使用本机 Chrome 完成阶段三、四 UI 验收；修复登录后 Hook 顺序崩溃、换号残留数据、只读操作入口显示及 AI 路由重写 | 关闭阶段三、四剩余浏览器验收项 | Chrome 桌面和 390px `scrollWidth=clientWidth`；日报生成 v2、切换 v1、CSV/XLSX/PDF 请求 200；管理员显示 LLM 配置，只读仅显示 S01/S02 且无生成/导出/配置/删除入口；异常收起后行数 20→0；新标签控制台无错误；30 项测试与 Vite 构建通过 | Codex |
| 2026-08-20 17:27 +08:00 | `README.md`、`backend/.env.example`、`frontend/.env.example`、`doc/PROJECT_PROGRESS.md` | 按 GitHub 项目主页结构重写 README，并增加后端与前端环境变量模板 | 完整描述项目并提供不含真实密钥的可复制配置入口 | 对照应用配置读取代码复核变量；检查 Markdown、Git 忽略规则与 diff | Codex |

提交前请执行 `git status` 和 `git diff`，确认变更后再提交并推送。
