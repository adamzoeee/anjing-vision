# 安龄智境（Anjing Vision）

用普通手机拍摄一段房间视频，自动重建 3D 场景，评估老人居住空间的**通行安全性**——检测门宽、通道宽度、门槛高度、地面坡度、台阶与临时障碍物，生成带可视化标注的评估报告与改造建议。

本项目使用 **3D Gaussian Splatting（3DGS）** 取代 Apple RoomPlan 等专有方案：任何手机（无需 LiDAR）拍摄的视频即可重建。重建核心采用开源项目 [vid2scene](https://github.com/samuelm2/vid2scene)（Apache-2.0）的端到端管线（HLoc 检索式 SfM + gsplat MCMC 训练），全部算法基于开源 Python 生态，**Windows + NVIDIA GPU 即可本地部署**。

## 特性

- **视频采集为主**：绕房间随意录 1~3 分钟视频即可，自动抽帧；也支持逐张拍照模式
- **端到端 3D 重建**：vid2scene（EigenPlaces 检索 + ALIKED/LightGlue 匹配 + COLMAP SfM + gsplat MCMC 训练，带 bilateral grid 外观建模与高斯数量上限）
- **真实尺度标定**：用户填写 2～3 个门、床或家具真实尺寸，多参考物一致性校验后恢复米制比例；分歧超限时自动退化为单参考物 + 层高门禁兜底
- **8 类风险规则**：门宽 / 通道净宽 / 门槛高度 / 台阶 / 地面坡度 / 地面高差 / 通道障碍物 / 卫生间门口，按「通行性 40% + 跌倒风险 40% + 无障碍 20%」加权评分
- **语义辅助**：GroundingDINO 零样本识别纸箱、杂物、宠物等临时障碍物，SAM 实例分割
- **可视化报告**：多视角标注渲染图 + 交互式 3D 点云预览（WebGL，无外部依赖）
- **多机构隔离**：机构/成员注册登录（JWT），数据按机构隔离，支持改造前后两次扫描对比
- **跨平台 App**：Flutter 实现 iOS + Android 双端

## 技术栈

| 层 | 选型 | 说明 |
|----|------|------|
| 采集端 | Flutter 3.x（Dart） | 视频录制引导、上传、进度轮询、报告展示 |
| 后端 | Python 3.12 + FastAPI | REST API、JWT 认证、多机构数据隔离 |
| 任务队列 | Celery + Redis（可选） | 管道异步编排；本地部署默认同步执行，无需 Redis |
| 3D 重建 | vid2scene（Apache-2.0） | 端到端重建：抽帧 → HLoc SfM → gsplat MCMC 训练，运行在独立 conda 环境 |
| 深度学习 | PyTorch 2.7（cu128）+ segment-anything + transformers | GPU 训练与零样本语义分割 |
| 点云处理 | Open3D 0.19 | RANSAC 平面提取、几何测量、渲染 |
| 视频处理 | ffmpeg | 抽帧、格式兼容 |
| 数据库 | SQLite（开发，默认）/ PostgreSQL 16（生产） | 元数据、报告、用户 |
| 对象存储 | 本地文件（开发，默认）/ MinIO（生产） | 视频、点云、渲染图 |
| 部署 | 本地裸机（conda 双环境，默认） | Docker Compose 作为生产形态备选 |

## 系统架构

```
┌─────────────────────┐
│  Flutter App        │  拍摄引导 / 上传 / 进度 / 报告 / 3D 预览
│  (iOS + Android)    │
└──────────┬──────────┘
           │ REST + JWT
┌──────────▼──────────────────────────────────────────┐
│  FastAPI 后端（backend/.venv）                       │
│  auth / projects / scans / reports + 管道编排        │
└──────────┬──────────────────────────────────────────┘
           │ subprocess 调用
┌──────────▼──────────────────────────┐
│  vid2scene 独立 conda 环境（GPU）    │
│  抽帧 → HLoc SfM → gsplat MCMC 训练  │
│  → COLMAP 相机模型 + splat.ply       │
└──────────┬──────────────────────────┘
           │ 解析为下游契约（cameras/points3D/高斯）
┌──────────▼──────────────────────────────────────────┐
│  语义分割 → 多参考物标定 → 几何测量 → 规则评分 → 报告  │
└─────────────────────────────────────────────────────┘
```

### 管道链路

```
视频 → vid2scene 端到端重建（抽帧 → EigenPlaces 检索配对 → ALIKED+LightGlue 特征匹配
→ COLMAP SfM → gsplat MCMC 训练 + bilateral grid 外观建模）
→ 解析 COLMAP 相机模型与 splat.ply → 点云导出与统计滤波 → GroundingDINO/SAM 语义分割
→ 多参考物米制标定（分歧超限自动单参考物兜底）→ 几何分析（门宽/门槛/坡度/高差）
→ 规则评分 → 报告（标注图 + 交互 3D 预览）
```

## 本地部署教程

> 目标形态：一台 Windows 11 + NVIDIA GPU 的机器，跑通「Flutter App → FastAPI → vid2scene 重建 → 报告」完整链路。

### 0. 前置条件

| 组件 | 要求 | 说明 |
|------|------|------|
| 操作系统 | Windows 10/11 | 无需 Docker、无需 WSL2 |
| GPU | NVIDIA，RTX 30 系及以上 | **RTX 50 系（sm_120）必须 torch 2.7+cu128**，本教程已固定该版本 |
| CUDA Toolkit | 12.8 | 安装到默认路径 `C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8` |
| MSVC | Visual Studio Build Tools 2022（C++ 生成工具） | 编译 gsplat / fused-ssim / fused-bilagrid 用 |
| Miniconda | 最新版 | 创建 vid2scene 隔离环境 |
| Git | 最新版 | 含 submodule 支持 |
| ffmpeg | ≥ 5.x（推荐 9.x） | 抽帧用；推荐安装到 `C:\ffmpeg\bin` |
| 磁盘 | ≥ 50GB 可用 | 环境 ~15GB + 模型权重 ~1GB + 扫描数据 |
| Python | 3.12 | 后端 venv 用（open3d 不支持 3.13+） |

### 1. 克隆本项目

```powershell
git clone git@github.com:adamzoeee/anjing-vision.git
cd anjing-vision
```

### 2. 获取 vid2scene 源码并打 Windows 补丁（已并入一键脚本，无需手动执行）

vid2scene 是独立仓库（Apache-2.0）。旧版部署文档曾固定克隆到 `E:\.PJs\vid2scene`，
**该路径已废弃**——现在由第 3 节的一键脚本自动完成：克隆主仓库 →
初始化子模块（gsplat/glomap/Hierarchical-Localization/spz 及 gsplat 的 glm）→
应用本项目 `backend/scripts/vid2scene/` 下的三个 Windows 补丁：

- `vid2scene-core-windows.patch`：ffmpeg ≥7 参数适配；pycolmap 4.x 多模型选主；
  `model_orientation_aligner` Windows 崩溃优雅跳过；新增 `--no_normalize_world_space`
  保留 COLMAP 世界坐标（下游几何测量必需）；vggt 路径惰性导入；
  训练器 DataLoader Windows spawn 管道溢出自动回退单进程
- `gsplat-windows.patch`：gsplat CUDA 扩展 MSVC 编译适配
- `pycolmap-parser-windows.patch`：pycolmap_parser Windows/numpy2/pycolmap4.x 适配

### 3. 一键搭建 vid2scene conda 环境（含源码克隆）

```powershell
cd <本仓库>\backend\scripts\vid2scene
powershell -NoProfile -ExecutionPolicy Bypass -File setup_vid2scene.ps1 -RepoRoot D:\vid2scene
```

`-RepoRoot` 改为你机器上的目标目录（**必填，不再有默认路径**）；
已克隆过源码时可加 `-SkipClone`。脚本会完成：克隆 vid2scene + 子模块 + 补丁 →
`conda create`（Python 3.12 + COLMAP 4.1.1 CLI，失败自动重试并提示换源）→
torch 2.7.1+cu128 → Python 依赖（hloc/pycolmap/opencv 等）→
pycolmap_parser（HEAD + 补丁）→ MSVC 编译 fused-ssim / fused-bilagrid / gsplat fork →
冒烟自检。**每一步失败都会立即报错停止**（不再静默继续），日志在
`<RepoRoot>\setup_vid2scene.log`。

脚本要点（换机器排错时对照）：
- 自动探测最新 CUDA（`C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v*`）与 vcvars64.bat
- 所有 pip 调用使用 `python -I` 并清空 `PYTHONPATH`，避免其他 Python 环境串入
- `DISTUTILS_USE_SDK=1` 是 MSVC 下编译 torch 扩展的必需项

**首次运行会联网下载模型权重**（EigenPlaces/ALIKED/LightGlue，约 500MB，缓存在
`C:\Users\<你>\.cache\torch\hub`，之后不再下载）。仅重建本身**不需要** HuggingFace
账号（glomap/vggt 才需要，默认不使用）。

### 4. 配置后端

```powershell
cd <本仓库>\backend
py -3.12 -m venv .venv
.venv\Scripts\pip install -r requirements.txt -r requirements-dev.txt
# 后端语义模型用 GPU：torch 必须是 cu128 版（RTX 50 系为 Blackwell sm_120）
.venv\Scripts\pip install torch==2.7.* --index-url https://download.pytorch.org/whl/cu128
Copy-Item .env.example .env
```

`.env` 中需要确认/修改：

```ini
DATABASE_URL=sqlite:///./anjing.db   # 本地部署默认 SQLite，无需安装数据库
TASK_SYNC=true                       # 同步执行管道，无需 Redis/Celery
DATA_DIR=./data

# ---- vid2scene 重建（默认开启）----
VID2SCENE_ENABLED=true
VID2SCENE_PYTHON=<RepoRoot>\envs\vid2scene\python.exe   # conda 环境 python（setup 脚本结尾会打印实际路径）
VID2SCENE_CORE_DIR=<RepoRoot>\vid2scene\vid2scene_core
VID2SCENE_GSPLAT_SCRIPT=<RepoRoot>\vid2scene\gsplat\examples\simple_trainer.py
VID2SCENE_FRAMECOUNT=300             # 抽帧数
VID2SCENE_TRAINING_STEPS=20000       # 训练步数
VID2SCENE_MAX_GAUSSIANS=1200000      # 高斯数量上限
VID2SCENE_RECONSTRUCTION_METHOD=colmap
APRILTAG_ENABLED=true
APRILTAG_FAMILY=tagStandard41h12
APRILTAG_SIZE_M=0.09             # 标准打印版 detection-corner 边长
```

### 5. 启动后端

```powershell
cd <本仓库>\backend
.venv\Scripts\uvicorn app.main:app --host 0.0.0.0 --port 8000
```

API 文档：<http://localhost:8000/docs>

### 6. 端到端验证（不启动 App 也能测）

```powershell
cd <本仓库>\backend
.venv\Scripts\python scripts\run_pipeline.py `
  --input 你的房间视频.mp4 `
  --outdir out
```

输出 `out\report.json`：评分、风险项、米制测量、预览资源清单。参考耗时：2.5 分钟
1080p 视频在 RTX 5080 Laptop 上约 **24 分钟**（重建 23.7 分钟 + 语义/报告约 1 分钟）。

### 7. Flutter App（可选）

```powershell
cd <本仓库>\app
flutter pub get
flutter run   # Android 模拟器默认连 http://10.0.2.2:8000
```

### 常见问题

| 现象 | 处理 |
|------|------|
| `未找到 vid2scene conda 环境` | 重跑第 3 步，或设置 `VID2SCENE_PYTHON` 指向 `<RepoRoot>\envs\vid2scene\python.exe`（setup 脚本结尾会打印实际路径） |
| 重建报 `ffmpeg ... Unrecognized option 'vsync'` | 补丁未生效：重跑第 3 步的 setup 脚本（会自动重试补丁） |
| 训练报 `num_workers`/管道错误 | gsplat 补丁未生效：重跑第 3 步的 setup 脚本（自动应用 gsplat-windows.patch） |
| 首次重建很慢 | 正常：首次要下载模型权重；另确认 GPU 未被其他程序占用 |
| 尺度标定失败 | 确认使用 tagStandard41h12 / ID 00000 标准打印版、100% 比例打印，并让完整 Tag 在多个视角清晰可见 |
| 抽帧里有模糊帧 | 新版 setup 已自动应用清晰度过滤补丁；确认 `setup_vid2scene.log` 出现 `blur filter` 字样，或重跑第 3 步脚本 |

## 生产部署（可选）

本地单机模式适合评估机构内网使用。多实例/公网部署时：

- 数据库换 PostgreSQL、对象存储换 MinIO（`.env` 指向对应地址）
- 关闭 `TASK_SYNC`，启动 Celery worker 消费 Redis 队列
- 或使用 `backend/docker-compose.yml`（PostgreSQL + Redis + MinIO + API + GPU Worker 五服务）
- 公网暴露必须更换 `SECRET_KEY` 与默认密码，并限制 CORS

## 采集与评估流程

1. 注册机构账号 → 新建评估项目（如「王奶奶家」）
2. 将标准 AprilTag 标定纸平整放置并以 100% 比例打印，录制或选择 1～3 分钟单房间视频；系统自动恢复米制尺度
3. 上传后实时查看进度（3D 重建 → 语义分割 → 标定 → 分析 → 评分）
4. 查看报告：安全评分、风险项列表、改造建议、标注图、交互式 3D 预览
5. 改造完成后再次扫描，使用「改造前后对比」查看评分变化

## 项目结构

```
├── backend/
│   ├── app/                 # FastAPI 应用
│   │   ├── routers/         # auth / projects / scans / reports API
│   │   └── tasks/           # Celery 任务与 vid2scene 正式管道编排器
│   ├── pipeline/            # 核心算法（无框架依赖，可独立测试）
│   │   ├── vid2scene_runner.py  # vid2scene 适配层：subprocess 调用 + 产物解析
│   │   ├── scene_contract.py    # 统一相机、点云、米制状态数据契约
│   │   ├── sfm.py / trainer.py  # 仅保留新管道复用的后处理算法，不再作为重建入口
│   │   ├── exporter.py          # 高斯场 → 点云 / 预览 PLY 导出
│   │   ├── calibrator.py        # 历史扫描兼容的参考物标定工具
│   │   ├── semantic.py          # GroundingDINO + SAM 语义分割
│   │   ├── geometry.py / spatial_measurement.py  # 点云几何测量
│   │   ├── rules.py             # 风险规则与评分（全 unknown 时不可评分）
│   │   └── report_builder.py    # 标注图与预览资源（含抽稀）
│   ├── scripts/             # CLI：run_pipeline / download_models
│   │   └── vid2scene/       # vid2scene 部署：一键环境脚本 + Windows 补丁
│   ├── tests/               # 后端自动测试
│   ├── docker-compose.yml   # 生产形态（可选）
│   └── ENV_SETUP.md         # 历史：Windows GPU 环境搭建记录
└── app/                     # Flutter 客户端
    ├── lib/api/             # Dio 客户端与数据模型
    ├── lib/pages/           # 登录/项目/采集/上传/报告/对比页
    ├── lib/widgets/         # 评分环、风险卡片
    └── web/preview/         # 自研 WebGL 点云渲染器
```

## 测试

```bash
# 后端
cd backend && .venv/Scripts/python -m pytest tests/ -v

# Flutter
cd app && flutter test
```

## 已知限制

- 管道需要 GPU（gsplat 光栅化依赖 CUDA），无 GPU 环境无法完成训练阶段
- 正常产品流程依赖 AprilTag 自动恢复米制尺度；标定失败时明确返回失败状态，绝不把相对坐标伪装成米。旧参考物标定仅用于历史扫描兼容
- 重建质量受拍摄条件影响：光线充足、慢速移动、避免反光面效果最佳
- 3D 交互预览目前是独立 Web 页面（`app/web/preview/index.html`），App 内的 webview 集成待完成
- 正式重建入口只有 vid2scene；旧自研抽帧/SfM/训练入口已退役，仍被调用的文件只提供畸变校正、曝光归一化等后处理能力

## 重建后端：vid2scene（替代自研管线）

自研的「抽帧 → pycolmap SfM → gsplat 训练」已被 [vid2scene](https://github.com/samuelm2/vid2scene)（Apache-2.0）端到端重建替代：视频直接送入，内部完成抽帧 → HLoc（EigenPlaces 检索 + ALIKED/LightGlue 匹配）→ COLMAP SfM → gsplat MCMC 训练（bilateral grid 外观建模、高斯数量硬上限）。下游的语义分割、尺度标定、几何测量、风险规则与报告全部保留，只消费 vid2scene 产出的 COLMAP 相机模型与 `splat.ply`，通过 `backend/pipeline/vid2scene_runner.py` 适配。

### 与旧自研管线的既有实测对比（单房间样本，2.5 分钟，同机 GPU）

| 指标 | 自研（Scan 11） | vid2scene（Scan 18） |
|------|----------------|-----------|
| 总耗时 | 4 小时 10 分 | **约 24 分钟**（重建 23.7 分钟 + 语义/报告约 1 分钟） |
| SfM | 699 帧 68 分钟、漂移严重（跳变比 12.5） | 282/282 帧注册、跳变比 4.8、重投影误差 1.16px |
| 训练 | 3 小时、损失 0.199、PSNR 16.5dB | 约 19 分钟、损失 0.03~0.12 |
| 高斯数 | 566 万（膨胀 37 倍） | **91 万**（硬上限 120 万） |
| 标定 | 26.3% 分歧 → 失败 | **10.1% 分歧 → 标定成功**（米制房间 6.99×6.58m、床 2.22×0.76m、门宽 0.85m） |
| 报告 | 全部 unknown 假 100 分 | 全 unknown 不再产出分数（显示"无法评分"） |
| 预览体积 | 168MB + 1.4GB | **24MB scene.ply + 74MB gaussian ply** |

## 许可证

本项目仅供学习与研究使用。依赖的开源组件（PyTorch、gsplat、Open3D、FastAPI、Flutter、vid2scene（Apache-2.0）等）遵循各自许可证；请勿将本项目用于商业用途前确认所有依赖的许可条款。
