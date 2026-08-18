# 安龄智境后端

老人居住空间 3D 化：视频采集 → SLAM3R 稠密重建 → 点云清理/方向对齐 → SpatialLM 空间结构识别 → 3D 预览。

## 本地开发（最快路径，无 Docker）

1. `py -3.12 -m venv .venv && .venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt`
2. 用 `backend\scripts\slam3r\setup_slam3r_stack.ps1` 一键搭建重建栈（SLAM3R / SpatialLM 源码 + 两个独立 conda 环境 + 权重）
3. **Gaussian 连续场景分支**：`backend\scripts\gaussian\setup_gaussian.ps1`
   （克隆 slam3r 环境到纯 ASCII 路径 `D:\conda\slam3r` 并源码编译 gsplat；中文用户名/中文路径下必须走这一步，
   编译依赖系统 CUDA 12.8 + MSVC）。确认 `.env` 中 `GAUSSIAN_PYTHON=D:\conda\slam3r\python.exe`（默认即该路径）。
4. 复制 `.env.example` 为 `.env`，确认 `SLAM3R_*` / `SPATIALLM_*` 路径，设置 `TASK_SYNC=true`
5. `uvicorn app.main:app --reload` 启动 API（文档：http://localhost:8000/docs）
6. 端到端管道：`python scripts/run_pipeline.py --input 你的视频.mp4 --outdir out/`
7. 3D 预览：`http://localhost:8000/preview/{scan_id}`（默认 Gaussian 连续场景，可切“几何/调试模式”看点云）

## Docker Compose（生产形态：PostgreSQL + Redis + MinIO + GPU worker）

需要 NVIDIA Container Toolkit；GPU 重建在 worker 容器内执行（重建子进程要求两个 conda 环境，建议本地裸机形态）。

`docker compose up -d --build`

## 环境变量

见 `.env.example`。`STORAGE_BACKEND=minio` 时媒体存 MinIO；`TASK_SYNC=false` 时用 Celery 异步管道。

## 架构

```
Flutter App → FastAPI (REST+JWT) → 管道：ffmpeg 抽帧(4fps)
→ SLAM3R（slam3r conda 环境，稠密点云）→ Open3D 后处理（去噪/z-up/贴轴/层高缩放）
→ SpatialLM1.1-Qwen-0.5B（spatiallm conda 环境，墙/门/窗/家具 3D 框）
→ three.js 高密度点云预览（/preview/{scan_id}）
存储：PostgreSQL/SQLite（元数据）+ Redis（队列，可选）+ MinIO/本地（媒体/点云/报告）
```

## 测试

`python -m pytest tests/ -v`
