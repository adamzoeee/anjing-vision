import numpy as np

from pipeline.quality import assess_gaussians, assess_metric_scene, assess_sfm


def _cameras(count: int):
    return [{"center": np.array([index * 0.1, 0.0, 1.5])} for index in range(count)]


def test_sfm_quality_accepts_covered_room_sequence():
    points = np.random.default_rng(1).normal(size=(1000, 3))
    result = assess_sfm(_cameras(40), points, 80, {"median_reprojection_error": 1.2})
    assert result.ok
    assert result.metrics["registration_ratio"] == 0.5


def test_sfm_quality_rejects_stationary_rotation_and_low_registration():
    points = np.random.default_rng(2).normal(size=(1000, 3))
    assert not assess_sfm(_cameras(12), points, 100).ok
    stationary = [{"center": np.zeros(3)} for _ in range(40)]
    result = assess_sfm(stationary, points, 50)
    assert not result.ok
    assert "轨迹" in result.reason


def test_gaussian_quality_rejects_exploded_cube():
    rng = np.random.default_rng(3)
    source = rng.normal(0, 0.3, (1000, 3))
    exploded = rng.uniform(-5, 5, (1000, 3))
    result = assess_gaussians(exploded, source)
    assert not result.ok
    assert result.metrics["extent_ratio"] > 4


def test_metric_quality_rejects_twenty_meter_single_room_but_allows_relative():
    points = np.random.default_rng(4).uniform([0, 0, 0], [20, 18, 4], (2000, 3))
    assert not assess_metric_scene(points, calibrated=1).ok
    assert assess_metric_scene(points, calibrated=0).ok
