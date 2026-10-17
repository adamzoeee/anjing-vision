# 小助手重构交接说明

更新日期：2026-10-17

## 新增：不满意后的重试与新视频重传

- 方案生成完成后，点击“不满意，重新处理”：可仅重新生成方案，也可由管理员重新上传新视频。重传必须选择文件，不复用旧扫描媒体。
- 新视频创建同项目的独立扫描，旧扫描、报告、点云和已保存方案不覆盖。新扫描创建请求 `defer_processing=true`，状态为 `awaiting_video`；服务端保留手动启动模式，关闭窗口后可从项目列表继续上传。
- 上传完成进入 `uploaded`，不派发任务；点击“开始重新重建与分析”才调用 `POST /api/scans/{id}/start`。项目列表、待开始房间页均可恢复启动。完成后使用新扫描的 AI 改造助手生成方案。
- 启动接口检查管理员与机构归属，并原子领取状态，防止重复启动；队列不可用时保留新视频并允许重试。已完成或正在处理的扫描不可被上传覆盖。原普通新建扫描的自动启动默认行为保持兼容。
- 此处“重新训练”指复用既有视频重建、结构识别与风险分析管道，不训练 DeepSeek 权重；部署仍需原有 SLAM3R/SpatialLM 运行环境。本轮验证使用隔离数据库及任务分发替身，不运行真实重建、不发送媒体给 DeepSeek。无需新增数据库迁移。
- 最终后端回归632通过、1跳过、1条已知坏ZIP重复条目警告（按安全要求排除旧校准清理测试）；浏览器验证包含重新生成、新文件必选、上传中断后从项目恢复、队列失败重试及关闭后从房间继续启动。同模型审查发现的恢复模式问题已修复并复核。

## 本轮续接结果（优先阅读）

- 已从远端 `feat/ai-room-assistant` 的 `f185cb6` 继续，而非从旧点云分支重新实现；已合并 `main` 的 `f99618e`。保留最新登录、账号管理、home-plan 背景及现有工作台改动。
- starter 的门宽及移除候选修复已复用，并补上单调时钟的5秒搜索预算，仍最多40次验算、4个 starter，不放宽几何或可信度校验。
- 两套迁移均保留：账号管理 `20261016_0006` → 方案元数据 `20261016_0007`。隔离库升级与旧方案兼容测试通过；只读成员不可保存方案，管理员保存时仍重算指纹、几何及证据。
- 发现并修复 Windows 将 `.mjs` 返回为 `text/plain` 导致整页空白的问题；静态服务对成功的模块响应显式返回 JavaScript MIME，保留原缓存策略。已用真实服务与模拟错误注册表的回归测试验证。
- 扫描46真实 DeepSeek 请求返回200，一轮生成3案；一个候选经保存API写入隔离库并读回，标题、编辑、服务端证据和未过期状态一致。已获用户许可，仅发送精简结构与风险，不发送媒体、点云、账号。
- 重点后端回归202通过、1跳过；最终完整回归620通过、1跳过（按安全要求排除 `test_preview_frame_calibration.py`），1条警告来自校验坏ZIP的重复条目测试。前端行为测试通过，覆盖卡片切换、前后3D、保存回读、取消、错误、窄屏及成员权限；真实网页也完成生成、切换及保存详情验收。同模型独立审查未发现P1/P2。
- 本机续接目录：`D:/部署文件/anjing-vision-assistant-20261017`，本地分支追踪 `origin/feat/ai-room-assistant`；真实验收服务为 `http://127.0.0.1:8001/console`，使用 `backend/tmp/assistant-live-20261017.db` 与复制的展示数据。未升级或替换原8000服务、正式数据库和点云。
- 接手方部署仍用 `8000/console`：先备份其实际数据库，再运行迁移与重启。下文的 `E:/`、`C:/Users/Adamz/`、422与旧迁移状态均为上一台电脑的历史记录，不表示这些路径或失败仍存在于本机。
- 代理保持用户当前选定模型（本轮6.1 Sol），包括审查子代理；下文旧的模型档位约束不覆盖用户最新要求。
- 已知非阻塞项：候选按数组索引选取，尚未增加规格所述的临时候选ID；不影响当前切换、验算及保存。
- 上传前主分支由 `f99618e` 重写为 `66ffcde`；两者Git tree完全一致，无新增文件内容。助手分支以普通合并保留这条最新主分支历史，不强推、不覆盖队友改动。最终提交可从 `feat/ai-room-assistant` 的分支历史核对。

