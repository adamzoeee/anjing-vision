"""SLAM3R 视频稠密重建适配层。

调用独立的 slam3r conda 环境（E:\\PJs\\slam3r 源码 + siyan824 权重）：
  1. ffmpeg 抽帧（默认 4fps，HEVC/H264/任意输入统一转 JPEG）；
  2. subprocess 运行 recon.py（offline 模式）逐帧 I2P → L2W 全局注册；
  3. 返回稠密彩色点云 PLY（scene_recon.ply）与逐帧点云/预测缓存。

SLAM3R 输出为「无相机显式估计」的稠密点云；方向任意、尺度未定，
下游 scene_postprocess 负责去噪、z-up/墙面方向对齐与米制缩放。
"""
from __future__ import annotations

import json
import logging
import os
import subprocess
import time
from functools import lru_cache
from pathlib import Path

_BACKEND_ROOT = Path(__file__).resolve().parents[1]
try:  # CLI/worker 直跑时也读取 backend/.env 中的 SLAM3R_* 配置
    from dotenv import load_dotenv

    load_dotenv(_BACKEND_ROOT / ".env", override=False)
except ImportError:  # 无 dotenv 时退化为纯环境变量
    pass

logger = logging.getLogger("anjing.pipeline.slam3r")

SLAM3R_DIR = Path(os.getenv("SLAM3R_DIR", r"E:\.PJs\slam3r")).resolve()
SLAM3R_CONDA_ENV = os.getenv("SLAM3R_CONDA_ENV", "slam3r")
SLAM3R_I2P_WEIGHTS = Path(
    os.getenv("SLAM3R_I2P_WEIGHTS", r"E:\.PJs\models\slam3r_i2p\slam3r_i2p.pth")
)
SLAM3R_L2W_WEIGHTS = Path(
    os.getenv("SLAM3R_L2W_WEIGHTS", r"E:\.PJs\models\slam3r_l2w\slam3r_l2w.pth")
)
DEFAULT_FPS = float(os.getenv("SLAM3R_FPS", "4"))
DEFAULT_KEYFRAME_STRIDE = int(os.getenv("SLAM3R_KEYFRAME_STRIDE", "3"))
DEFAULT_WIN_R = int(os.getenv("SLAM3R_WIN_R", "5"))
DEFAULT_NUM_SCENE_FRAME = int(os.getenv("SLAM3R_NUM_SCENE_FRAME", "10"))
DEFAULT_INITIAL_WINSIZE = int(os.getenv("SLAM3R_INITIAL_WINSIZE", "5"))
DEFAULT_CONF_THRES_L2W = float(os.getenv("SLAM3R_CONF_THRES_L2W", "12"))
DEFAULT_CONF_THRES_I2P = float(os.getenv("SLAM3R_CONF_THRES_I2P", "1.5"))
DEFAULT_NUM_POINTS_SAVE = int(os.getenv("SLAM3R_NUM_POINTS_SAVE", "3000000"))
DEFAULT_TIMEOUT_SECONDS = float(os.getenv("SLAM3R_TIMEOUT_SECONDS", "0") or 0)
_FFMPEG_DIRS = (r"C:\ffmpeg\bin", r"C:\Program Files\ffmpeg\bin")


def find_ffmpeg() -> str:
    """定位 ffmpeg.exe：显式配置 → PATH → 常见安装目录。"""
    configured = os.getenv("FFMPEG_BIN")
    if configured and Path(configured).is_file():
        return str(Path(configured))
    for candidate in ("ffmpeg",):
        from shutil import which

        found = which(candidate)
        if found:
            return found
    for directory in _FFMPEG_DIRS:
        candidate = Path(directory) / "ffmpeg.exe"
        if candidate.is_file():
            return str(candidate)
    raise RuntimeError("未找到 ffmpeg（C:\\ffmpeg\\bin 或 PATH），无法抽帧")


@lru_cache(maxsize=1)
def slam3r_env_python() -> Path:
    """定位 slam3r conda 环境的 python.exe（可用 SLAM3R_PYTHON 覆盖）。"""
    configured = os.getenv("SLAM3R_PYTHON")
    if configured:
        return Path(configured)
    candidates: list[Path] = []
    for root in (
        Path(os.environ.get("USERPROFILE", "C:\\")) / ".conda",
        Path(os.environ.get("USERPROFILE", "C:\\")) / "miniconda3",
        Path(os.environ.get("USERPROFILE", "C:\\")) / "anaconda3",
        Path("C:\\") / "anaconda3",
        Path("C:\\") / "ProgramData" / "miniconda3",
        Path("C:\\") / "ProgramData" / "anaconda3",
    ):
        candidates.append(root / "envs" / SLAM3R_CONDA_ENV / "python.exe")
    try:
        base = subprocess.run(
            ["conda", "info", "--base"],
            capture_output=True, text=True, check=False, timeout=60,
        ).stdout.strip()
    except Exception:  # noqa: BLE001 - conda 不在 PATH 时落到候选路径兜底
        base = ""
    if base:
        candidates.append(Path(base) / "envs" / SLAM3R_CONDA_ENV / "python.exe")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    raise RuntimeError(
        "未找到 slam3r conda 环境（python.exe 不存在）。"
        "请先创建 slam3r 环境（torch 2.7 cu128 + SLAM3R requirements）或设置 SLAM3R_PYTHON。"
    )


