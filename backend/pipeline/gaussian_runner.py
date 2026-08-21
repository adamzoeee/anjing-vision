"""Gaussian 场景重建适配层：调用独立 gsplat 环境（D:\\conda\\slam3r）执行
位姿恢复（SLAM3R 相机系点云 ↔ 融合世界点云 RANSAC 刚体对齐）与 3DGS 训练，
输出与点云后处理同一米制坐标系下的 gaussian.ply。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
try:
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_ROOT / ".env", override=False)
except ImportError:
    pass

logger = logging.getLogger("anjing.pipeline.gaussian")

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts" / "gaussian"
DEFAULT_PYTHON = r"D:\conda\slam3r\python.exe"
DEFAULT_ITERS = int(os.getenv("GAUSSIAN_TRAIN_ITERS", "8000"))
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("GAUSSIAN_TIMEOUT_SECONDS", "0") or 0)
MAX_SAFE_VIEWS_8GB = 1024


def env_python() -> Path:
    configured = os.getenv("GAUSSIAN_PYTHON")
    if configured:
        return Path(configured)
    if Path(DEFAULT_PYTHON).is_file():
        return Path(DEFAULT_PYTHON)
    raise RuntimeError(f"Gaussian 环境 python 不存在：{DEFAULT_PYTHON}（可设置 GAUSSIAN_PYTHON）")


def _child_environment() -> dict:
    """子进程环境：ASCII 临时目录 + 已编译的 gsplat 扩展目录 + CUDA 路径。"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    env["TMP"] = r"D:\tmp-build"
    env["TEMP"] = r"D:\tmp-build"
    env["TMPDIR"] = r"D:\tmp-build"
    env["TORCH_EXTENSIONS_DIR"] = r"D:\torch-ext-build"
    python = env_python()
    env_root = python.parent.parent
    extra = [
        str(env_root / "Library" / "bin"),
        str(env_root / "Scripts"),
        str(env_root),
        str(python.parent),
        r"C:\Program Files\NVIDIA GPU Computing Toolkit\CUDA\v12.8\bin",
    ]
    env["PATH"] = os.pathsep.join([*extra, env.get("PATH", "")])
    return env


def run_gaussian(preds_dir: Path, work_dir: Path, *, iters: int = DEFAULT_ITERS,
                 progress_callback=None, kill_check=None,
                 timeout_s: float = DEFAULT_TIMEOUT_SECONDS) -> dict:
    preds_dir = Path(preds_dir).resolve()
    work_dir = Path(work_dir).resolve()
    gaussian_dir = work_dir / "gaussian"
    gaussian_dir.mkdir(parents=True, exist_ok=True)
    python = env_python()

    if not preds_dir.is_dir():
        raise RuntimeError(f"SLAM3R preds 目录不存在：{preds_dir}")
    raw_ply = next(iter(sorted(work_dir.rglob("*_recon.ply"))), None)
    if raw_ply is None:
        raise RuntimeError("未找到 SLAM3R 融合点云 *_recon.ply")
    alignment = work_dir / "postprocess" / "alignment.json"
    if not alignment.is_file():
        raise RuntimeError("alignment.json 不存在，请先运行点云后处理")

    steps = [
        ("位姿恢复", [str(SCRIPTS / "recover_poses.py"), str(work_dir),
                    "--max-frames", str(MAX_SAFE_VIEWS_8GB)]),
        ("Gaussian 训练", [str(SCRIPTS / "train_gsplat.py"), str(gaussian_dir), str(int(iters))]),
    ]
    started = time.perf_counter()
    for name, cmd in steps:
        logger.info("gaussian_stage %s command=%s", name, " ".join(cmd))
        proc = subprocess.Popen(
            [str(python), *cmd],
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace", bufsize=1,
            env=_child_environment(),
        )
        assert proc.stdout is not None
        tail: list[str] = []
        for raw in proc.stdout:
            line = raw.rstrip("\r\n")
            if "\r" in line:
                line = line.split("\r")[-1]
            tail.append(line)
            tail = tail[-40:]
            if progress_callback is not None:
                progress_callback(name, line)
            if kill_check is not None and kill_check():
                proc.terminate()
                raise RuntimeError("Gaussian 重建被取消")
            if timeout_s and time.perf_counter() - started > timeout_s:
                proc.terminate()
                raise RuntimeError(f"Gaussian 重建超过 {timeout_s:.0f} 秒限制")
        code = proc.wait()
        if code != 0:
            detail = "\n".join(tail)
            logger.error("gaussian_failed stage=%s exit=%s\n%s", name, code, detail)
            raise RuntimeError(f"Gaussian {name}失败（退出码 {code}）：{detail[-1500:]}")
        logger.info("gaussian_stage_done %s", name)

    ply = gaussian_dir / "gaussian.ply"
    if not ply.is_file():
        raise RuntimeError("Gaussian 训练未产出 gaussian.ply")
    cameras = json.loads((gaussian_dir / "cameras.json").read_text(encoding="utf-8"))
    return {
        "ply": ply,
        "cameras_json": gaussian_dir / "cameras.json",
        "views": len(cameras),
        "seconds": round(time.perf_counter() - started, 1),
    }


