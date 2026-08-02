# 安龄智境后端

老人居住空间通行安全自动评估：视频/照片采集 → 3D 高斯泼溅重建 → 点云几何分析 → 风险评分与改造建议。

## 本地开发（最快路径，无 Docker）

1. `py -3.12 -m venv .venv && .venv/Scripts/pip install -r requirements.txt -r requirements-dev.txt`
   （GPU 环境准备详见 `ENV_SETUP.md`，RTX 50 系需 cu128 版 torch）
2. 复制 `.env.example` 为 `.env`，设置 `TASK_SYNC=true`（管道同步执行，无需 Redis/Celery）
3. `python scripts/download_models.py` 下载 SAM checkpoint（约 2.5GB，放 `models/`）
4. `uvicorn app.main:app --reload` 启动 API（文档：http://localhost:8000/docs）
5. 端到端管道：`python scripts/run_pipeline.py --input 你的视频.mp4 --outdir out/`

## Docker Compose（生产形态：PostgreSQL + Redis + MinIO + GPU worker）

需要 NVIDIA Container Toolkit；GPU 训练在 worker 容器内执行。

`docker compose up -d --build`

## 环境变量

见 `.env.example`。`STORAGE_BACKEND=minio` 时媒体存 MinIO；`TASK_SYNC=false` 时用 Celery 异步管道。

## 架构

```
Flutter App → FastAPI (REST+JWT) → Celery → 管道：ffmpeg 抽帧 → pycolmap SFM
→ gsplat 3DGS 训练 → 点云导出 → A4 纸标定 → SAM 语义 → 几何分析 → 规则评分 → 报告
存储：PostgreSQL（元数据）+ Redis（队列）+ MinIO（媒体/点云/报告）
```

## 测试

`python -m pytest tests/ -v`
