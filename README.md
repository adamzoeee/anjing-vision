# 安龄智境 · Anjing Vision

面向居家适老化的三维空间分析项目：通过手机视频重建房间，识别空间结构与家具，生成尺寸、通行分析、风险评估和整改建议。提供 Flutter/Web 业务界面，以及可直接运行的家庭点云与三维渲染展示。

**文档导航：** [项目与代码索引](docs/INDEX.md) · [家庭 Demo 交付](docs/house-demo-delivery.md) · [整屋 Scan 工作台](docs/house-scan-workbench.md) · [Scan46 恢复](docs/scan46-handoff-20261008.md) · [风险评估开发报告](docs/risk-assessment-development-report.md)

本页依据 2026-10-14 的仓库实现整理。已打包的家庭 Demo 可以直接预览；处理新视频需要另行配置重建模型和 GPU 环境。

## 先体验已完成的展示

**下面的 8095/8096 地址是本机服务入口，不是 GitHub 在线网站。** 直接点击前，请先下载/克隆仓库并启动服务；没有启动时会显示“无法连接”。换一台电脑需要在那台电脑启动，重启电脑后需重新启动。

只想在 GitHub 查看效果，可以直接打开 [整屋渲染图](backend/data/demos/house_render_20261014_final/overview.png)、[客厅渲染图](backend/data/demos/house_render_20261014_final/living_room.png)或下方的数据下载，无需启动服务。

Windows 一键启动并打开两个页面（在仓库根目录执行，需要 Python 3）：

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File backend/scripts/start_house_demos.ps1 -OpenBrowser
```

如果 Python 不在 PATH，增加 `-PythonPath "你的 Python 安装目录/python.exe"`。脚本验证服务就绪，可重复执行；日志在 `backend/tmp/house-demo-servers/`。

在仓库根目录分别打开两个终端，使用 Python 3 启动：

```powershell
python -m http.server 8095 --bind 127.0.0.1 --directory backend/data/demos/house_demo_risk_20261013
```

```powershell
python -m http.server 8096 --bind 127.0.0.1 --directory backend/data/demos/house_render_20261014_final
```

| 入口 | 可以查看什么 |
|---|---|
| [点云隐患 Demo · 本地启动后访问 8095](http://127.0.0.1:8095/) | 五个房间的彩色点云、房间切换、家具框与标签、几何布局建议 |
| [三维渲染版 · 本地启动后访问 8096](http://127.0.0.1:8096/) | 可旋转实体场景、剖墙/天花板开关、整屋与分房间图片、模型下载 |

这些页面使用已提交的静态资源，预览不需要启动 API、重新推理或安装 Blender。通过 HTTP 打开，避免直接双击 HTML；端口已被占用时改用其他端口。

点云 Demo 提供两种证据来源：**生成真值**显示完整设计布局；**SpatialLM 模型结果**显示实际解析输出，包含漏检和误识别。场景是合成家庭环境，其布局建议用于展示几何分析，不作为真实住宅安全鉴定。

### 展示数据与下载

- [点云完整包](backend/data/demos/house_demo_risk_20261013.zip)：独立房间/整屋 PCD、PLY、网页缓冲区、模型输出与分析 JSON。
- [三维编辑与渲染包](backend/data/demos/house_render_20261014_final/house_c4d_bundle.zip)：FBX、GLB、glTF/bin、OBJ/MTL、Blender 工程、渲染图和验证记录。
- [渲染总览](backend/data/demos/house_render_20261014_final/overview.png) · [客厅](backend/data/demos/house_render_20261014_final/living_room.png) · [卧室](backend/data/demos/house_render_20261014_final/bedroom.png) · [厨房](backend/data/demos/house_render_20261014_final/kitchen.png) · [卫生间](backend/data/demos/house_render_20261014_final/bathroom.png) · [走廊](backend/data/demos/house_render_20261014_final/corridor.png)。

渲染版通过结构参数和家具布局生成可编辑的程序化实体，保留米制坐标与 0.9 × 2.1 m 门洞。默认使用完整预设布局；脚本也支持独立的模型结果模式。C4D 建议优先导入 FBX，复核单位、材质和灯光；已完成 Blender 回读验证，尚未进行 C4D 软件内验收。详见 [渲染与导出说明](docs/house-render-export.md)。

## 当前业务能力

| 模块 | 当前实现 |
|---|---|
| 视频重建 | FFmpeg 抽帧 → SLAM3R 稠密重建 → Open3D 点云清理、方向对齐及尺度先验 |
| 结构与家具 | SpatialLM 候选解析、点云几何验证，结合可用视频/语义证据生成结构化输出与诊断 |
| 尺度与测量 | 用户参考尺寸记录、统一比例标定、独立验证参考、家具尺寸与测量置信度 |
| 空间与通行 | 房间/家具占地、门到床等路径、家具净距、通道瓶颈及可达空间 |
| 正式风险评估 | 带版本规则、15 项空间指标、40/30/30 分类评分、置信度、覆盖率、Top Risks 和建议 |
| 报告 | 结构图、通行图、正式风险图、PDF，API 与 Flutter 页面读取同一评估结果 |
| 改造助手 | 已完成单房间在工作台生成 2–3 个 AI 候选，逐组正式验算，保存前预览独立改后框架与证据 |
| 三维查看 | 业务点云查看器、Scan46 六个展示层；另有独立家庭点云及实体渲染 Demo |
| 整屋 Scan | 同一项目逐个完成房间扫描，确认两侧门的对应关系后生成新的整屋 Scan；合成家庭演示仍保留作回归样例 |

当前视频主流程是 **SLAM3R + SpatialLM**。Gaussian 由 `GAUSSIAN_EXPERIMENTAL_ENABLED` 显式启用，默认关闭；已有结构化结果的正式风险评估不要求重新训练 Gaussian。

正式评估保留 `not_evaluable` 状态：缺少可靠数据不计为安全。标定、几何验证和风险评估是否成功应分别检查；扫描任务完成不等于每个指标都可评估。默认层高缩放是先验，实际测量可靠性以参考标定和证据质量为准。

## 业务处理流程

```text
手机视频 / Flutter 上传
  → FFmpeg 抽帧
  → SLAM3R 稠密重建
  → Open3D 清理、z-up / 墙轴对齐、尺度先验
  → 可用时恢复视频相机位姿
  → SpatialLM 结构及定向家具识别
  → 结构验证与 structure.json
  → 参考标定、measurements.json、通行分析
  → SpatialMetric → 版本化风险规则 → 40/30/30 评分
  → 正式风险图 / PDF / API / Flutter 报告与三维查看
