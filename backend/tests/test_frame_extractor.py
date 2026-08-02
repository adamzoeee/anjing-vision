import os
import subprocess
from pathlib import Path

import numpy as np
import pytest

from pipeline.frame_extractor import extract_frames, filter_sharp_frames

FFMPEG_DIR = "C:/Program Files/ffmpeg/bin"

# 模块级设置 PATH，确保本进程内任何位置调用 ffmpeg 都能找到（不依赖测试运行顺序）
os.environ["PATH"] = FFMPEG_DIR + ";" + os.environ.get("PATH", "")


def _make_test_video(path: Path, duration: float = 2.0, size: str = "320x240", rate: int = 30):
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size={size}:rate={rate}",
        "-pix_fmt", "yuv420p", str(path),
    ], check=True, capture_output=True)


def test_extract_frames_writes_jpgs(tmp_path):
    video = tmp_path / "test.mp4"
    _make_test_video(video)
    out = tmp_path / "frames"
    out.mkdir()
    paths = extract_frames(video, out, target_count=30)
    # 2 秒视频 @30fps → fps=15 → 约 30 帧；ffmpeg 输出帧数与 target_count 有容差
    assert 20 <= len(paths) <= 40
    assert all(p.suffix == ".jpg" for p in paths)
    assert paths == sorted(paths)


def test_extract_frames_cleans_old_frames(tmp_path):
    """重跑抽帧时旧帧必须被清理，避免帧数减少时残留混入结果。"""
    video = tmp_path / "test.mp4"
    _make_test_video(video, duration=4.0)
    out = tmp_path / "frames"
    out.mkdir()
    paths_a = extract_frames(video, out, target_count=60)   # 4s → fps=15 → ~60 帧
    assert len(paths_a) > 40
    paths_b = extract_frames(video, out, target_count=15)   # fps≈3.75 → ~15 帧
    assert 10 <= len(paths_b) <= 25
    assert paths_b == sorted(out.glob("frame_*.jpg"))       # 无旧帧残留


def test_extract_frames_raises_on_garbage(tmp_path):
    bad = tmp_path / "garbage.mp4"
    bad.write_bytes(b"this is not a video")
    with pytest.raises(ValueError):
        extract_frames(bad, tmp_path / "out", target_count=30)


def test_filter_sharp_frames_keeps_clear_images(tmp_path):
    import cv2
    rng = np.random.default_rng(42)
    sharp = tmp_path / "sharp.jpg"
    blur = tmp_path / "blur.jpg"
    noise = rng.integers(0, 255, (200, 200, 3), dtype=np.uint8)
    cv2.imwrite(str(sharp), noise)
    cv2.imwrite(str(blur), cv2.GaussianBlur(noise, (31, 31), 0))
    kept, dropped = filter_sharp_frames([sharp, blur], min_variance=50.0)
    assert sharp in kept and blur in dropped


def test_filter_sharp_frames_drops_unreadable_file(tmp_path):
    bad = tmp_path / "broken.jpg"
    bad.write_bytes(b"not an image")
    kept, dropped = filter_sharp_frames([bad], min_variance=50.0)
    assert kept == [] and dropped == [bad]
