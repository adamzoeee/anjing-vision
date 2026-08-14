# 安龄智境（Anjing Vision）

用普通手机拍摄一段房间视频，自动重建 3D 场景，评估老人居住空间的**通行安全性**——检测门宽、通道宽度、门槛高度、地面坡度、台阶与临时障碍物，生成带可视化标注的评估报告与改造建议。

本项目使用 **3D Gaussian Splatting（3DGS）** 取代 Apple RoomPlan 等专有方案：任何手机（无需 LiDAR）拍摄的视频即可重建，全部算法基于开源 Python 生态，Windows + NVIDIA GPU 即可本地运行。

## 特性

- **视频采集为主**：绕房间随意录 1~3 分钟视频即可，自动抽帧与清晰度过滤；也支持逐张拍照模式
- **3D 高斯泼溅重建**：pycolmap 恢复相机位姿 → gsplat 训练 3D 高斯场 → 导出稠密点云
- **真实尺度标定**：用户填写 2～3 个门、床或家具真实尺寸，多参考物一致性校验后恢复米制比例
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
| 任务队列 | Celery + Redis | 管道异步编排，同步模式（开发兜底） |
| 3D 重建 | gsplat 1.5 + pycolmap 4.1 | 3D 高斯泼溅训练与增量式 SFM |
| 深度学习 | PyTorch 2.7（cu128）+ segment-anything + transformers | GPU 训练与零样本语义分割 |
| 点云处理 | Open3D 0.19 | RANSAC 平面提取、几何测量、渲染 |
| 视频处理 | ffmpeg | 抽帧、格式兼容 |
| 数据库 | PostgreSQL 16（生产）/ SQLite（开发） | 元数据、报告、用户 |
| 对象存储 | MinIO（生产）/ 本地文件（开发） | 视频、点云、渲染图 |
| 部署 | Docker Compose（GPU 直通） | 一键启动全部服务 |

## 系统架构

```
┌─────────────────────┐
│  Flutter App        │  拍摄引导 / 上传 / 进度 / 报告 / 3D 预览
│  (iOS + Android)    │
└──────────┬──────────┘
           │ REST + JWT
┌──────────▼──────────┐
│  FastAPI 后端        │  auth / projects / scans / reports
└──────────┬──────────┘
           │ Celery（任务队列）
┌──────────▼──────────┐      ┌──────────────────────┐
│  管道 Worker         │─────►│  GPU：gsplat 3DGS 训练 │
│  抽帧→SFM→训练→分析→报告│      └──────────────────────┘
└──────────┬──────────┘
           │
┌──────────▼──────────────────────────────────┐
│  PostgreSQL（元数据） Redis（队列） MinIO（媒体） │
└─────────────────────────────────────────────┘
```

### 管道链路

```
视频/照片 → ffmpeg 抽帧 → Laplacian 清晰度过滤 → pycolmap SFM（相机位姿 + 稀疏点云）
→ gsplat 3DGS 训练 → 点云导出与统计滤波 → 多参考物米制标定 → GroundingDINO/SAM 语义分割
→ 几何分析（门宽/门槛/坡度/高差）→ 规则评分 → 报告（标注图 + 交互 3D 预览）
```

## 快速开始

### 环境要求

- Python 3.12（open3d 不支持 3.13+）
- NVIDIA GPU（RTX 30 系及以上，需 CUDA 12.8 + MSVC Build Tools，详见 `backend/ENV_SETUP.md`）
- Flutter 3.x（可选，仅构建 App 时需要）
- ffmpeg

### 本地开发（最快路径）

```bash
cd backend
py -3.12 -m venv .venv
.venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt

# 复制配置并启用同步管道（无需 Redis/Celery）
cp .env.example .env   # 设置 TASK_SYNC=true

# 启动 API（文档：http://localhost:8000/docs）
.venv/Scripts/uvicorn app.main:app --reload

# 端到端管道（真实数据验证）
.venv/Scripts/python scripts/run_pipeline.py --input 你的房间视频.mp4 --outdir out/
```

### Flutter App

```bash
cd app
flutter pub get
flutter run   # 连接后端：默认 http://10.0.2.2:8000（Android 模拟器访问宿主机）
```

