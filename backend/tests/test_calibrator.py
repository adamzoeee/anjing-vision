import numpy as np
from pipeline.calibrator import (
    compute_scale_from_pixel,
    estimate_scale_from_references,
    fallback_single_reference_scale,
    scale_from_door_prior,
)


class _FakeRoomFrame:
    """最小房间框架桩：up 轴 = +Z，原点在原点。"""

    def __init__(self):
        self.axes = np.eye(3)
        self.origin = np.zeros(3)
        self.confidence = "high"
        self.ground_inlier_ratio = 0.9
        self.horizontal_method = "plane"


def test_fallback_single_reference_picks_plausible_ceiling():
    """门开着时门高分割不全 → 门比例给出 ~4.7m 层高被拒；床比例给出 ~3.1m 层高被采纳。"""
    rng = np.random.default_rng(3)
    # 合成房间点云（任意单位）：净高 8 单位
    floor = rng.uniform([-3, -3, 0], [3, 3, 0.05], (2000, 3))
    ceiling = rng.uniform([-3, -3, 8.0], [3, 3, 8.05], (1500, 3))
    walls = rng.uniform([-3, -3, 0], [3, 3, 8.0], (3000, 3))
    points = np.concatenate([floor, ceiling, walls])
    details = {
        "references": [
            {"object_type": "door", "dimension": "height", "meters": 2.05,
             "status": "used", "model_units": 3.5},   # 分割不全 → 比例虚高
            {"object_type": "bed", "dimension": "length", "meters": 2.0,
             "status": "used", "model_units": 5.1},   # 大平物 → 可靠
        ]
    }
    scale, meta = fallback_single_reference_scale(details, points, _FakeRoomFrame())
    assert scale is not None
    assert abs(scale - 2.0 / 5.1) < 1e-6
    assert meta["method"] == "single_reference_fallback"
    assert meta["reference"]["object_type"] == "bed"
    assert 1.8 <= meta["ceiling_m"] <= 4.5


def test_fallback_single_reference_prefers_bed_over_door():
    """床比例层高合理时优先床；只有床被门禁拒绝后才轮到门（门比例层高虚高也被拒）。"""
    rng = np.random.default_rng(5)
    points = rng.uniform([-3, -3, 0], [3, 3, 8.0], (4000, 3))
    details = {
        "references": [
            {"object_type": "door", "dimension": "height", "meters": 2.05,
             "status": "used", "model_units": 3.5},
            {"object_type": "bed", "dimension": "length", "meters": 2.0,
             "status": "used", "model_units": 5.1},
        ]
    }
    # 层高上限压到 2.6m：床(≈3.1m)被拒 → 门(≈4.7m)也被拒 → 无兜底
    scale, meta = fallback_single_reference_scale(
        details, points, _FakeRoomFrame(), max_ceiling_m=2.6
    )
    assert scale is None
    rejected = {item["reference"]["object_type"] for item in meta["rejected_candidates"]}
    assert rejected == {"bed", "door"}


def test_fallback_single_reference_rejects_all_when_no_plausible_ceiling():
    rng = np.random.default_rng(4)
    points = rng.uniform([-3, -3, 0], [3, 3, 2.8], (2000, 3))
    details = {
        "references": [
            {"object_type": "door", "dimension": "height", "meters": 2.05,
             "status": "used", "model_units": 3.5},
            {"object_type": "bed", "dimension": "length", "meters": 2.0,
             "status": "used", "model_units": 5.1},
        ]
    }
    # 门禁上限压到 2.0m：两个候选层高都超限 → 拒绝全部
    scale, meta = fallback_single_reference_scale(
        details, points, _FakeRoomFrame(), max_ceiling_m=2.0
    )
    assert scale is None
    assert len(meta["rejected_candidates"]) == 2


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


def test_three_references_use_two_consistent_measurements_and_mark_outlier():
    rng = np.random.default_rng(9)
    door = rng.uniform([-0.5, -0.02, 0], [0.5, 0.02, 2.3], (300, 3))
    table = rng.uniform([2, 0, 0], [3.7, 0.8, 0.75], (400, 3))
    # 分割不完整的床会给出明显错误比例，不能否决门和桌的一致结果。
    bed = rng.uniform([4, 0, 0], [4.8, 0.4, 0.3], (300, 3))
    points = np.concatenate([door, table, bed])
    scale, details = estimate_scale_from_references(
        points,
        {
            "门": list(range(300)),
            "桌子": list(range(300, 700)),
            "床": list(range(700, 1000)),
        },
        [
            {"object_type": "door", "dimension": "height", "meters": 2.3},
            {"object_type": "table", "dimension": "length", "meters": 1.7},
            {"object_type": "bed", "dimension": "width", "meters": 1.5},
        ],
    )
    assert scale is not None
    assert 0.85 < scale < 1.2
    assert details["used_count"] == 2
    assert details["candidate_count"] == 3
    assert any(item["status"] == "outlier" for item in details["references"])