## 要完成的目标

完成“小助手”重构，并在唯一工作台入口 `http://127.0.0.1:8000/console` 提供可用体验。小助手只处理**单个房间**：用户输入目标，AI 生成 2–3 个经过服务端几何与效果校验的方案，用户可在候选卡片和同一 3D 框架视图中切换改造前后，选择一个方案保存并回读。

用户已经确认：

- 不再使用旧的小助手入口，业务入口只有 `8000/console`。
- 采用“AI 提案、服务端校验、失败后修订一轮”的流程。
- 采用候选卡片和可切换改造前后的 3D 框架图。
- 保存规则写入设计文档。
- 按实施计划使用子代理逐项实施。
- AI 服务使用 DeepSeek `deepseek-flash`。

用户此前在对话中提供了 API 密钥。**不要将密钥写入文档、代码、命令输出、提交或日志，也不要重复展示。** 检查配置时只报告是否已设置，不显示值。

## 已有设计和计划

- 设计：[2026-10-16-ai-room-renovation-assistant-design.md](specs/2026-10-16-ai-room-renovation-assistant-design.md)
- 实施计划：[2026-10-16-ai-room-renovation-assistant.md](plans/2026-10-16-ai-room-renovation-assistant.md)
- 进度账本（位于忽略目录）：`C:\Users\Adamz\.codex\worktrees\ai-room-assistant\old-friend\.superpowers\sdd\2026-10-16-ai-room-renovation-assistant\progress.md`

主要已实现内容包括 DeepSeek JSON 客户端、房间候选提案 API、服务端方案校验与可信度证据、保存时再次校验、方案元数据迁移、工作台助手页面与共享 3D 视图、前端行为测试。此前代码评审修复过门洞与家具几何、旋转语义、过期输入校验、客户端分数不可信等问题。DeepSeek thinking 模式已关闭，以避免 JSON 输出被思考 token 挤占。

## 当前工作树和分支状态

1. 原始项目目录：`E:\.PJs\old-friend`，分支 `codex/renovation-sandbox`。它已前进到提交 `d996ff4`（账号管理改造）。**工作区有用户未提交改动，必须保留：**
   - `backend/app/static/console/css/base.css`
   - `backend/app/static/console/js/app.js`
   - `backend/app/static/console/js/background3d.js`
   - `backend/app/static/console/js/views/login.js`
   - `backend/app/static/console/js/views/users.js`
   - 未跟踪的 `backend/app/static/console/js/home-plan.mjs`

2. 小助手实现工作树：`C:\Users\Adamz\.codex\worktrees\ai-room-assistant\old-friend`。已提交到 `ebdc2bc`，但“starter 候选菜单”修复还在未提交状态，改动涉及：
   - `backend/assistant/proposal_engine.py`
   - `backend/tests/test_assistant_proposals.py`
   - `docs/superpowers/specs/2026-10-16-ai-room-renovation-assistant-design.md`

   子代理在中断前报告这批修改的回归测试为 145 passed、1 skipped，尚未完成提交和代码复核。先查看此工作树实际状态，继续修复、提交并复核，不要丢弃这些改动。

3. 合入工作树：`C:\Users\Adamz\.codex\worktrees\assistant-integration\old-friend`，起点 `d996ff4`。小助手分支正在与账号管理提交合并时被中断，当前存在未解决冲突及暂存/未暂存的部分合并结果。**先检查 `git status`，从现状继续解决冲突，不要重置或覆盖工作树。**冲突文件包括 README、`backend/app/main.py`、`backend/app/routers/renovation.py`、控制台 `room.js`、测试 conftest 和文档索引等。

## 阻塞问题和最近实测