```

主编排入口是 [pipeline_runner.py](backend/app/tasks/pipeline_runner.py)。改造模拟从已有结构化结果建立副本；家庭 Demo 从合成场景布局生成独立数据。

## 本地运行业务系统

### 后端与环境

从 HTTPS 克隆，在 `backend` 工作目录安装后端环境：

```powershell
git clone https://github.com/adamzoeee/anjing-vision.git
cd anjing-vision/backend
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
Copy-Item .env.example .env
```

按目标机器修改 `.env`，示例本地运行方式：

```ini
ENVIRONMENT=development
DATABASE_URL=sqlite:///./anjing.db
STORAGE_BACKEND=local
DATA_DIR=./data
TASK_SYNC=true
CORS_ORIGINS=["http://localhost:8080","http://127.0.0.1:8080"]
```

`SECRET_KEY` 使用自行生成的随机值。SLAM3R 与 SpatialLM 的源码、权重和独立 Python 环境需要单独准备，并填写 `SLAM3R_DIR`、`SLAM3R_PYTHON`、`SLAM3R_I2P_WEIGHTS`、`SLAM3R_L2W_WEIGHTS`、`SPATIALLM_DIR`、`SPATIALLM_PYTHON`、`SPATIALLM_MODEL_PATH`；FFmpeg 通过 `FFMPEG_BIN` 或 PATH 配置。

[重建环境脚本](backend/scripts/slam3r/setup_slam3r_stack.ps1)记录了环境和权重搭建方法，但含固定盘符、Conda 及用户名路径，运行前必须按本机核对。完整配置项见 [.env.example](backend/.env.example)。建议源码、模型、临时文件和数据放在空间充足的独立目录。

重建和识别需要 NVIDIA/CUDA 环境；实际显存需求取决于视频长度、采样、窗口及参考帧数量。8GB 显卡应先做短视频冒烟验证，再确认长视频档位；仓库默认值不是统一的 8GB 保证配置。静态 Demo 不受这些模型环境要求影响。

```powershell
# 在 backend 目录
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000
```

- API 文档：[http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)
- 存活检查：`GET /api/health`
- 数据库就绪：`GET /api/health/ready`

健康检查不验证 CUDA 或模型权重。`TASK_SYNC=true` 使用本地任务执行路径；`TASK_SYNC=false` 使用 Redis/Celery。API 和 worker 必须使用一致的数据库、数据目录及模型环境。Docker 服务定义见 [docker-compose.yml](backend/docker-compose.yml)，外部模型环境仍需单独配置。

### Flutter/Web

另开终端，在 `app` 目录执行：

```powershell
flutter pub get
flutter run -d web-server --web-hostname 127.0.0.1 --web-port 8080 --dart-define=API_BASE_URL=http://127.0.0.1:8000
```

打开 [http://127.0.0.1:8080/](http://127.0.0.1:8080/)，按登录 → 项目 → 扫描/采集 → 上传视频 → 进度 → 报告/三维查看使用。Web 的 API 地址使用 `API_BASE_URL`；移动端默认是 Android 模拟器宿主机地址，真机访问需另行配置实际后端地址。

API、上传和报告依赖业务数据与认证；家庭 Demo 使用独立的静态端口。预览及 PDF 链接需要当前有效登录状态。

### 恢复 Scan46 已有结果

按 [2026-10-08 Scan46 精简交接](docs/scan46-handoff-20261008.md)准备指定数据包并运行恢复脚本。这一流程复现已有点云、六层展示和业务结果，无需重新训练。Scan40 与 Scan46 是不同扫描，不能互相替代底包。

## 大屏工作台（Web 端 · 桌面专用）

`/console`（`http://<host>:8000/console`）是一套 Web 工作台，与 Flutter App 共用同一套后端 API 与账号体系，仅支持视频上传（不做浏览器内拍摄）。项目页、整屋视图和单房间 AI 助手可在较窄窗口使用；其余工作台视图仍以桌面大屏（≥1280px）为主。

