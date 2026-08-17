"""米制场景质量门控（从已退役的 quality.py 中保留的最小函数）。

仅用于历史扫描兼容的参考物标定路径（calibrator 的兜底标定）。
"""
from dataclasses import dataclass

import numpy as np


@dataclass(frozen=True)
class QualityResult:
    ok: bool
    reason: str | None
    metrics: dict


def _robust_extent(points: np.ndarray) -> np.ndarray:
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    return np.percentile(points, 99, axis=0) - np.percentile(points, 1, axis=0)


def assess_metric_scene(points: np.ndarray, calibrated: int) -> QualityResult:
    """米制标定后检查单房间尺寸；未标定场景只检查几何退化。"""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    extent = _robust_extent(points)
    metrics = {"robust_extent": extent.tolist(), "calibrated": calibrated}
    positive = extent[extent > 1e-4]
    if len(positive) < 2:
        return QualityResult(False, "重建点云退化，无法形成房间三维结构", metrics)
    if calibrated and (float(extent.max()) > 15.0 or float(positive.min()) < 0.2):
        return QualityResult(False, "尺度标定结果超出单房间合理范围，请检查标定物识别或重新录制", metrics)
    return QualityResult(True, None, metrics)
