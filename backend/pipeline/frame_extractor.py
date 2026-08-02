"""视频抽帧：ffmpeg 均匀抽帧 + Laplacian 清晰度过滤。"""
import subprocess
from pathlib import Path

import cv2

TARGET_COUNT = 200       # 重建输入目标帧数
MIN_VARIANCE = 60.0      # Laplacian 方差阈值（低于视为模糊）


def extract_frames(video: Path, out_dir: Path, target_count: int = TARGET_COUNT) -> list[Path]:
    """用 ffmpeg 从视频均匀抽帧为 jpg（先探测时长，再按间隔抽）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.jpg"):
        old.unlink()
    probe = subprocess.run(
        ["ffmpeg", "-i", str(video)],
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
    fps = max(target_count / duration, 0.5)
    subprocess.run([
        "ffmpeg", "-y", "-i", str(video),
        "-vf", f"fps={fps},scale=1600:-2",
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
