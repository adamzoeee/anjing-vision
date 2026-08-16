# 环境搭建记录（Windows）

本文件记录 Windows GPU 重建环境的搭建过程与常见问题。下列路径均为示例，
部署时应按目标机器的实际安装位置设置环境变量，不要照搬个人电脑路径。

> ⚠️ **重要：旧自研 3D 重建管线已退役。**
> `pipeline/sfm.py`、`pipeline/trainer.py`、`scripts/start_local_worker.cmd`
> 是退役的旧自研链路（慢、显存占用大），**不要再按旧文档/旧脚本启动它们**。
> 正式主线是 vid2scene 端到端重建：`pipeline/vid2scene_runner.py`。

## 部署步骤（新机器从零开始）

```bash
# 1. 后端 venv 与依赖（torch 必须 cu128，RTX 50 系为 Blackwell sm_120）
python -m venv backend\.venv
backend\.venv\Scripts\pip install -r backend/requirements.txt
backend\.venv\Scripts\pip install torch==2.7.* --index-url https://download.pytorch.org/whl/cu128

# 2. vid2scene 引擎 + 独立 conda 环境 + Windows 补丁（自动下载源码）
powershell -ExecutionPolicy Bypass -File backend/scripts/vid2scene/setup_vid2scene.ps1

# 3. 语义模型（SAM 2.4GB；GroundingDINO 首次运行联网缓存 ~900MB）
backend\.venv\Scripts\python backend/scripts/download_models.py

# 4. 复制 backend/.env.example 为 backend/.env，按本机路径修改
#    VID2SCENE_PYTHON=<conda 环境 python.exe>
#    VID2SCENE_CORE_DIR=<vid2scene/vid2scene_core>
#    APRILTAG_ENABLED / APRILTAG_SIZE_M ...

# 5. 端到端自检（vid2scene 主链，8GB 显存安全档）
backend\.venv\Scripts\python backend/scripts/smoke_reconstruction.py <video.mp4> <outdir> \
    --frames 60 --steps 2000 --gaussians 400000
```

自检通过的标准：`summary.json` 生成，`registered_frames > 0`、
`gaussian_count > 0`、`metric_scale_status` 符合预期。

## 8GB 显存下的训练档位（VID2SCENE_* 环境变量）

| 档位 | FRAMECOUNT | TRAINING_STEPS | MAX_GAUSSIANS | 预期耗时 |
|---|---|---|---|---|
| 自检档 | 60 | 2000 | 400000 | ~10-20 分钟 |
| 均衡档（默认） | 300 | 20000 | 1200000 | ~40-90 分钟 |
| 质量档 | 400 | 30000 | 1200000 | ~2 小时 |

显存不足（CUDA OOM）时**降低 MAX_GAUSSIANS / FRAMECOUNT**，不要动代码。

## 常见问题

1. **"未找到 vid2scene conda 环境（python.exe 不存在）"**
   - 未运行 `setup_vid2scene.ps1`，或 `.env` 中 `VID2SCENE_PYTHON` 指向错误。
   - `vid2scene_runner.py` 会自动加载 `backend/.env`，CLI 直跑也有效。
2. **"尺度标定失败，需要重新拍摄/检查标定纸"**
   - 视频里必须拍到 AprilTag（tagStandard41h12，默认 9cm）且多个角度可见；
   - 自检视频无 Tag 时给 smoke 脚本加 `--apriltag-size` 前的默认行为即失败，
     用 `APRILTAG_ENABLED=false` 只验证重建主链。
3. **语义阶段极慢**：后端 torch 装成了 CPU 版；按上面第 1 步的 cu128 源重装，
   用 `python -c "import torch; print(torch.cuda.is_available())"` 验证为 True。
4. **语义模型缺失**：管道会在重建开始前预检并直接给出中文提示（不会白跑重建）；
   缺失时补跑 `scripts/download_models.py`（SAM）并联网缓存 GroundingDINO。
5. **预览模型比训练结果模糊**：浏览器预览默认保留 80 万高斯（`PREVIEW_MAX_GAUSSIANS`），
   原始模型在 `work/<scan>/vid2scene/ply/splat.ply`。

## 踩坑记录

1. **open3d 不支持 Python 3.14/3.13**——必须 Python 3.12。
2. **torch 必须 cu128**——RTX 50 系（Blackwell sm_120）不支持 cu124/cu126 构建。
3. **CUDA network installer 失败（0xe0e00019）**——改用 NVIDIA redist zip 组件免安装，
   解压后合并 bin/include/lib，并创建 `version.json`（`{"cuda": {"version": "12.8.61"}}`）。
4. **VS 2022 Community 缺 C++ 工具链**——用 Build Tools 引导器装 VCTools 工作负载：
   `vs_BuildTools.exe --quiet --wait --norestart --installPath <dir> --add Microsoft.VisualStudio.Workload.VCTools --includeRecommended`
5. **pycolmap 4.1.1**：`incremental_mapping` 返回 dict；位姿用 `img.cam_from_world`
   （Rigid3d）→ `rotation().matrix()` / `translation()`。
6. **gsplat 1.5.3 源码分发**：Windows 下首次 import 需 nvcc + MSVC 并打补丁，
   见 `scripts/vid2scene/gsplat-windows.patch`（由 setup 脚本自动应用）。

## 验证命令

```bash
cd backend
# 后端环境 + GPU
.venv/Scripts/python -c "import torch, open3d, pycolmap, cv2; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))"
# 语义模型预检（应为空列表）
.venv/Scripts/python -c "from pipeline.semantic import preflight_semantic_models; print(preflight_semantic_models())"
```
