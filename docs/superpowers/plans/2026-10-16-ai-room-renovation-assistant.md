# 单房间 AI 改造助手 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在 8000/console 中为单个已完成房间生成 2–3 个经正式链路验证的 DeepSeek Flash 改造方案，并在保存前查看每个方案的改后框架图与证据。

**Architecture:** DeepSeek 只输出受限空间编辑与设计意图；后端最多两轮生成，逐组检查编辑、几何和正式评估结果。新工作台页面复用现有 `scheme3d.mjs` 显示每组独立的前后布局，保存时扩展既有方案记录。

**Tech Stack:** Python 3.12、FastAPI、SQLAlchemy/Alembic、pytest、原生 ES Module、Three.js、Node 测试；DeepSeek Chat Completions JSON 输出，使用 Python 标准库 HTTP 客户端。

**Spec:** `docs/superpowers/specs/2026-10-16-ai-room-renovation-assistant-design.md`

## Global Constraints

- 新生成流程只在 `http://127.0.0.1:8000/console` 的已完成单房间 Scan 中可用；整屋 Scan 和 Flutter 报告页不接入。
- 候选最多三组、至少两组可信且有区别；不足时至多修订一轮并说明失败，不能用未经验证的方案凑数。
- `deepseek-flash` 只生成结构化编辑与设计意图；分数、指标、风险和框架布局来自每组独立运行的 `build_scheme`。
- 视频、图像、点云、账户数据、密钥均不得进入模型上下文；真实扫描产物与正式报告只读。
- 每个候选必须能用现有 `scheme3d.mjs` 的 `layout_before/after` 显示；历史方案仍可读。
- 用户指令禁止批量删除文件或目录；本计划不要求删除文件。

## Review Focus

- 模型给出不存在的家具 ID 或 `NaN`/超大位移：拒绝该组并反馈修订，绝不落入框架图。
- 两组只有同一家具位移数值稍有不同：当作重复策略，不能凑成两个选择。
- 扫描在生成和保存之间被重算：保存返回冲突并让用户重新生成。
- 模型超时、空内容、截断 JSON 或 401/429：工作台给可重试错误，响应和日志不带密钥或堆栈。
- 恶意方案标题或描述含 HTML：作为文本显示，不能执行脚本。

---

### Task 1: DeepSeek JSON 客户端与安全配置

**Files:**
- Create: `backend/assistant/deepseek_client.py`
- Modify: `backend/app/config.py`
- Modify: `backend/.env.example`
- Test: `backend/tests/test_deepseek_client.py`

**Interfaces:**
- Produces: `request_candidates(context: dict, feedback: list[dict] | None, *, api_key: str, model: str = "deepseek-flash", endpoint: str = "https://api.deepseek.com/chat/completions", timeout_s: float = 20) -> list[dict]`；异常类型 `DeepSeekUnavailable` 与 `DeepSeekInvalidResponse`，都不含密钥。
- Config: `Settings.deepseek_api_key: str | None`、`Settings.deepseek_model: str`、`Settings.deepseek_timeout_s: float`；本地 `.env` 存放实际密钥，仓库仅提交变量名示例。

- [ ] **Step 1: 写失败测试。** 用替身 `urlopen` 断言请求模型为 `deepseek-flash`、`response_format` 为 `json_object`，Authorization 仅出现在请求头；正常 JSON 返回候选数组；空内容、截断响应、HTTP 401/429 和超时产生不含凭据的受控异常。
- [ ] **Step 2: 验证测试失败。** 从 `backend` 执行 `./.venv/Scripts/python.exe -m pytest tests/test_deepseek_client.py -q`，预期因模块/接口未实现而失败。
- [ ] **Step 3: 实现客户端和配置。** JSON 提示只请求编辑及意图，配置较小的输出上限；校验 `finish_reason` 和最外层 `proposals` 数组；HTTP 响应大小设上限，密钥只用于后端请求头。
- [ ] **Step 4: 重跑测试至通过。** 同 Step 2 命令，预期全部通过。
- [ ] **Step 5: 提交。** 仅提交上述代码、测试和 `.env.example`，不提交 `backend/.env`。

### Task 2: 候选验算、修订和房间 API

**Files:**
- Create: `backend/assistant/proposal_engine.py`
- Create: `backend/app/routers/assistant_proposals.py`
- Modify: `backend/app/main.py`
- Modify: `backend/tests/conftest.py` (non-deleting test media isolation required by user instruction)
- Test: `backend/tests/test_assistant_proposals.py`

**Interfaces:**
- Consumes: Task 1 的 `request_candidates(...)` 与受控异常；`pipeline.scheme_assessment.build_scheme` 和 `diff_assessments`。
- Produces: `generate_room_proposals(measurements: dict, structure: dict, goal: str | None, request_fn: Callable) -> dict`，含 `proposals`、生成状态及拒绝原因；`POST /api/assistant/scans/{scan_id}/proposals` 请求 `{goal?: string}`，返回每项 `title`、`intent`、`edits`、`layout_before/after`、`comparison`、`metric_changes`、`risk_changes`、`evidence`、`limitations`、`disclaimer` 与输入指纹。

