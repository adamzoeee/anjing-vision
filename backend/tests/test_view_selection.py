import numpy as np

from pipeline.view_selection import select_training_views


def _camera(index: int, count: int) -> dict:
    angle = (index / max(count - 1, 1)) * np.pi * 1.2
    center = np.array([np.cos(angle), np.sin(angle), 0.05 * np.sin(angle * 2)])
    forward = -center / np.linalg.norm(center)
    right = np.cross(np.array([0.0, 0.0, 1.0]), forward)
    right /= np.linalg.norm(right)
    up = np.cross(forward, right)
    c2w = np.stack([right, up, forward], axis=1)
    return {"R": c2w.T, "center": center, "id": index}


def test_view_selection_reserves_real_holdout_and_reduces_duplicates():
    count = 100
    cameras = [_camera(index, count) for index in range(count)]
    # 每五帧内容相同，模拟连续视频中的高度冗余。
    images = [np.full((64, 96, 3), (index // 5) * 10 % 255, np.uint8) for index in range(count)]
    split = select_training_views(cameras, images, max_train_views=80)

    assert 10 <= len(split.holdout_indices) <= 15
    assert len(split.train_indices) == 48
    assert set(split.train_indices).isdisjoint(split.holdout_indices)
    assert split.train_indices[0] == 0
    assert split.train_indices[-1] == count - 1
    assert split.diagnostics["training_view_count"] == 48
    assert split.diagnostics["holdout_fraction"] == 0.12


def test_view_selection_rejects_mismatched_inputs():
    cameras = [_camera(index, 12) for index in range(12)]
    images = [np.zeros((32, 32, 3), np.uint8) for _ in range(11)]
    try:
        select_training_views(cameras, images)
    except ValueError as exc:
        assert "一一对应" in str(exc)
    else:
        raise AssertionError("mismatched inputs must fail")
