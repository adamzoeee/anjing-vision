# 安龄智境（Anjing Vision）

安龄智境（Anjing Vision）是一套面向居家适老化安全评估的 AI 空间分析系统。用户只需使用普通手机拍摄房间视频，系统即可完成稠密 3D 场景重建与空间结构识别，并进一步生成房间参考尺寸、2D 空间结构图、门到床等关键通行路径分析、通道净宽与可达空间指标，在此基础上进行正式风险评估、分类评分、改造建议和 PDF 报告生成。系统同时提供 AI 安全改造助手，可在不修改真实扫描、点云和结构数据的前提下，对选定改造建议或用户输入的家具移动、物品新增等方案进行沙盒模拟，并比较改造前后的空间指标与风险评分变化。

系统以 SLAM3R 完成视频稠密三维重建，以 SpatialLM 完成墙、门、窗和家具等空间结构理解，并由后端统一完成空间指标计算、通行分析、风险规则评估和评分，Flutter/Web 前端负责报告、2D 结构图、3D 场景及改造模拟结果展示。

重建核心为 [SLAM3R](https://github.com/pku-vcl-3dv/SLAM3R)（CVPR 2025 Highlight）：逐帧回归稠密 3D 点云、无需显式相机位姿估计，比传统 SfM+训练式高斯溅射快一个数量级；空间理解核心为 [SpatialLM1.1-Qwen-0.5B](https://huggingface.co/manycore-research/SpatialLM1.1-Qwen-0.5B)（NeurIPS 2025），直接从点云输出墙/门/窗/家具的结构化 3D 框。全部基于开源 Python 生态，**Windows + NVIDIA GPU 本地部署**。

## 特性

**重建与结构理解**

- **手机视频 → 稠密 3D 场景**：上传普通手机拍摄的房间视频（1~3 分钟，任意移动），SLAM3R 输出稠密彩色点云
- **SpatialLM 房间结构识别**：识别墙、门、窗与家具实例（59 类家具，含朝向），输出结构化 3D 框
- **自动后处理**：统计离群点去噪 → 地板/墙面方向自动对齐（z-up + Manhattan 贴轴）→ 米制尺度恢复

**测量与空间分析**

- **参考尺寸标定**：以用户输入的已知物体真实尺寸（门高、床长、书桌宽等，或 A4 纸 / 门高先验）把重建恢复为真实比例，输出房间与家具参考尺寸
- **空间指标计算**：通道净宽、门净宽、家具间距、床周净空、拥挤度、入口可用空间等结构化指标，作为风险评估的输入
- **2D 结构图**：按测量结果绘制的 2D 结构平面图，标注墙、门、床、桌等结构与家具
- **门→床通行分析**：识别门到床主要通行路径，计算沿途通道净宽与可达区域，输出通行图

**风险评估与报告**

- **正式风险评估与综合评分**：按正式规则逐项评估风险（通道、路径、家具、床周等），并按通行能力 40% / 空间布局 30% / 使用安全 30% 综合评分
- **风险项与改造建议**：报告逐项风险等级、触发阈值与对应适老化改造建议
- **PDF 评估报告**：一键导出正式评估报告

**AI 安全改造助手（沙盒 What-if 模拟）**

- **建议模拟**：勾选报告中的改造建议（如"扩大床侧净空"），在内存沙盒中模拟执行并重算空间指标与评分
- **自由改造评估**：输入"把床向左移动 30 厘米""在床边放一个 60×40×50 厘米的箱子"等自然语言设想，助手按真实几何重算，给出「当前分 → 修改后分」、关键指标变化与风险等级变化
- **数据零改动**：模拟只发生在内存副本中，不修改真实扫描、点云、结构数据或正式报告；无几何对应、无法精确量化的方案只给出定性提示，不虚构评分变化

**产品形态**

- **高密度 3D 预览**：自研 three.js 查看器，百万级点云渐进式连续加载，自由旋转/缩放/平移，结构框叠加开关、自动旋转、截图
- **跨平台 App 与 Web**：Flutter 实现 iOS + Android 双端；Web 端直接展示报告、2D 结构图、3D 场景与改造模拟结果
- **多机构隔离**：机构/成员注册登录（JWT），数据按机构隔离
- **独立环境隔离**：SLAM3R 与 SpatialLM 各自独立 conda 环境，后端通过 subprocess 调用

## 技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| 采集/展示端 | Flutter 3.x（Dart）/ Web | 视频录制引导、上传、进度轮询、报告与改造模拟、2D 图与 3D 预览 |
| 后端 | Python 3.12 + FastAPI | REST API、JWT 认证、多机构数据隔离 |
| 稠密重建 | SLAM3R（独立 `slam3r` conda 环境） | 视频 → 逐帧 I2P → L2W 全局注册 → 稠密点云 PLY |
| 空间理解 | SpatialLM1.1-Qwen-0.5B（独立 `spatiallm` conda 环境） | 点云 → 墙/门/窗/家具结构化 3D 框 |
| 点云处理 | Open3D 0.19（后端 venv） | 去噪、方向对齐、体素化、预览导出 |
| 视频处理 | ffmpeg | 抽帧 |
| 可视化 | three.js（本地内置，无外部 CDN） | 高密度点云 Web 预览 + 结构框叠加 |
| 数据库 | SQLite（开发，默认）/ PostgreSQL（生产） | 元数据、报告 |
| 部署 | 本地裸机（conda 三环境） | SLAM3R / SpatialLM / 后端 |

## 系统架构

```
┌─────────────────────┐
│  Flutter App        │  拍摄 / 上传 / 进度 / 3D 预览（webview 打开 /preview/{scan}）
└──────────┬──────────┘
           │ REST + JWT
┌──────────▼──────────────────────────────────────────┐
│  FastAPI 后端（backend/.venv，Python 3.12）          │
│  auth / projects / scans / preview + 管道编排        │
└──────┬────────────────────────┬─────────────────────┘
       │ subprocess             │ subprocess
┌──────▼──────────────┐  ┌──────▼────────────────────┐
│ slam3r conda 环境   │  │ spatiallm conda 环境       │
│ ffmpeg 抽帧         │  │ SpatialLM1.1-Qwen-0.5B     │
│ SLAM3R 稠密重建      │  │ → layout.txt（墙/门/窗/框） │
│ → scene_recon.ply   │  └──────┬────────────────────┘
└──────┬──────────────┘         │
       ▼                        │
┌──────────────────────────┐    │
│ Open3D 后处理（后端 venv）│    │
│ 去噪 → z-up/贴轴 → 缩放  │◄───┘
│ → scene_aligned.ply      │
│ → scene_preview.ply      │
└──────┬───────────────────┘
       ▼
┌──────────────────────────┐
│ /preview/{scan} Web 查看器 │  three.js 点云 + 结构框叠加
└──────────────────────────┘
```

### 管道链路

```
视频 → ffmpeg 抽帧(4fps) → SLAM3R（I2P 逐帧点图 + L2W 全局注册，置信度过滤）
→ scene_recon.ply → 统计去噪 → 竖直轴估计（法向投票×RANSAC 主平面）
→ z-up + 地面平移 + 墙面贴轴 → 层高(2.6m)恢复米制尺度
→ SpatialLM 推理（all：墙/门/窗/家具框）→ layout.txt → 统一 boxes JSON
→ 高密度预览 PLY + /preview/{scan} 交互式 3D 查看器
```

## 本地部署教程

> 目标形态：一台 Windows 11 + NVIDIA RTX 50 系（或 30/40 系）GPU 的机器，跑通「Flutter App → FastAPI → SLAM3R → SpatialLM → 3D 预览」完整链路。

### 0. 前置条件

| 组件 | 要求 | 说明 |
|------|------|------|
| 操作系统 | Windows 10/11 | 无需 Docker、无需 WSL2 |
| GPU | NVIDIA，RTX 30 系及以上，≥16GB 显存 | **RTX 50 系（sm_120）必须 torch 2.7+cu128**，本教程已固定 |
| CUDA Toolkit | 12.8（编译 spconv/torch-scatter 时才需要） | 安装到默认路径 `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8` |
| Miniconda/Anaconda | 任意版本 | 创建 slam3r / spatiallm 隔离环境 |
| Git | 最新版 | 克隆两个上游仓库 |
| ffmpeg | ≥5.x（推荐 9.x） | 抽帧用；推荐安装到 `C:\ffmpeg\bin` |
| 磁盘 | ≥40GB 可用 | 两环境 ~15GB + 权重 ~6.5GB + 扫描数据 |

### 1. 克隆本项目

```powershell
git clone git@github.com:adamzoeee/anjing-vision.git
cd anjing-vision
```

### 2. 一键搭建重建栈（源码 + 环境 + 权重）

```powershell
powershell -NoProfile -ExecutionPolicy Bypass -File backend\scripts\slam3r\setup_slam3r_stack.ps1
```

脚本完成：克隆 SLAM3R / SpatialLM 源码 → SpatialLM Windows 补丁（禁用 flash-attn，官方无 Windows 轮子）→ 创建 `slam3r` 与 `spatiallm` 两个 conda 环境（Python 3.11 + torch 2.7.1 cu128；spatiallm 另装 transformers 4.46 / spconv cu128 / torch-scatter）→ 下载三套权重 → 双模型加载冒烟自检。日志在 `E:\.PJs\deploy\setup_slam3r_stack.log`。

可选参数：`-SkipSources` / `-SkipEnv` / `-SkipWeights`（已装过的部分可跳过）。

**权重来源（境内网络）**：SLAM3R 的 `slam3r_i2p.pth` / `slam3r_l2w.pth` 走 AIFastHub 镜像；`SpatialLM1.1-Qwen-0.5B` 走 ModelScope 镜像。全部落盘到 `E:\.PJs\models\`。**不下载任何训练/测试数据集，不做训练与微调。**

### 3. 配置后端

```powershell
cd backend
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt
Copy-Item .env.example .env
```

`.env` 中确认（路径与第 2 步实际安装位置一致）：

```ini
DATABASE_URL=sqlite:///./anjing.db   # 本地部署默认 SQLite
TASK_SYNC=true                       # 同步执行管道（后台线程），无需 Redis/Celery
DATA_DIR=./data

# ---- SLAM3R 稠密重建（默认值即本机部署值）----
SLAM3R_DIR=E:\.PJs\slam3r
SLAM3R_PYTHON=C:\Users\<你>\.conda\envs\slam3r\python.exe
SLAM3R_I2P_WEIGHTS=E:\.PJs\models\slam3r_i2p\slam3r_i2p.pth
SLAM3R_L2W_WEIGHTS=E:\.PJs\models\slam3r_l2w\slam3r_l2w.pth
SLAM3R_FPS=4
SLAM3R_TARGET_HEIGHT_M=2.6          # 层高米制缩放（住宅 2.6~2.8m）

# ---- SpatialLM 空间结构识别 ----
SPATIALLM_DIR=E:\.PJs\spatiallm
SPATIALLM_PYTHON=C:\Users\<你>\.conda\envs\spatiallm\python.exe
SPATIALLM_MODEL_PATH=E:\.PJs\models\SpatialLM1.1-Qwen-0.5B
```

> 后端 requirements 不再包含 vid2scene/gsplat/pycolmap；后端点云处理只用 open3d。

### 4. 启动后端

```powershell
cd backend
.venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8000
```

API 文档：<http://localhost:8000/docs>

### 5. 端到端验证（不启动 App 也能测）

```powershell
cd backend
.venv\Scripts\python scripts\run_pipeline.py `
  --input 你的房间视频.mp4 `
  --outdir out
```

参考耗时（2.5 分钟 1080p 视频，RTX 5080 Laptop）：抽帧 ~13s → SLAM3R 重建 ~10~20 分钟 → 后处理 ~1 分钟 → SpatialLM ~1~2 分钟。

输出 `out\report.json`（含结构识别结果与各阶段产物路径）；预览地址为 `http://localhost:8000/preview/{scan_id}`（登录后打开，或在 URL 带 `?scan={id}&token={jwt}`）。

### 6. Flutter App（可选）

```powershell
cd app
flutter pub get
flutter run   # Android 模拟器默认连 http://10.0.2.2:8000
```

## 3D 预览（验收标准）

`/preview/{scan_id}`（或 API `GET /api/preview/{scan_id}/manifest.json`）：

- 自由旋转（左键）/ 缩放（滚轮）/ 平移（右键）查看整个房间
- 百万级稠密点云**渐进式连续加载**（分块上屏，不白屏不卡死）
- 叠加 SpatialLM 的墙（蓝）/ 门（黄）/ 窗（绿）/ 家具（粉）3D 框与家具标签，可分别开关
- 点大小、自动旋转、重置视角、截图

验收：墙、床、桌、门、柜等主要结构肉眼清楚可辨（允许少量孔洞/噪点/局部模糊）；结构框与点云对齐叠加。

## 接口说明

| 接口 | 说明 |
|------|------|
| `POST /api/scans/{scan_id}/upload` | 上传视频，自动进入管道 |
| `GET /api/scans/{scan_id}` | 状态/进度轮询（status: uploading→extracting→reconstructing→cleaning→understanding→done） |
| `GET /api/reports/scans/{scan_id}` | 评估报告：官方评分、风险项、参考尺寸、结构信息与 PDF 链接 |
| `GET /api/preview/{scan_id}/structure_plan.png` | 2D 结构图（按测量结果绘制） |
| `GET /api/preview/{scan_id}/passage_plan.png` | 通行图（门→床路径与通道净宽标注） |
| `GET /api/preview/{scan_id}/manifest.json` | 预览清单（点云/结构文件 URL、对齐与缩放元数据） |
| `GET /api/preview/{scan_id}/scene.ply` | 高密度预览点云（binary PLY） |
| `GET /api/preview/{scan_id}/layout.json` | 结构框 JSON：`{walls,doors,windows,objects,counts}`，米制 z-up |
| `GET /preview/{scan_id}` | 3D 查看器页面 |
| `GET /api/assistant/scans/{scan_id}/suggestions` | AI 安全改造助手：当前评分与可模拟的改造建议列表 |
| `POST /api/assistant/scans/{scan_id}/simulate` | AI 安全改造助手：沙盒模拟（勾选建议或自由改造指令），返回改造前后评分、指标与风险变化 |

结构框 JSON 中每个框为 `{center:[x,y,z], size:[sx,sy,sz], rotation_z_deg, kind/category}`（米）。管道产物目录：`data/work/{scan_id}/`（`frames/`、`slam3r/scene/scene_recon.ply`、`postprocess/`）。

## 项目结构

```
├── backend/
│   ├── app/                 # FastAPI 应用
│   │   ├── routers/         # auth / projects / scans / reports / preview API
│   │   ├── static/preview/  # three.js 高密度点云查看器（本地内置）
│   │   └── tasks/           # pipeline_runner：SLAM3R+SpatialLM 管道编排
│   ├── pipeline/
│   │   ├── slam3r_runner.py     # SLAM3R 适配层：ffmpeg 抽帧 + subprocess 重建
│   │   ├── scene_postprocess.py # Open3D 去噪 / z-up / 贴轴 / 层高缩放 / 预览导出
│   │   ├── spatiallm_runner.py  # SpatialLM 适配层：subprocess 推理 + layout 解析
│   │   └── …（calibrator/semantic/geometry/rules 等历史模块保留）
│   ├── scripts/
│   │   ├── run_pipeline.py      # CLI 端到端验证
│   │   └── slam3r/              # setup_slam3r_stack.ps1 一键部署 + 权重下载
│   └── tests/
└── app/                     # Flutter 客户端
```

## 已知限制

- 管道需要 NVIDIA GPU（SLAM3R 前馈重建、SpatialLM 推理均依赖 CUDA）
- SLAM3R 输出方向任意、尺度未定：本管道先做 z-up/墙面贴轴对齐与默认层高先验缩放（`SLAM3R_TARGET_HEIGHT_M` 可调），正式测量结果以用户输入的参考尺寸标定为准；标定与测量仍在持续优化
- SpatialLM 输入要求 z-up + 墙面贴 x/y 轴 + 米制：后处理已满足该约定
- AI 安全改造助手只模拟有几何对应的改造（移动现有家具、新增/移除物品、勾选建议），且严格基于现有结构化空间数据与正式评分体系；拆墙、定制家具等无法精确映射到现有指标体系的方案仅提供定性提示，不虚构评分变化
- 重建质量受拍摄条件影响：光线充足、慢速移动、避免反光面效果最佳

## 许可证

本项目仅供学习与研究使用。依赖的开源组件（PyTorch、Open3D、FastAPI、Flutter、three.js、SLAM3R、SpatialLM 等）遵循各自许可证；SpatialLM 权重为 CC-BY-NC-4.0，SLAM3R 权重/代码为 CC BY-NC-SA 4.0（非商业），请勿用于商业用途前确认所有依赖的许可条款。