def _child_environment() -> dict:
    """子进程环境：清空 PYTHONPATH/PYTHONHOME，附加 ffmpeg 目录。"""
    env = dict(os.environ)
    env.pop("PYTHONPATH", None)
    env.pop("PYTHONHOME", None)
    python = slam3r_env_python()
    env_root = python.parent.parent
    extra = [
        str(env_root / "Library" / "bin"),
        str(env_root / "Scripts"),
        str(env_root),
        str(python.parent),
    ]
    extra += [d for d in _FFMPEG_DIRS if os.path.isdir(d)]
    env["PATH"] = os.pathsep.join([*extra, env.get("PATH", "")])
    return env


def extract_frames(video: Path, frames_dir: Path, fps: float = DEFAULT_FPS) -> int:
    """ffmpeg 抽帧为 frame_%05d.jpg（SLAM3R Seq_Data 要求带序号的图片名）。

    追加清晰度过滤：blur 检测滤掉运动模糊帧（快速甩动导致的糊帧会污染重建）。
    返回抽取的帧数。
    """
    video = Path(video).resolve()
    frames_dir = Path(frames_dir)
    frames_dir.mkdir(parents=True, exist_ok=True)
    for old in frames_dir.glob("frame_*.jpg"):
        old.unlink()
    command = [
        find_ffmpeg(),
        "-y",
        "-i", str(video),
        "-vf", f"fps={fps}",
        "-q:v", "2",
        str(frames_dir / "frame_%05d.jpg"),
    ]
    logger.info("slam3r_extract_frames video=%s fps=%s", video, fps)
    result = subprocess.run(command, capture_output=True, text=True, check=False, timeout=3600)
    if result.returncode != 0:
        raise RuntimeError(f"ffmpeg 抽帧失败：{result.stderr[-800:]}")
    frames = sorted(frames_dir.glob("frame_*.jpg"))
    if not frames:
        raise RuntimeError("ffmpeg 抽帧未产出任何图像")
    logger.info("slam3r_extract_frames_done count=%d", len(frames))
    return len(frames)


_STAGE_MARKERS = [
    ("Loading model", 0.02),
    ("finish pre-extracting img tokens", 0.10),
    ("choose", 0.12),  # adapt_keyframe_stride: "choose X as the stride"
    ("initialize scene with", 0.15),
    ("I2P resonstruction", 0.18),  # tqdm 阶段，按 n/total 细化
    ("pre-registering", 0.52),
    ("registering", 0.55),  # tqdm 阶段，按 n/total 细化
    ("mean confidence for whole scene", 0.96),
    ("resampling", 0.98),
]


def _tqdm_fraction(line: str, base: float, span: float) -> float | None:
    """解析 tqdm 行 '  123/600 [..]' 中的 n/total，映射到 base..base+span。"""
    try:
        chunk = line.split("[", 1)[0].strip()
        if "/" in chunk and all(part.strip().isdigit() for part in chunk.split("/", 1)):
            done, total = (int(part) for part in chunk.split("/", 1))
            if total > 0:
                return base + span * min(1.0, done / total)
    except ValueError:
        pass
    return None


def map_progress(line: str) -> float | None:
    """把一行 stdout/stderr 映射为 0..1 重建进度；无法识别返回 None。"""
    if "/" in line and ("I2P" in line or "register" in line.lower()):
        frac = _tqdm_fraction(line, 0.18, 0.34) if "I2P" in line else _tqdm_fraction(line, 0.55, 0.40)
        if frac is not None:
            return frac
    for marker, progress in _STAGE_MARKERS:
        if marker in line:
            return progress
    return None


