import numpy as np
from pipeline.calibrator import compute_scale_from_pixel, estimate_scale_from_references, scale_from_door_prior


def test_compute_scale_from_pixel():
    # A4 长边 0.297m 在 2.0m 距离、焦距 600px 时，像素长度约 89px
    scale = compute_scale_from_pixel(pixel_len=89.1, physical_len=0.297, distance=2.0, focal=600.0)
    assert 0.9 < scale < 1.1  # 单位: 米/单位（此处相机单位=米）


def test_compute_scale_from_pixel_direction():
    """方向验证：SFM 单位深度更浅（distance=1.0）时，米制深度 2.0 → 尺度 2.0 米/单位。"""
    scale = compute_scale_from_pixel(pixel_len=89.1, physical_len=0.297, distance=1.0, focal=600.0)
    assert abs(scale - 2.0) < 1e-6
    # 反过来：SFM 单位深度更深（distance=4.0）→ 尺度 0.5 米/单位
    scale2 = compute_scale_from_pixel(pixel_len=89.1, physical_len=0.297, distance=4.0, focal=600.0)
    assert abs(scale2 - 0.5) < 1e-6


def test_scale_from_door_prior():
    # 门高先验：点云中门框高度 1.6 单位，标准 2.0m → 尺度 1.25 m/unit
    s = scale_from_door_prior(door_height_units=1.6, standard_height=2.0)
    assert abs(s - 1.25) < 1e-6


def test_compute_scale_invalid_inputs():
    import pytest
    with pytest.raises(ValueError):
        compute_scale_from_pixel(pixel_len=0, physical_len=0.297, distance=2.0, focal=600.0)
    with pytest.raises(ValueError):
        scale_from_door_prior(door_height_units=0)


def test_known_object_references_produce_consistent_scale():
    rng = np.random.default_rng(7)
    door = rng.uniform([-0.5, -0.02, 0], [0.5, 0.02, 2.0], (300, 3))
    bed = rng.uniform([2, 0, 0], [4, 1.5, 0.5], (400, 3))
    scale, details = estimate_scale_from_references(
        np.concatenate([door, bed]),
        {"门": list(range(300)), "床": list(range(300, 700))},
        [
            {"object_type": "door", "dimension": "height", "meters": 2.0},
            {"object_type": "bed", "dimension": "length", "meters": 2.0},
        ],
    )
    assert scale is not None
    assert 0.9 < scale < 1.2
    assert details["used_count"] == 2


def test_known_object_references_reject_disagreement():
    rng = np.random.default_rng(8)
    door = rng.uniform([-0.5, 0, 0], [0.5, 0.02, 2], (300, 3))
    bed = rng.uniform([2, 0, 0], [4, 1.5, 0.5], (400, 3))
    scale, details = estimate_scale_from_references(
        np.concatenate([door, bed]),
        {"门": list(range(300)), "床": list(range(300, 700))},
        [
            {"object_type": "door", "dimension": "height", "meters": 2.0},
            {"object_type": "bed", "dimension": "length", "meters": 4.0},
        ],
    )
    assert scale is None
    assert "不一致" in details["reason"]