## 部署方式

### 方案一：Docker Compose（推荐生产形态）

包含 PostgreSQL、Redis、MinIO、API、GPU Worker 五个服务，一键启动：

```bash
cd backend
# 需要 NVIDIA Container Toolkit（GPU 训练在 worker 容器内执行）
docker compose up -d --build
```

服务说明：

| 服务 | 端口 | 说明 |
|------|------|------|
| `db` | 5432 | PostgreSQL 16，元数据存储 |
| `redis` | 6379 | Celery 任务队列 |
| `minio` | 9000 / 9001 | 对象存储（媒体/点云/报告），控制台 :9001 |
| `api` | 8000 | FastAPI 服务，OpenAPI 文档在 `/docs` |
| `worker` | - | Celery Worker，GPU 直通执行重建管道 |

环境变量通过 `docker compose` 内联配置，`SECRET_KEY` 需在启动前通过环境变量注入：

```bash
export SECRET_KEY='你的高强度随机密钥'
docker compose up -d --build
```

### 方案二：裸机部署（无 Docker）

```bash
cd backend
# 依赖 PostgreSQL、Redis、MinIO 自行安装，.env 指向对应地址
.venv/Scripts/uvicorn app.main:app --host 0.0.0.0 --port 8000
# Windows 本地 3DGS worker：启动前自动加载 VS 2022 x64、CUDA、Ninja 并预检 gsplat
scripts/start_local_worker.cmd
```

### 采集与评估流程

1. 注册机构账号 → 新建评估项目（如「王奶奶家」）
2. 进入采集页，填写 2～3 个门、床或家具的已知尺寸，再录制或选择 1～3 分钟单房间视频（无需放置 A4 纸）
3. 上传后实时查看进度（抽帧 → 位姿估计 → 3D 重建 → 标定 → 分析 → 评分）
4. 查看报告：安全评分、风险项列表、改造建议、标注图、交互式 3D 预览
5. 改造完成后再次扫描，使用「改造前后对比」查看评分变化

## 项目结构

