import subprocess
from pathlib import Path

import numpy as np
import pytest

import pipeline.frame_extractor as frame_extractor
from pipeline.frame_extractor import (
    _ffmpeg_bin,
    _max_dropped_run,
    _select_dynamic_frames,
    extract_frames,
    filter_sharp_frames,
    protect_sfm_continuity,
)


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
    # 静止/低运动区域允许降采样，但动态选择不得超过约 8 张/秒。
    assert 3 <= len(paths) <= 16
    assert all(p.suffix == ".jpg" for p in paths)
    assert paths == sorted(paths)


def test_extract_frames_cleans_old_frames(tmp_path):
    """重跑抽帧时旧帧必须被清理，避免帧数减少时残留混入结果。"""
    video = tmp_path / "test.mp4"
    _make_test_video(video, duration=4.0)
    out = tmp_path / "frames"
    out.mkdir()
    paths_a = extract_frames(video, out, target_count=60)
    assert 7 <= len(paths_a) <= 32
    (out / "frame_99999.jpg").write_bytes(b"stale")
    paths_b = extract_frames(video, out, target_count=15)
    assert len(paths_b) == len(paths_a)                     # target_count 不改变动态抽帧策略
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


@pytest.mark.parametrize(
    ("score", "method", "expected_min", "expected_max"),
    [
        (0.02, "flow", 7.0, 8.0),
        (0.006, "flow", 4.8, 5.2),
        (0.0, "flow", 2.0, 2.3),
    ],
)
def test_dynamic_selection_adapts_to_motion(
    tmp_path, monkeypatch, score, method, expected_min, expected_max,
):
    paths = []
    for index in range(61):
        path = tmp_path / f"frame_{index + 1:05d}.jpg"
        image = np.full((40, 60), index % 255, dtype=np.uint8)
        import cv2
        cv2.imwrite(str(path), image)
        paths.append(path)
    monkeypatch.setattr(frame_extractor, "_motion_score", lambda _a, _b: (score, method))
    selected, stats = _select_dynamic_frames(paths, candidate_fps=15.0)
    effective_fps = (len(selected) - 1) / 4.0
    assert expected_min <= effective_fps <= expected_max
    assert stats["max_sample_gap_seconds"] <= 0.5


def test_protect_sfm_continuity_restores_best_readable_bridges(tmp_path):
    import cv2

    paths = []
    for index in range(12):
        path = tmp_path / f"frame_{index + 1:05d}.jpg"
        image = np.zeros((80, 80), dtype=np.uint8)
        if index in {0, 11}:
            image[::2, ::2] = 255
        elif index == 5:
            image[20:60:2, 20:60:2] = 180
        else:
            image[30:50, 30:50] = 20 + index
        cv2.imwrite(str(path), image)
        paths.append(path)
    sharp = [paths[0], paths[-1]]

    sfm_frames, recovered, stats = protect_sfm_continuity(
        paths, sharp, max_dropped_run=2,
    )

    assert paths[5] in recovered
    assert set(sharp).issubset(sfm_frames)
    assert set(recovered).isdisjoint(sharp)
    assert stats["candidate_frames"] == 12
    assert stats["sharp_frames"] == 2
    assert stats["sfm_bridge_frames_restored"] == len(recovered)
    assert stats["max_dropped_run_before_recovery"] == 10
    assert stats["max_dropped_run_after_recovery"] <= 2
    assert _max_dropped_run(paths, set(sfm_frames)) <= 2


def test_protect_sfm_continuity_never_restores_unreadable_file(tmp_path):
    import cv2

    first = tmp_path / "frame_00001.jpg"
    broken = tmp_path / "frame_00002.jpg"
    last = tmp_path / "frame_00003.jpg"
    cv2.imwrite(str(first), np.eye(20, dtype=np.uint8) * 255)
    broken.write_bytes(b"not an image")
    cv2.imwrite(str(last), np.eye(20, dtype=np.uint8) * 255)

    sfm_frames, recovered, stats = protect_sfm_continuity(
        [first, broken, last], [first, last], max_dropped_run=0,
    )

    assert broken not in recovered
    assert broken not in sfm_frames
    assert stats["max_dropped_run_after_recovery"] == 1
