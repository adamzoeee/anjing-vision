# 项目索引

[返回项目首页](../README.md)。本索引按 2026-10-14 仓库实际目录和调用入口整理，供部署、展示和开发定位使用。

## 按目标选择入口

| 目标 | 首选资料 / 入口 |
|---|---|
| 快速展示家庭点云和三维模型 | [两套 Demo 交付与启动](house-demo-delivery.md) |
| 展示存在空间问题的家具布局 | [风险布局 Demo](house-pointcloud-risk-demo.md) |
| 导出 C4D 可编辑模型与渲染图 | [Blender / FBX / GLB 导出](house-render-export.md) |
| 恢复 Scan46 当前接受结果 | [Scan46 精简交接](scan46-handoff-20261008.md) |
| 搭建 API、前端及视频主流程 | [首页运行说明](../README.md)、[配置样例](../backend/.env.example) |
| 理解正式测量与风险评分 | [空间指标验收](spatial-metrics-acceptance.md)、[风险开发报告](risk-assessment-development-report.md) |
| 查看窗边修复的来源和未解决问题 | [源数据可追溯修复](scan46-source-traced-repair.md)、[窗边问题交接](scan46-window-black-handoff.md) |

## 业务代码地图

| 范围 | 关键文件 | 职责 |
|---|---|---|
| 应用 / 配置 | [main.py](../backend/app/main.py)、[config.py](../backend/app/config.py) | API 装配、错误响应、健康检查、环境配置 |
| 数据与权限 | [models.py](../backend/app/models.py)、[schemas.py](../backend/app/schemas.py)、[deps.py](../backend/app/deps.py) | 机构、用户、项目、Scan、Report 及权限边界 |
| 存储 | [storage.py](../backend/app/storage.py) | 上传、媒体与产物存储 |
| 主流程 | [pipeline_runner.py](../backend/app/tasks/pipeline_runner.py) | 阶段状态、重建/结构/测量编排、正式风险和报告 |
| 任务执行 | [pipeline_tasks.py](../backend/app/tasks/pipeline_tasks.py)、[celery_app.py](../backend/app/tasks/celery_app.py) | 本地任务与 Celery 注册 |
| 视频与重建 | [slam3r_runner.py](../backend/pipeline/slam3r_runner.py) | 抽帧与独立 SLAM3R 子进程调用 |
| 点云后处理 | [scene_postprocess.py](../backend/pipeline/scene_postprocess.py) | 去噪、竖直对齐、墙轴方向、先验比例和预览输出 |
| 空间识别 | [spatiallm_runner.py](../backend/pipeline/spatiallm_runner.py) | 外部 SpatialLM 推理及结构解析 |
| 结构验证 | [structure_builder.py](../backend/pipeline/structure_builder.py) | 候选框、局部点云、几何与诊断输出 |
| 视频 / 语义证据 | [semantic.py](../backend/pipeline/semantic.py)、[video_box_refiner.py](../backend/pipeline/video_box_refiner.py) | 可用时为结构与物体提供语义及多视角证据 |
| 比例与测量 | [measurement_builder.py](../backend/pipeline/measurement_builder.py)、[metric_quality.py](../backend/pipeline/metric_quality.py) | 参考尺寸、统一比例、验证参考和质量判定 |
| 通行与空间 | [space_foundation.py](../backend/pipeline/space_foundation.py)、[passage_builder.py](../backend/pipeline/passage_builder.py)、[passage_metrics.py](../backend/pipeline/passage_metrics.py) | 占地、路径、净距、通道和通行分析 |
| 正式评估输入 | [spatial_assessment_inputs.py](../backend/pipeline/spatial_assessment_inputs.py)、[spatial_metrics.py](../backend/pipeline/spatial_metrics.py) | 已有结构化证据 → SpatialMetric 契约 |
| 布局与路径指标 | [spatial_layout.py](../backend/pipeline/spatial_layout.py)、[spatial_paths.py](../backend/pipeline/spatial_paths.py) | 家具空间关系、布局与路线指标 |
| 正式风险与评分 | [rules.py](../backend/pipeline/rules.py)、[risk_assessment.py](../backend/pipeline/risk_assessment.py) | 规则版本、风险项、40/30/30、置信度与覆盖率 |
| 正式图 / 报告 | [formal_risk_figure.py](../backend/pipeline/formal_risk_figure.py)、[pdf_report.py](../backend/pipeline/pdf_report.py)、[report_composer.py](../backend/pipeline/report_composer.py) | 消费正式评估结果生成风险图和 PDF |
| 结构平面图 | [structure_figure.py](../backend/pipeline/structure_figure.py) | 房间与家具二维结构展示 |
| 改造助手 | [renovation.py](../backend/app/routers/renovation.py)、[renovation_simulator.py](../backend/pipeline/renovation_simulator.py)、[intent_parser.py](../backend/assistant/intent_parser.py) | 意图、沙盒副本、几何与评分对比 |
| Gaussian 实验 | [gaussian_runner.py](../backend/pipeline/gaussian_runner.py) | 位姿恢复相关调用，以及显式启用的 Gaussian 分支 |