- [ ] **Step 0: 修正测试隔离。** 现有 `clean_db` 夹具会递归删除测试目录，违反用户指令。先改为每个测试分配唯一的、位于 `backend/.test-data/` 下的 `DATA_DIR`，同步更新环境变量与缓存 Settings 对象；测试结束不删除文件或目录。既有数据库表重建行为保持。
- [ ] **Step 1: 写失败测试。** 复用单房间结构化测试夹具，替身模型给三组不同编辑；断言每组数值等于独立 `build_scheme`。再覆盖 Review Focus 的不存在 ID、非有限数字、重复策略，以及首轮不足时恰好修订一次；两轮后不足返回明确失败。API 测试确认 `status=done` 且 `capture_type` 为 `video` 或 `photos`，缺数据、整屋、跨机构均被拒绝。
- [ ] **Step 2: 验证测试失败。** 从 `backend` 执行 `./.venv/Scripts/python.exe -m pytest tests/test_assistant_proposals.py -q`，预期缺少新接口或断言失败。
- [ ] **Step 3: 实现验算与 API。** 构造最小结构化上下文；白名单化编辑操作和参数范围，按操作/目标/效果去重；冲突、无生效编辑、不可评估或无可核查改善者不推荐。只有合格候选进入响应；一次修订接收结构化拒绝原因，异常映射为不泄密的 4xx/5xx。
- [ ] **Step 4: 重跑测试至通过。** 同 Step 2 命令，预期全部通过。
- [ ] **Step 5: 提交。** 提交本任务文件与测试。

### Task 3: 保存所选方案及元数据

**Files:**
- Create: `backend/migrations/versions/20261016_0006_scheme_proposal_meta.py`
- Modify: `backend/app/models.py`
- Modify: `backend/app/routers/renovation.py`
- Test: `backend/tests/test_renovation_schemes_api.py`

**Interfaces:**
- Consumes: Task 2 的候选 `edits`、`title`、`intent`、`evidence`、`limitations`、`input_fingerprints`。
- Produces: `RenovationScheme.proposal_meta` JSON 字段；`POST /api/assistant/scans/{scan_id}/schemes` 扩展可选 `based_on` 和 `proposal_meta`，`GET` 列表/详情返回元数据。旧请求体保持兼容。

- [ ] **Step 1: 写失败测试。** 保存 AI 方案后断言说明和服务端重算证据可读；旧方案没有元数据仍可读；指纹不匹配返回 409；模型说明过长返回 422；恶意 HTML 说明只作为文本保存；迁移升级后已有行可读。
- [ ] **Step 2: 验证测试失败。** 从 `backend` 执行 `./.venv/Scripts/python.exe -m pytest tests/test_renovation_schemes_api.py -q`，预期新字段相关测试失败。
- [ ] **Step 3: 实现迁移与接口。** 新 JSON 列默认 `{}`，`down_revision="20260926_0005"`；保存时重算方案、检查扫描输入指纹、长度限制和数据来源，证据值由服务端产生。旧方案查询不依赖新元数据存在。
- [ ] **Step 4: 重跑方案测试和迁移检查。** 同 Step 2 命令；将 `DATABASE_URL` 临时设为 `sqlite:///./tmp/assistant-migration-check.db` 后执行 `./.venv/Scripts/python.exe -m alembic upgrade head`，预期测试通过且隔离数据库迁移成功。
- [ ] **Step 5: 提交。** 提交迁移、模型、路由和测试。

### Task 4: 工作台三案框架视图

**Files:**
- Create: `backend/app/static/console/js/views/assistant.js`
- Create: `backend/app/static/console/js/views/assistant_model.mjs`
- Create: `backend/app/static/console/css/assistant.css`
- Modify: `backend/app/static/console/index.html`
- Modify: `backend/app/static/console/js/app.js`
- Modify: `backend/app/static/console/js/api.js`
- Modify: `backend/app/static/console/js/views/room.js`
- Modify: `backend/app/static/console/js/views/scheme.js`
- Test: `backend/tests/test_console_assistant.cjs`

**Interfaces:**
- Consumes: Task 2 的候选响应和 Task 3 的保存接口；`createSchemeViewer`/`diffLayout` from `scheme3d.mjs`。
- Produces: 路由 `#/scans/{scan_id}/assistant`；`api.generateRoomProposals(scanId, goal)`；纯函数 `candidateViewModel(proposal)` 与 `proposalSavePayload(proposal, fingerprints)`，供页面及 Node 测试共用。

- [ ] **Step 1: 写失败测试。** Node 测试断言各候选独立布局/分数绑定，切换前后所用布局正确，保存载荷含指纹和元数据，标题/描述按文本转义；验证房间页助手入口实际导航到新页面且旧模拟控件不呈现（测试用户可见行为，不搜索源码）。
- [ ] **Step 2: 验证测试失败。** 从 `backend` 执行 `node tests/test_console_assistant.cjs`，预期因新模块或路由缺失失败。
- [ ] **Step 3: 实现新页面和入口。** 房间页顶部按钮进入新路由；三案卡选择控制同一 3D viewer，未保存即可看前后；卡片呈现服务端证据、限制和保存动作。生成中显示进度，离开页面时中止页面请求。旧模拟面板从工作台移除；方案详情显示保存的说明。复用工作台 token、主题、组件与 `scheme3d.mjs`，窄屏折叠且保留键盘焦点样式。
- [ ] **Step 4: 重跑测试及静态回归。** 执行 `node tests/test_console_assistant.cjs`、`node tests/test_console_scheme3d.cjs` 与相关后端测试，预期通过；在运行中的 8000/console 对真实房间做一次受控模型调用，验证候选切换、改后框架图、保存与重读。
- [ ] **Step 5: 提交并核查。** 更新 `README.md` 和 `docs/INDEX.md` 的助手入口说明；运行 `git diff --check`、目标测试与 `git status --short`，提交本任务文件与文档，不包含密钥。