- **登录页** `#/login`：机构账号登录，或“一键进入演示”（需 `DEMO_LOGIN=true`）
- **项目管理工作台** `#/projects`：项目 KPI 概览、项目卡片（评分环 + 蓝图封面）、新建项目
- **项目详情** `#/projects/{id}`：房间/扫描列表、管道进度（抽帧→重建→后处理→结构识别）、新建房间与视频上传（支持参考尺寸标定：门宽/门高/床长）、报告 PDF 下载；选择已完成房间并配对两侧门，可生成整屋 Scan
- **房间管理工作台** `#/scans/{id}`：3D 稠密点云（分层开关/点数/自动旋转/截图/全屏）、房间档案与参考尺寸、2D 结构图与通行图、综合评分与分类得分、风险清单与关键指标、顶部 AI 改造助手入口、方案库（保存/删除/导出 PDF 附录）
- **单房间 AI 改造助手** `#/scans/{id}/assistant`：补充不超过 300 字的目标后生成 2–3 个经正式评估链路验算的独立候选；切换候选和改造前后，共用交互式 3D 家居框架图，查看扫描事实、AI 设计意图、指标与风险变化、施工/假设新增提示和模拟边界。保存所选方案后进入详情回读；扫描输入已变时须重新生成。仅支持已完成单房间，整屋与 Flutter 报告页不接入。
- **整屋工作台** `#/scans/{id}`：同一路由显示拼接后的整屋点云、空间框架、房间拓扑与门对应关系；每次拼接生成新 Scan，原房间 Scan 保留。详见[逐房间扫描与整屋拼接](docs/house-scan-workbench.md)
- **大屏演示模式** `#/screen/{id}`：一屏驾驶舱（结构识别、参考尺寸、平面图纸、3D 场景、综合评估、风险清单、关键指标跑马灯），含入场编排与实时时钟，可切换同项目内其他已完成房间

