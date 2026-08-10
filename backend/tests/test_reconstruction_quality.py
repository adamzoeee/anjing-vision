import numpy as np

from pipeline.quality import assess_gaussians, assess_metric_scene, assess_sfm


def _cameras(count: int):
    return [{"center": np.array([index * 0.1, 0.0, 1.5])} for index in range(count)]


def test_sfm_quality_accepts_covered_room_sequence():
    points = np.random.default_rng(1).normal(size=(2000, 3))
    result = assess_sfm(_cameras(60), points, 80, {"median_reprojection_error": 1.2})
    assert result.ok
    assert result.metrics["registration_ratio"] == 0.75


def test_sfm_quality_rejects_stationary_rotation_and_low_registration():
    points = np.random.default_rng(2).normal(size=(2000, 3))
    assert not assess_sfm(_cameras(12), points, 100).ok
    stationary = [{"center": np.zeros(3)} for _ in range(40)]
    result = assess_sfm(stationary, points, 50)
    assert not result.ok
    assert "轨迹" in result.reason


def test_sfm_quality_rejects_partial_model_that_would_render_with_large_holes():
    points = np.random.default_rng(20).normal(size=(3000, 3))
    result = assess_sfm(_cameras(54), points, 93, {"median_reprojection_error": 1.0})
    assert not result.ok
    assert result.metrics["registration_ratio"] < 0.7
    assert "注册率" in result.reason


def test_sfm_quality_rejects_large_temporal_camera_jump():
    cameras = [
        {"name": f"frame_{index:05d}.jpg", "center": np.array([index * 0.1, 0.0, 1.5])}
        for index in range(60)
    ]
    cameras[40]["center"] = np.array([50.0, 0.0, 1.5])
    points = np.random.default_rng(21).normal(size=(3000, 3))
    result = assess_sfm(cameras, points, 70, {"median_reprojection_error": 0.8})
    assert not result.ok
    assert result.metrics["trajectory_jump_ratio"] > 30
    assert "断层" in result.reason


def test_sfm_quality_rejects_significant_disconnected_component():
    points = np.random.default_rng(4).normal(size=(3000, 3))
    result = assess_sfm(
        _cameras(80),
        points,
        100,
        {
            "median_reprojection_error": 0.7,
            "component_count": 2,
            "component_registered_images": [80, 15],
        },
    )
    assert not result.ok
    assert "多个独立三维片段" in result.reason


def test_gaussian_quality_rejects_exploded_cube():
    rng = np.random.default_rng(3)
    source = rng.normal(0, 0.3, (1000, 3))
    exploded = rng.uniform(-5, 5, (1000, 3))
    result = assess_gaussians(exploded, source)
    assert not result.ok
    assert result.metrics["extent_ratio"] > 4


def test_gaussian_quality_rejects_blurry_or_incomplete_multiview_model():
    rng = np.random.default_rng(30)
    source = rng.normal(0, 0.3, (1000, 3))
    assert not assess_gaussians(
        source.copy(), source, {"validation_psnr_mean": 15.9, "validation_alpha_coverage_min": 0.9}
    ).ok
    result = assess_gaussians(
        source.copy(), source, {"validation_psnr_mean": 22.0, "validation_alpha_coverage_min": 0.4}
    )
    assert not result.ok
    assert "覆盖" in result.reason


def test_gaussian_quality_accepts_clear_representative_views():
    source = np.random.default_rng(31).normal(0, 0.3, (1000, 3))
    result = assess_gaussians(
        source.copy(), source, {"validation_psnr_mean": 24.0, "validation_alpha_coverage_min": 0.8}
    )
    assert result.ok


def test_metric_quality_rejects_twenty_meter_single_room_but_allows_relative():
    points = np.random.default_rng(4).uniform([0, 0, 0], [20, 18, 4], (2000, 3))
    assert not assess_metric_scene(points, calibrated=1).ok
    assert assess_metric_scene(points, calibrated=0).ok