```
├── backend/
│   ├── app/                 # FastAPI 应用
│   │   ├── routers/         # auth / projects / scans / reports API
│   │   └── tasks/           # Celery 任务与管道编排器
│   ├── pipeline/            # 核心算法（无框架依赖，可独立测试）
│   │   ├── frame_extractor.py   # ffmpeg 抽帧 + 清晰度过滤
│   │   ├── sfm.py               # pycolmap 相机位姿估计
│   │   ├── trainer.py           # gsplat 3DGS 训练
│   │   ├── exporter.py          # 高斯场 → 点云
│   │   ├── calibrator.py        # 多参考物米制尺度标定（兼容旧 A4 工具）
│   │   ├── semantic.py          # GroundingDINO + SAM 语义分割
│   │   ├── geometry.py          # 点云几何测量
│   │   ├── rules.py             # 风险规则与评分
│   │   └── report_builder.py    # 标注图与预览资源
│   ├── scripts/             # CLI：run_pipeline / download_models
│   ├── tests/               # 58 个测试
│   ├── docker-compose.yml
│   ├── Dockerfile
│   └── ENV_SETUP.md         # Windows GPU 环境搭建记录
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
- 米制标定依赖 GroundingDINO/SAM 检出至少两个已填写参考物；参考物漏拍或尺寸估计不一致时保留相对尺度并明确提示，不伪造米制结果
- 重建质量受拍摄条件影响：光线充足、慢速移动、避免反光面效果最佳
- 3D 交互预览目前是独立 Web 页面（`app/web/preview/index.html`），App 内的 webview 集成待完成

## 3D 重建后端：vid2scene（替代自研管线）

自研的「抽帧 → pycolmap SfM → gsplat 训练」已被 [vid2scene](https://github.com/samuelm2/vid2scene)（Apache-2.0）端到端重建替代：视频直接送入，内部完成抽帧 → HLoc（EigenPlaces 检索 + ALIKED/LightGlue 匹配）→ COLMAP SfM → gsplat MCMC 训练（带 bilateral grid 外观建模、高斯数量上限）。下游的语义分割、尺度标定、几何测量、风险规则与报告全部保留，只消费 vid2scene 产出的 COLMAP 相机模型与 `splat.ply`。

### 部署形态

vid2scene 运行在独立的 conda 环境（`vid2scene`），与本后端 venv 完全隔离，通过 subprocess 调用（`backend/pipeline/vid2scene_runner.py`）：

| 组件 | 说明 |
|------|------|
| conda env `vid2scene` | Python 3.12 + torch 2.7.1(cu128) + colmap 4.1.1 + pycolmap + hloc + gsplat(fork 1.5.3) + fused_ssim |
| `E:\.PJs\vid2scene\` | vid2scene 源码（clone + submodule），含少量 Windows 适配补丁 |
| `VID2SCENE_*` 环境变量 | 见 `backend/.env`：开关、源码路径、帧数/训练步数/高斯上限/SfM 方法 |

### 环境搭建（Windows 原生，无 Docker/WSL）

```powershell
git clone https://github.com/samuelm2/vid2scene.git E:\.PJs\vid2scene
cd E:\.PJs\vid2scene
git submodule update --init gsplat glomap Hierarchical-Localization spz
git -C gsplat submodule update --init   # gsplat 内部的 glm
# 然后依次执行仓库内 setup_env.ps1 / setup_env3.ps1（或按 setup_env*.log 记录的
# 步骤手动执行）：conda create → conda install colmap → pip torch/deps →
# fused-ssim 与 gsplat fork 用 MSVC+CUDA12.8 编译（注意 DISTUTILS_USE_SDK=1、
# TORCH_CUDA_ARCH_LIST=12.0+PTX、CUDA_HOME 指向真实 CUDA 路径）
```

注意事项（已踩坑）：
- RTX 50 系（sm_120）必须用 torch 2.7+cu128；vid2scene 官方锁的 torch 2.5.1+cu124 不含 sm_120 内核
- `pycolmap_parser` 需用最新 HEAD（旧 pin 在 numpy 2.x 下 `np.uint64(-1)` 溢出）
- ffmpeg ≥ 7 已移除 `-vsync`，`extract_frames.py` 已改用 `-fps_mode`
- 首次运行会从 HuggingFace/GitHub 下载 EigenPlaces/ALIKED/LightGlue 权重（约 500MB，缓存后不再下载）

### 回退开关

`VID2SCENE_ENABLED=false` 时回退到自研管线（`sfm.py` + `trainer.py`）；vid2scene 失败会以明确的「3D 重建失败」消息落库，不会静默降级。

### 与自研管线的实测对比（吕昊东房间.mp4，2.5 分钟，同机 RTX 5080 Laptop）

| 指标 | 自研（Scan 11） | vid2scene（最终实测，Scan 18） |
|------|----------------|-----------|
| 总耗时 | 4 小时 10 分 | **约 24 分钟**（重建 23.7 分钟 + 语义/报告约 1 分钟） |
| SfM | 699 帧 68 分钟、漂移严重（跳变比 12.5） | 282/282 帧注册、跳变比 4.8、重投影误差 1.16px |
| 训练 | 3 小时、损失 0.199、PSNR 16.5dB | 约 19 分钟、损失 0.03~0.12（MCMC + bilateral grid 外观建模） |
| 高斯数 | 566 万（膨胀 37 倍） | **91 万**（硬上限 120 万） |
| 标定 | 26.3% 分歧 → 失败 | **10.1% 分歧 → 标定成功**（门高 2.05m、床长 2.0m 一致），分歧超限时自动退化为单参考物+层高门禁兜底 |
| 报告 | 全部 unknown 假 100 分 | 米制房间 6.99×6.58m、床 2.22×0.76m、门宽 0.85m；全 unknown 不再产出分数 |
| 预览体积 | 168MB + 1.4GB | **24MB scene.ply + 74MB gaussian ply** |

## 许可证

本项目仅供学习与研究使用。依赖的开源组件（PyTorch、gsplat、Open3D、FastAPI、Flutter、vid2scene（Apache-2.0）等）遵循各自许可证；请勿将本项目用于商业用途前确认所有依赖的许可条款。