def run_reconstruction(
    frames_dir: Path,
    work_dir: Path,
    *,
    test_name: str = "scene",
    fps: float = DEFAULT_FPS,
    keyframe_stride: int = DEFAULT_KEYFRAME_STRIDE,
    win_r: int = DEFAULT_WIN_R,
    num_scene_frame: int = DEFAULT_NUM_SCENE_FRAME,
    initial_winsize: int = DEFAULT_INITIAL_WINSIZE,
    conf_thres_l2w: float = DEFAULT_CONF_THRES_L2W,
    conf_thres_i2p: float = DEFAULT_CONF_THRES_I2P,
    num_points_save: int = DEFAULT_NUM_POINTS_SAVE,
    progress_callback=None,
    kill_check=None,
    timeout_s: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """运行 SLAM3R 离线重建，返回产物路径字典。

    progress_callback(progress: float, line: str) 每行输出调用；
    kill_check() 返回 True 时终止子进程并抛出 RuntimeError。
    """
    frames_dir = Path(frames_dir).resolve()
    work_dir = Path(work_dir).resolve()
    work_dir.mkdir(parents=True, exist_ok=True)
    i2p = Path(SLAM3R_I2P_WEIGHTS)
    l2w = Path(SLAM3R_L2W_WEIGHTS)
    if not i2p.is_file():
        raise RuntimeError(f"SLAM3R I2P 权重不存在：{i2p}")
    if not l2w.is_file():
        raise RuntimeError(f"SLAM3R L2W 权重不存在：{l2w}")

    command = [
        str(slam3r_env_python()),
        str(SLAM3R_DIR / "recon.py"),
        # SLAM3R 的 Seq_Data 用 '/' 切分 img_dir 推导场景名；Windows 反斜杠会
        # 让场景名变成整条带盘符路径，最终 scene_recon.ply 被写到意料之外的位置。
        "--img_dir", str(frames_dir).replace("\\", "/"),
        "--test_name", test_name,
        "--save_dir", str(work_dir).replace("\\", "/"),
        "--i2p_weights", str(i2p),
        "--l2w_weights", str(l2w),
        "--keyframe_stride", str(int(keyframe_stride)),
        "--win_r", str(int(win_r)),
        "--num_scene_frame", str(int(num_scene_frame)),
        "--initial_winsize", str(int(initial_winsize)),
        "--conf_thres_l2w", str(float(conf_thres_l2w)),
        "--conf_thres_i2p", str(float(conf_thres_i2p)),
        "--num_points_save", str(int(num_points_save)),
        "--save_all_views",
        "--save_preds",
        "--gpu_id", "0",
    ]
    started = time.perf_counter()
    logger.info("slam3r_start frames=%s work_dir=%s command=%s", frames_dir, work_dir, " ".join(command))
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        encoding="utf-8",
        errors="replace",
        bufsize=1,
        env=_child_environment(),
        cwd=str(SLAM3R_DIR),
    )
    assert process.stdout is not None
    tail: list[str] = []
    for raw in process.stdout:
        line = raw.rstrip("\r\n")
        if "\r" in line:  # tqdm 进度行
            line = line.split("\r")[-1]
        tail.append(line)
        tail = tail[-60:]
        if progress_callback is not None:
            progress = map_progress(line)
            if progress is not None:
                progress_callback(progress)
        if kill_check is not None and kill_check():
            process.terminate()
            raise RuntimeError("SLAM3R 重建被取消")
        if timeout_s and time.perf_counter() - started > timeout_s:
            process.terminate()
            raise RuntimeError(f"SLAM3R 重建超过 {timeout_s:.0f} 秒限制")
    return_code = process.wait()
    if return_code != 0:
        detail = "\n".join(tail)
        logger.error("slam3r_failed exit=%s\n%s", return_code, detail)
        raise RuntimeError(f"SLAM3R 重建失败（退出码 {return_code}），日志尾部：\n{detail[-2000:]}")
    elapsed = time.perf_counter() - started

    # scene 名由输入目录派生（room_frames 之类）；Windows 下场景名可能带路径
    # 导致 ply 落到 work_dir 其他位置，做递归兜底检索。
    result_dir = work_dir / test_name
    candidate_plys = sorted(result_dir.glob("*_recon.ply")) if result_dir.is_dir() else []
    if not candidate_plys:
        candidate_plys = sorted(p for p in work_dir.rglob("*_recon.ply") if p.is_file())
    ply = candidate_plys[-1] if candidate_plys else result_dir / "scene_recon.ply"
    if not ply.is_file():
        raise RuntimeError(f"SLAM3R 未产出稠密点云 PLY：{ply}（结果目录 {result_dir}）")
    per_frame_plys = sorted((work_dir / test_name).glob("frame_*.ply"))
    preds_dir = work_dir / test_name / "preds"
    metadata = {
        "backend": "slam3r",
        "test_name": test_name,
        "fps": fps,
        "keyframe_stride": keyframe_stride,
        "win_r": win_r,
        "seconds": round(elapsed, 1),
        "num_points_save": num_points_save,
        "conf_thres_l2w": conf_thres_l2w,
        "conf_thres_i2p": conf_thres_i2p,
    }
    (work_dir / "slam3r_metadata.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info("slam3r_done seconds=%.1f ply=%s per_frame=%d", elapsed, ply, len(per_frame_plys))
    return {
        "ply": ply,
        "result_dir": work_dir / test_name,
        "per_frame_plys": per_frame_plys,
        "preds_dir": preds_dir if preds_dir.is_dir() else None,
        "frames_dir": frames_dir,
        "seconds": elapsed,
        "metadata": metadata,
    }
