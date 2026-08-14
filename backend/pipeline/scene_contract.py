"""重建器与语义/空间测量之间的统一场景数据契约。"""
from __future__ import annotations

from enum import Enum
from typing import Any, TypedDict


class MetricScaleStatus(str, Enum):
    metric_apriltag = "metric_apriltag"
    relative = "relative"
    calibration_failed = "calibration_failed"


class CalibrationMetadata(TypedDict, total=False):
    status: str
    coordinate_unit: str
    family: str | None
    tag_size_m: float | None
    scale_factor: float | None
    scale_applied_by: str | None


class SceneGeometry(TypedDict, total=False):
    images: Any
    cameras: list[dict]
    points3D: Any
    colors3D: Any
    gaussian: dict
    splat_ply: Any
    coordinate_unit: str
    metric_scale_status: str
    metric_calibration: CalibrationMetadata
    reconstruction_quality: dict


def validate_metric_calibration(metadata: CalibrationMetadata) -> CalibrationMetadata:
    """拒绝“看似米制但实际未缩放”的场景，阻止下游误报 m/cm。"""
    try:
        status = MetricScaleStatus(metadata.get("status", "relative"))
    except ValueError as exc:
        raise ValueError("未知的场景尺度状态") from exc
    unit = metadata.get("coordinate_unit")
    if status is MetricScaleStatus.metric_apriltag:
        if unit != "meters" or metadata.get("scale_applied_by") != "vid2scene":
            raise ValueError("AprilTag 米制状态缺少 vid2scene 缩放凭据")
        factor = metadata.get("scale_factor")
        if factor is None or float(factor) <= 0:
            raise ValueError("AprilTag 米制状态缺少有效比例因子")
    elif status in {MetricScaleStatus.relative, MetricScaleStatus.calibration_failed}:
        if unit != "model_units":
            raise ValueError("未标定场景必须使用 model_units")
    return metadata