代码中保留了部分历史实现和原型；判断是否属于正式链路，应查看主编排中的实际调用，不能只依据文件名或旧文件头注释。

## API 与前端入口

| 功能 | 路由 / 页面 | 实现 |
|---|---|---|
| 健康 | `GET /api/health`、`GET /api/health/ready` | [main.py](../backend/app/main.py) |
| 登录与机构 | `/api/auth` | [auth.py](../backend/app/routers/auth.py)、[login_page.dart](../app/lib/pages/login_page.dart) |
| 项目与扫描 | `/api/projects`、`/api/scans` | [projects.py](../backend/app/routers/projects.py)、[scans.py](../backend/app/routers/scans.py) |
| 视频采集 / 上传 | `POST /api/scans/{scan_id}/upload` | [capture_page.dart](../app/lib/pages/capture_page.dart)、[upload_page.dart](../app/lib/pages/upload_page.dart) |
| 报告 | `GET /api/reports/scans/{scan_id}` | [reports.py](../backend/app/routers/reports.py)、[report_page.dart](../app/lib/pages/report_page.dart) |
| 三维查看 | `/preview/{scan_id}`、`/api/preview/{scan_id}/manifest.json` | [preview.py](../backend/app/routers/preview.py)、[viewer.js](../backend/app/static/preview/viewer.js) |
| 展示层 | 点云预览的六个结构展示层 | [visual_layers.js](../backend/app/static/preview/visual_layers.js) |
| 正式评估数据 | `/api/preview/{scan_id}/spatial-metrics.json`、`risk-assessment.json` | [preview.py](../backend/app/routers/preview.py) |
| 改造助手 | `/api/assistant/scans/{scan_id}/suggestions`、`simulate` | [renovation.py](../backend/app/routers/renovation.py) |
| API 地址与产物解析 | `API_BASE_URL`、report / artifact 模型 | [client.dart](../app/lib/api/client.dart)、[models.dart](../app/lib/api/models.dart) |
| 历史对比 | 扫描比较界面 | [compare_page.dart](../app/lib/pages/compare_page.dart) |

业务接口的字段与授权规则以运行中的 `/docs` 为准；静态 Demo 不经过业务认证或任务队列。

## 家庭 Demo 代码与数据

