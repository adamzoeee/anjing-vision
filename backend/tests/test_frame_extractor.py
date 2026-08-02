import os
import subprocess
from pathlib import Path

from pipeline.frame_extractor import extract_frames, filter_sharp_frames

FFMPEG_DIR = "C:/Program Files/ffmpeg/bin"

# 模块级设置 PATH，确保本进程内任何位置调用 ffmpeg 都能找到（不依赖测试运行顺序）
os.environ["PATH"] = FFMPEG_DIR + ";" + os.environ.get("PATH", "")


def _make_test_video(path: Path, n_frames: int = 60, size: str = "320x240"):
    subprocess.run([
        "ffmpeg", "-y", "-f", "lavfi",
        "-i", f"testsrc=duration=2:size={size}:rate=30",
        "-pix_fmt", "yuv420p", str(path),
    ], check=True, capture_output=True)


def test_extract_frames_writes_jpgs(tmp_path):
    video = tmp_path / "test.mp4"
    _make_test_video(video)
    out = tmp_path / "frames"
    out.mkdir()
    paths = extract_frames(video, out, target_count=30)
    assert len(paths) >= 10
    assert all(p.suffix == ".jpg" for p in paths)


def test_filter_sharp_frames_keeps_clear_images(tmp_path):
    import cv2, numpy as np
    sharp = tmp_path / "sharp.jpg"
    blur = tmp_path / "blur.jpg"
    cv2.imwrite(str(sharp), np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8))
    cv2.imwrite(str(blur), cv2.GaussianBlur(np.random.randint(0, 255, (200, 200, 3), dtype=np.uint8), (31, 31), 0))
    kept, dropped = filter_sharp_frames([sharp, blur], min_variance=50.0)
    assert sharp in kept and blur in dropped