真实 DeepSeek 调用确认 thinking 关闭后 JSON 能正常返回。但首次真实房间提案返回 422：模型给出的移动在 Scan 46 房间中无法通过几何校验。为提高成功率，`ebdc2bc` 增加了服务端先验算候选 starter、再让模型选择和解释的路径；AI 返回仍会重新经过完整服务端验证，不存在绕过校验的后门。

这条 starter 路径的真实回归目前仍失败：Scan 46 菜单只含一个“移动书架”动作，模型按提示原样选择后，端点因候选不足仍返回 422。独立评审标为 P1。房间此前已确认存在的有效单步编辑包括：门宽调整到 0.9 米、移除 `bookshelf_002`、移除 `table_002`、移动 `bookshelf_001`（例如 `dx=-0.2`）。现有 starter 搜索偏向前几个物体的移动，且没有覆盖移除操作；按公平轮转搜索门宽、移除、移动和旋转等候选，保持严格数量与计算上限。

最近一次状态检查：`8000`、`8001`、`8002` 的 `/console` 与 `/api/health` 均返回 200。`8000` 对应原始项目目录旧服务；`8001` 和 `8002` 是小助手工作树上的隔离验证服务。此前 `8002` 的提案调用仍返回 422。重启/验证时先确认进程工作目录和数据库配置，避免将隔离数据误写入正式库。

## 迁移合并必须处理

账号管理提交 `d996ff4` 和小助手分支各自使用了 Alembic revision `20261016_0006`，并且都从 `20260926_0005` 延伸。合入时保留两套迁移：将小助手元数据迁移改为唯一 revision（例如 `20261016_0007`），并让它依赖账号管理的 `20261016_0006`。检查数据库当前 Alembic 版本并在隔离库验证升级路径。

原始目录正式 SQLite 数据库此前读到版本 `20260809_0004`；升级前需停止对应服务，并用 SQLite backup 生成**单个明确备份文件**。不能将 Scan 46 插入正式数据库做测试。隔离 smoke DB 在小助手工作树 `backend/tmp/assistant-live-smoke-20261017.db`，曾标记为 AI 版本 `20261016_0006`；若改了迁移 revision，重新从安全副本创建新的隔离测试库更稳妥。

## 接下来按顺序做

1. 在小助手工作树审阅并完成 starter 菜单修复，至少保证 Scan 46 能提供 2 个不同、各自有效的候选。保留模型独立选择与叙述，并在生成后重新校验。
2. 重新运行 Scan 46 的真实 DeepSeek 提案 smoke；预期返回 2–3 个方案。把一个方案经保存 API 写入隔离 DB，再读回验证标题、编辑与服务端证据。不要只用假模型测试替代真实调用。
3. 完成代码评审，确认 starter 改动和原功能没有 P1/P2 缺陷。
4. 在合入工作树继续处理 `d996ff4` 与小助手提交间的冲突，保留账号管理功能、用户当前工作台变更的兼容性，并修好 Alembic revision 顺序。运行需要的安全后端和 Node 测试。
5. 再查看原始目录的工作区状态；**不要覆盖、清理或 stash 丢弃其中的用户改动**。用合入工作树启动已集成服务，按备份与迁移步骤升级正式 SQLite 数据库，最后在 `8000/console` 检查页面、助手入口和接口。
6. 更新 `.superpowers/sdd/.../progress.md` 和本交接文档，记录最终提交、验证结果、服务状态及任何剩余限制。

## 本地安全约束

项目指令禁止批量删除文件或目录。不要使用 `del /s`、`rd /s`、`rmdir /s`、`Remove-Item -Recurse` 或 `rm -rf`。需要清理时，每次只删除一个明确的文件路径；若必须批量删除，停止并交给用户手动处理。测试时避免 `backend/tests/test_preview_frame_calibration.py`，该文件包含多文件删除清理逻辑。pytest 要使用隔离的 `TEST_DATABASE_URL`，并设置 `-o tmp_path_retention_count=1000000 -o tmp_path_retention_policy=all`。

子代理必须保持用户当前选定模型不变，仅调整该模型支持的推理强度；本轮为6.1 Sol，不使用Astra或另换模型。不要用 `@chrome`，除非严格执行项目 Chrome workaround。不要把真实密钥放进日志、输出或版本控制。