def run_pose_recovery(work_dir: Path, *, max_frames: int = MAX_SAFE_VIEWS_8GB,
                      timeout_s: float = 1200.0, progress_callback=None) -> dict:
    """只恢复逐帧相机位姿（视频证据），不训练 Gaussian。

    测量阶段“点云+视频融合”的通用数据通路：每个视频都能得到 cameras.json
    （对齐米制系位姿）+ 对应帧图像，供 video_box_refiner 修正家具尺寸。
    Gaussian 开关只控制后续 3DGS 训练，与位姿恢复无关。
    """
    work_dir = Path(work_dir).resolve()
    gaussian_dir = work_dir / "gaussian"
    gaussian_dir.mkdir(parents=True, exist_ok=True)
    cameras_json = gaussian_dir / "cameras.json"
    if cameras_json.is_file():
        cameras = json.loads(cameras_json.read_text(encoding="utf-8"))
        return {"cameras_json": cameras_json, "views": len(cameras), "reused": True}
    preds_npy = work_dir / "slam3r" / "scene" / "preds" / "registered_pcds.npy"
    if not preds_npy.is_file():
        raise RuntimeError("registered_pcds.npy 不存在，无法恢复位姿")
    raw_ply = next(iter(sorted(work_dir.rglob("*_recon.ply"))), None)
    alignment = work_dir / "postprocess" / "alignment.json"
    if raw_ply is None or not alignment.is_file():
        raise RuntimeError("位姿恢复依赖缺失（*_recon.ply / alignment.json）")
    python = env_python()
    safe_max_frames = min(max(int(max_frames), 30), MAX_SAFE_VIEWS_8GB)
    cmd = [str(python), str(SCRIPTS / "recover_poses.py"), str(work_dir),
           "--max-frames", str(safe_max_frames)]
    started = time.perf_counter()
    logger.info("pose_recovery command=%s", " ".join(cmd))
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace", bufsize=1,
        env=_child_environment(),
    )
    assert proc.stdout is not None
    tail: list[str] = []
    for raw in proc.stdout:
        line = raw.rstrip("\r\n")
        if "\r" in line:
            line = line.split("\r")[-1]
        tail.append(line)
        tail = tail[-40:]
        if progress_callback is not None:
            progress_callback("pose_recovery", line)
        if timeout_s and time.perf_counter() - started > timeout_s:
            proc.terminate()
            raise RuntimeError(f"位姿恢复超过 {timeout_s:.0f} 秒限制")
    code = proc.wait()
    if code != 0 or not cameras_json.is_file():
        detail = "\n".join(tail)
        logger.error("pose_recovery_failed exit=%s\n%s", code, detail)
        raise RuntimeError(f"位姿恢复失败（退出码 {code}）：{detail[-1500:]}")
    cameras = json.loads(cameras_json.read_text(encoding="utf-8"))
    return {"cameras_json": cameras_json, "views": len(cameras),
            "seconds": round(time.perf_counter() - started, 1)}
