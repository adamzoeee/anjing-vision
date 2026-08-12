import subprocess
from pathlib import Path

import numpy as np
import pytest

from pipeline.frame_extractor import _ffmpeg_bin, extract_frames, filter_sharp_frames


def _make_test_video(path: Path, duration: float = 2.0, size: str = "320x240", rate: int = 30):
    subprocess.run([
        _ffmpeg_bin(), "-y", "-f", "lavfi",
        "-i", f"testsrc=duration={duration}:size={size}:rate={rate}",
        "-pix_fmt", "yuv420p", str(path),
    ], check=True, capture_output=True)


def test_extract_frames_writes_jpgs(tmp_path):
    video = tmp_path / "test.mp4"
    _make_test_video(video)
    out = tmp_path / "frames"
    out.mkdir()
    paths = extract_frames(video, out, target_count=30)
    # 2 秒视频 @30fps → 约 60 个输入帧；每 5 帧保存 1 帧 → 约 12 帧
    assert 11 <= len(paths) <= 13
    assert all(p.suffix == ".jpg" for p in paths)
    assert paths == sorted(paths)


def test_extract_frames_cleans_old_frames(tmp_path):
    """重跑抽帧时旧帧必须被清理，避免帧数减少时残留混入结果。"""
    video = tmp_path / "test.mp4"
    _make_test_video(video, duration=4.0)
    out = tmp_path / "frames"
    out.mkdir()
    paths_a = extract_frames(video, out, target_count=60)
    assert 23 <= len(paths_a) <= 25                         # 4s @30fps，每 5 帧保存 1 帧
    (out / "frame_99999.jpg").write_bytes(b"stale")
    paths_b = extract_frames(video, out, target_count=15)
    assert 23 <= len(paths_b) <= 25                         # target_count 不改变固定抽帧间隔
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
