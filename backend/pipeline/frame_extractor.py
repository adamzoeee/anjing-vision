"""视频抽帧：ffmpeg 均匀抽帧 + Laplacian 清晰度过滤。"""
import os
import shutil
import subprocess
from pathlib import Path

import cv2

TARGET_COUNT = 200       # 重建输入目标帧数
MIN_VARIANCE = 60.0      # Laplacian 方差阈值（低于视为模糊）
MIN_SAMPLE_FPS = 2.5     # 手机绕拍需要足够相邻重叠，不能只按总帧数稀疏抽样
MAX_SAMPLE_FPS = 30.0    # 短视频仍允许按 target_count 获取足够帧
MAX_RECONSTRUCTION_FRAMES = 240  # 限制显存/匹配开销，同时保留长视频连续性

_FFMPEG_CANDIDATES = (
    "C:/Program Files/ffmpeg/bin/ffmpeg.exe",
    "C:/ffmpeg/bin/ffmpeg.exe",
    "/usr/bin/ffmpeg",
)


def _ffmpeg_bin() -> str:
    """探测 ffmpeg 可执行文件：PATH 优先，其次常见安装路径。"""
    configured = os.getenv("FFMPEG_PATH")
    if configured:
        if os.path.isfile(configured):
            return configured
        raise FileNotFoundError(f"FFMPEG_PATH 指向的文件不存在: {configured}")
    p = shutil.which("ffmpeg")
    if p:
        return p
    for cand in _FFMPEG_CANDIDATES:
        if os.path.exists(cand):
            return cand
    raise FileNotFoundError("ffmpeg 未安装或不在 PATH")


def extract_frames(video: Path, out_dir: Path, target_count: int = TARGET_COUNT) -> list[Path]:
    """用 ffmpeg 从视频均匀抽帧为 jpg（先探测时长，再按间隔抽）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.jpg"):
        old.unlink()
    probe = subprocess.run(
        [_ffmpeg_bin(), "-i", str(video)],
        capture_output=True, text=True,
    )
    duration = 0.0
    for line in probe.stderr.splitlines():
        if "Duration:" in line:
            parts = line.split("Duration:")[1].split(",")[0].strip().split(":")
            duration = int(parts[0]) * 3600 + int(parts[1]) * 60 + float(parts[2])
            break
    if duration <= 0:
        raise ValueError(f"无法解析视频时长: {video}")
    # 旧实现对 1～2 分钟视频通常只取约 2fps；实测相邻 ORB 特征重叠中位数
    # 仅约 20%，会让快速转弯处的轨迹断裂。长视频按约 2.5fps 保留连续性，
    # 但总候选帧限制在 240，避免一次把数百张 1080p 图片常驻 GPU。
    desired_count = min(
        max(float(target_count), duration * MIN_SAMPLE_FPS),
        float(MAX_RECONSTRUCTION_FRAMES),
    )
    fps = min(desired_count / duration, MAX_SAMPLE_FPS)
    subprocess.run([
        _ffmpeg_bin(), "-y", "-i", str(video),
        # 只缩小不放大：低分辨率输入放大后插值模糊，会误伤清晰度过滤
        "-vf", f"fps={fps},scale='min(1600,iw)':-2",
        "-q:v", "2", str(out_dir / "frame_%05d.jpg"),
    ], check=True, capture_output=True)
    return sorted(out_dir.glob("frame_*.jpg"))


def filter_sharp_frames(
    frame_paths: list[Path], min_variance: float = MIN_VARIANCE,
) -> tuple[list[Path], list[Path]]:
    """按 Laplacian 方差过滤模糊帧。返回 (保留, 丢弃)。"""
    kept, dropped = [], []
    for p in frame_paths:
        img = cv2.imread(str(p), cv2.IMREAD_GRAYSCALE)
        if img is None:
            dropped.append(p)
            continue
        var = cv2.Laplacian(img, cv2.CV_64F).var()
        (kept if var >= min_variance else dropped).append(p)
    return kept, dropped