前端为原生 HTML/CSS/ES Module（零构建、零外部 CDN，静态查看无需外网），源码在 `backend/app/static/console/`；设计系统固化于 `design-system/anjing-vision/MASTER.md`（v2 暮光 / 象牙主题）。AI 生成需后端配置 `DEEPSEEK_API_KEY` 并能访问 DeepSeek；可通过 `DEEPSEEK_MODEL`、`DEEPSEEK_ENDPOINT`（HTTPS）、`DEEPSEEK_TIMEOUT_S` 和 `DEEPSEEK_MAX_TOKENS`（128–4096，默认 2048）调整服务，密钥不进入前端；未配置、超时或可信方案不足会显示可重试原因，取消仅中止本页等待。深链支持 `?token=<jwt>`（与 `/preview` 一致）。

> v2 视觉：双主题（「暮光」深紫珊瑚 /「象牙」暖白，工作台左下角一键切换）、全效动效开关（默认跟随系统"减少动效"，可强制全量）、界面背后常驻悬浮 3D 建模层（真实 layout 线框 + 粒子 + 鼠标视差）、页面级转场（View Transitions + 光带扫掠）、3D 场景「真实场景 / 空间结构」双视图切换。

> 数据源约定（与修补流程一致）：3D 结构框与结构识别面板**优先读取修补后的 `postprocess/structure.json`**（含窗、家具修补结果；盒尺寸优先采用 `measurements.json` 中审计后的实测值），仅在缺失时回退到原始 `layout.json`；点云使用 `preview_selection.json` 选定的修补版 PLY，并叠加 `window-visual.json` 窗户实拍展示层（visual-only，不参与几何与评分）。

## 仓库导航

```text
app/                         Flutter 业务界面、API 模型、报告与预览入口
backend/app/                 FastAPI、认证、数据模型、路由、任务编排
backend/pipeline/            重建适配、结构/测量/通路/风险/报告、家庭 Demo
backend/assistant/           改造意图解析与响应
backend/scripts/             部署、视频 CLI、Scan46 修复/交接、Blender 导出
backend/tests/               后端与查看器测试
backend/data/demos/          已打包的家庭点云及渲染结果
docs/                       项目索引、验收、交接、开发与历史方案
```

按功能找文件、API、测试与数据契约，请看 [完整项目索引](docs/INDEX.md)。`backend/data/` 通常被忽略；本仓库仅显式提交选定的合成 Demo，不包含运行所需的全部历史扫描、模型权重或个人 `.env`。

## 验证与测试

后端测试必须从 `backend` 目录执行，使用测试数据库及隔离媒体目录；依赖见 [requirements-dev.txt](backend/requirements-dev.txt)：

```powershell
cd backend
python -m pip install -r requirements-dev.txt
python -m pytest tests -q
node tests/test_preview_chunks.cjs
node tests/test_preview_room_camera.cjs
node tests/test_preview_visual_layers.cjs
```

Flutter：在 `app` 目录运行 `flutter analyze` 和 `flutter test`。

2026-10-14 两套家庭 Demo 的相关回归为 **65 passed**，FBX/GLB/OBJ 回读及网页检查通过。这是该功能的验证范围；项目全量测试与历史问题请按当前环境重跑并参考 [风险评估开发报告](docs/risk-assessment-development-report.md)。

## 使用边界与许可

重建完整度、家具识别、尺度和空间判断受拍摄与模型证据影响；结果需结合现场复核。Scan46 窗边残余问题和接受结果见交接文档。程序化渲染实体用于展示和编辑，不提供实拍纹理恢复精度承诺。

上游代码、模型权重和资源各自受对应许可证约束，使用或分发前请核对 SLAM3R、SpatialLM、SAM、Three.js、Blender 等许可及模型卡，尤其是非商业限制。网页资源保留其许可说明；个人凭据、真实视频和本机环境配置不应提交。
