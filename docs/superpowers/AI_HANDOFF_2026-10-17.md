# 小助手重构交接说明

更新日期：2026-10-17

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

用户限制子代理最多使用 `gpt-6-sol` high；更低档也可以。不要用 `@chrome`，除非严格执行项目 Chrome workaround。不要把真实密钥放进日志、输出或版本控制。
