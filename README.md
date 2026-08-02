# 安龄智境（Anjing Vision）

用普通手机拍摄一段房间视频，自动重建 3D 场景，评估老人居住空间的**通行安全性**——检测门宽、通道宽度、门槛高度、地面坡度、台阶与临时障碍物，生成带可视化标注的评估报告与改造建议。

本项目使用 **3D Gaussian Splatting（3DGS）** 取代 Apple RoomPlan 等专有方案：任何手机（无需 LiDAR）拍摄的视频即可重建，全部算法基于开源 Python 生态，Windows + NVIDIA GPU 即可本地运行。

## 特性

- **视频采集为主**：绕房间随意录 1~3 分钟视频即可，自动抽帧与清晰度过滤；也支持逐张拍照模式
- **3D 高斯泼溅重建**：pycolmap 恢复相机位姿 → gsplat 训练 3D 高斯场 → 导出稠密点云
- **真实尺度标定**：A4 纸双视角三角化（首选）+ 标准门高先验（兜底）
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
→ gsplat 3DGS 训练 → 点云导出与统计滤波 → A4 纸尺度标定 → GroundingDINO/SAM 语义分割
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
.venv/Scripts/celery -A app.tasks.celery_app worker --loglevel=info
```

### 采集与评估流程

1. 注册机构账号 → 新建评估项目（如「王奶奶家」）
2. 进入采集页，按引导绕房间录 1~3 分钟视频（**放一张 A4 纸**在地面显眼处用于标定）
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
│   │   ├── calibrator.py        # A4 纸尺度标定
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
- A4 纸标定依赖拍摄引导（纸张需在画面中可见），漏放时退回门高先验，精度降低
- 重建质量受拍摄条件影响：光线充足、慢速移动、避免反光面效果最佳
- 3D 交互预览目前是独立 Web 页面（`app/web/preview/index.html`），App 内的 webview 集成待完成

## 许可证

本项目仅供学习与研究使用。依赖的开源组件（PyTorch、gsplat、Open3D、FastAPI、Flutter 等）遵循各自许可证；请勿将本项目用于商业用途前确认所有依赖的许可条款。