| 文件 / 目录 | 用途 |
|---|---|
| [house_demo.py（生成模块）](../backend/pipeline/house_demo.py) | 房间、统一门洞、点云融合、真值和模型输出 |
| [house_demo_assets.py](../backend/pipeline/house_demo_assets.py) | 家具及室内物体表面几何 |
| [house_demo_lived.py](../backend/pipeline/house_demo_lived.py)、[house_demo_risk.py](../backend/pipeline/house_demo_risk.py) | 生活化 / 风险示例的独立布局配置 |
| [house_demo_layout.py](../backend/pipeline/house_demo_layout.py) | 家具间距、局部路线净空、门内占用和几何规则建议 |
| [house_demo.py（CLI）](../backend/scripts/house_demo.py) | generate / infer / serve；rich、lived、risk 配置 |
| [点云查看器](../backend/app/static/house_demo/) | 房间、证据来源、物体标签与助手展示 |
| [点云结果目录](../backend/data/demos/house_demo_risk_20261013/) | 202306 个整屋去重点，五房间已完成模型解析 |
| [house_render_prepare.py](../backend/scripts/house_render_prepare.py) | 从布局准备可追溯规格、去重墙面、门窗洞 |
| [house_render_blender.py](../backend/scripts/house_render_blender.py) | 程序化实体、材质、灯光、相机、渲染及导出 |
| [house_render_verify.py](../backend/scripts/house_render_verify.py) | FBX / GLB / OBJ 回读，尺寸及家具变换校验 |
| [渲染查看器](../backend/app/static/house_render/) | GLB 交互查看、六视角、剖墙、图片与文件下载 |
| [渲染结果目录](../backend/data/demos/house_render_20261014_final/) | 45 个输入物体、完整编辑文件、6 张 PNG 和验证记录 |

点云版 `ground_truth.json` 与 `predictions.json` 分别是生成真值和实际模型结果；`layout_analysis.json` 与 `model_layout_analysis.json` 分别使用对应来源。家具漏检不得用真值补入模型结果。

渲染版 `render_spec.json` 保存布局来源与变换；`build_audit.json` 和 `export_validation.json` 保存构建及回读结果。程序化实体可编辑，但不意味着恢复了手机视频中的真实表面纹理。

## 业务产物与契约

常规扫描产物位于配置的 `DATA_DIR/work/{scan_id}/`。实际生成项目受阶段成功与输入可用性影响。

| 产物 | 含义 / 消费方 |
|---|---|
| `frames/`、`slam3r/` | 抽帧与重建数据 |
| `postprocess/scene_aligned.ply` | 对齐后的稠密点云；结构理解和预览 |
| `postprocess/layout.txt` / 解析 JSON | SpatialLM 候选结果 |
| `postprocess/structure.json` | 验证后的结构与家具 |
| `postprocess/structure_calibrated.json` | 标定后结构；存在时下游优先读取 |
| `postprocess/measurements.json` | 尺寸、标定状态及质量信息 |
| `postprocess/passage_analysis.json`、`spatial_foundation.json` | 通路、布局和基础空间数据 |
| `postprocess/spatial_metrics.json` | 统一正式评估指标 |
| `postprocess/risk_assessment.json` | 正式风险、评分、覆盖率、置信度、建议 |
| `postprocess/structure_plan.png` / 通行图 | 二维证据与路径展示；具体 URL 见预览 API |
| `images/formal_risks.png`、`report/report.pdf` | 正式风险图及 PDF；通过报告 artifact URL 访问 |

尺度先验、标定状态、测量可靠性、风险等级和覆盖率是不同概念。未知指标保留 `not_evaluable`；模型或几何来源不足不能被自动解释为安全。

## 部署与运维资料

- [backend/.env.example](../backend/.env.example)：当前配置样例，真实 `.env` 不提交。
- [setup_slam3r_stack.ps1](../backend/scripts/slam3r/setup_slam3r_stack.ps1)：模型栈部署参考，含本机固定路径，先核对再运行。
- [run_pipeline.py](../backend/scripts/run_pipeline.py)：对本地视频执行业务管道并输出报告，使用实际数据库/存储。
- [docker-compose.yml](../backend/docker-compose.yml)：PostgreSQL、Redis、MinIO、API、worker 定义。
- [MIGRATIONS.md](../backend/MIGRATIONS.md)：数据库迁移说明。
- [backend/README.md](../backend/README.md)：后端补充说明；Gaussian 的正式/实验边界以当前主流程和首页为准。
- [ENV_SETUP.md](../backend/ENV_SETUP.md)：历史 vid2scene 环境记录，其主线描述不适用于当前 SLAM3R 编排。

健康与数据库就绪检查不证明模型加载成功。排障按扫描 status/message、主流程异常日志以及独立模型子进程输出定位。

## 测试入口

