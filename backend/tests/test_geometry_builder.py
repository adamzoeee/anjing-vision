import numpy as np
import pytest

from pipeline.geometry_builder import fit_instance_geometry


def _instance(status="stable", label="table"):
    return {
        "instance_id": f"{label}_001", "normalized_label": label,
        "semantic_label": "桌子", "status": status,
        "geometry_confidence": 0.9, "support_views": 4,
    }


def _structure():
    return {
        "room": {"bounds_xy": {"min": [-3.0, -3.0], "max": [3.0, 3.0]}},
        "walls": [{"id": 0}, {"id": 1}, {"id": 2}, {"id": 3}],
    }


def _alignment():
    return {"alignment": {"wall_theta_deg": 3.0}}


def _rotated_box_points(length, width, z_bottom, z_top, yaw_deg, count, rng):
    local = np.column_stack([
        rng.uniform(-length / 2, length / 2, count),
        rng.uniform(-width / 2, width / 2, count),
        rng.uniform(z_bottom, z_top, count),
    ])
    angle = np.deg2rad(yaw_deg)
    rotation = np.array([[np.cos(angle), -np.sin(angle)], [np.sin(angle), np.cos(angle)]])
    local[:, :2] = local[:, :2] @ rotation.T
    return local


def test_small_number_of_z_outliers_does_not_inflate_height():
    rng = np.random.default_rng(41)
    points = _rotated_box_points(1.2, 0.6, 0.1, 0.75, 15.0, 1500, rng)
    outliers = np.array([[0.0, 0.0, 8.0], [0.1, 0.1, -4.0], [-0.1, 0.0, 6.0]])

    fitted, diagnostic = fit_instance_geometry(
        np.vstack([points, outliers]), _instance(), _structure(), _alignment(),
    )

    assert fitted["geometry_status"] == "verified"
    assert fitted["dimensions"]["height"] == pytest.approx(0.65, abs=0.08)
    assert diagnostic["z_range"]["z_top"] < 1.0


def test_wall_sheet_does_not_pull_furniture_rectangle():
    rng = np.random.default_rng(42)
    furniture = _rotated_box_points(1.0, 0.5, 0.1, 1.2, 0.0, 1000, rng)
    wall = np.column_stack([
        rng.normal(3.0, 0.008, 700), rng.uniform(-2.0, 2.0, 700), rng.uniform(0.1, 2.5, 700),
    ])

    fitted, diagnostic = fit_instance_geometry(
        np.vstack([furniture, wall]), _instance(label="cabinet"), _structure(), _alignment(),
    )

    assert fitted["geometry_status"] == "verified"
    assert fitted["dimensions"]["length"] == pytest.approx(1.0, abs=0.12)
    assert fitted["dimensions"]["width"] == pytest.approx(0.5, abs=0.1)
    assert diagnostic["filter_breakdown"]["wall_removed"] >= 680


def test_rotated_furniture_keeps_data_driven_yaw_when_not_near_wall():
    rng = np.random.default_rng(43)
    points = _rotated_box_points(1.8, 0.65, 0.1, 0.8, 32.0, 1800, rng)

    fitted, diagnostic = fit_instance_geometry(
        points, _instance(label="desk"), _structure(), _alignment(),
    )

    assert fitted["geometry_status"] == "verified"
    assert abs(abs(fitted["rotation_z_deg"]) - 32.0) < 5.0
    assert diagnostic["wall_axis_snap"]["applied"] is False


def test_low_confidence_instance_never_gets_bbox_or_dimensions():
    rng = np.random.default_rng(44)
    points = _rotated_box_points(2.0, 1.5, 0.1, 0.6, 0.0, 1000, rng)

    fitted, diagnostic = fit_instance_geometry(
        points, _instance(status="low_confidence", label="bed"), _structure(), _alignment(),
    )

    assert fitted["bbox"] is None
    assert fitted["dimensions"] is None
    assert fitted["geometry_status"] == "low_confidence"
    assert diagnostic["reason"] == "instance_not_stable"