| 验证范围 | 测试文件 |
|---|---|
| 点云生成、布局与数据分源 | [test_house_demo.py](../backend/tests/test_house_demo.py)、[assets](../backend/tests/test_house_demo_assets.py)、[lived](../backend/tests/test_house_demo_lived.py)、[risk](../backend/tests/test_house_demo_risk.py) |
| 渲染规格与来源 | [test_house_render.py](../backend/tests/test_house_render.py)；导出回读另用 Blender 验证脚本 |
| 结构 / 测量 / 通路 | [test_structure_builder.py](../backend/tests/test_structure_builder.py)、[test_measurement_builder.py](../backend/tests/test_measurement_builder.py)、[test_passage_metrics.py](../backend/tests/test_passage_metrics.py) |
| 正式风险与报告集成 | [test_risk_assessment.py](../backend/tests/test_risk_assessment.py)、[test_pipeline_risk_integration.py](../backend/tests/test_pipeline_risk_integration.py)、[test_pdf_report.py](../backend/tests/test_pdf_report.py) |
| 查看器 | [test_preview_chunks.cjs](../backend/tests/test_preview_chunks.cjs)、[room_camera](../backend/tests/test_preview_room_camera.cjs)、[visual_layers](../backend/tests/test_preview_visual_layers.cjs) |
| Scan46 交付 | [test_scan46_delivery.py](../backend/tests/test_scan46_delivery.py) |
| Flutter | [app/test/](../app/test/)；analyze、models、报告、登录、采集等组件测试 |

后端 pytest 从 `backend` 工作目录运行，Flutter 从 `app` 运行。文档中的历史测试数字有对应验收时间和范围，不替代当前全量回归。

## 文档索引

### 展示与交付

- [house-demo-delivery.md](house-demo-delivery.md)：两套已打包 Demo 的最短运行路径。
- [house-pointcloud-demo.md](house-pointcloud-demo.md)：模块化家庭点云基础设计。
- [house-pointcloud-rich-demo.md](house-pointcloud-rich-demo.md)：室内语义物体丰富化。
- [house-pointcloud-lived-demo.md](house-pointcloud-lived-demo.md)：生活化布局、来源区分与模型验证。
- [house-pointcloud-risk-demo.md](house-pointcloud-risk-demo.md)：空间隐患示例和净空对比。
- [house-render-export.md](house-render-export.md)：实体、材质、渲染、交换格式和 C4D 注意事项。

### Scan46 与真实数据验收

- [scan46-handoff-20261008.md](scan46-handoff-20261008.md)：当前接受结果、数据包和恢复步骤，优先阅读。
- [scan46-current-window-deployment.md](scan46-current-window-deployment.md)：窗边部署记录。
- [scan46-source-traced-repair.md](scan46-source-traced-repair.md)：可追溯修复方法和验证。
- [scan46-window-black-handoff.md](scan46-window-black-handoff.md)：窗边问题、证据及后续限制。
- [scan46-structured-input-audit.md](scan46-structured-input-audit.md)：正式评估输入审计。
- [scan46_delivery.py](../backend/scripts/scan46_delivery.py)：数据恢复和交付工具。

### 风险与测量

- [spatial-metrics-acceptance.md](spatial-metrics-acceptance.md)：正式空间指标验收。
- [risk-assessment-baseline.md](risk-assessment-baseline.md)：开发基准和历史问题。
- [risk-assessment-development-report.md](risk-assessment-development-report.md)：正式评分、报告集成与历史回归结论。

### 历史设计与研究

- [风险报告与助手设计](superpowers/specs/2026-09-08-risk-report-assistant-redesign.md) / [实施计划](superpowers/plans/2026-09-08-risk-report-assistant-redesign.md)。
- [R2 映射与定位计划](superpowers/plans/2026-09-14-r2-map-localization.md)。
- [Flutter 初期计划](superpowers/plans/2026-07-31-flutter-app.md)。
- [后端与重建初期计划](superpowers/plans/2026-07-31-后端与重建管道.md)。
- [Gaussian 早期方案](superpowers/specs/安龄智境-高斯泼溅方案.md)。

历史设计描述意图和当时的方案。当前支持范围以代码调用、配置及最新交接验收资料为准。
