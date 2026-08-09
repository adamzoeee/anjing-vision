"""重建质量门控：坏模型不能进入米制测量和报告成功状态。"""
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


def assess_sfm(cameras: list[dict], points: np.ndarray, total_frames: int, quality: dict | None = None) -> QualityResult:
    """验证注册率、稀疏点数量、轨迹基线及 COLMAP 重投影误差。"""
    quality = quality or {}
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    registered = len(cameras)
    ratio = registered / max(total_frames, 1)
    centers = np.asarray([c["center"] for c in cameras], dtype=np.float64).reshape(-1, 3)
    trajectory_extent = _robust_extent(centers) if len(centers) >= 2 else np.zeros(3)
    trajectory_span = float(np.linalg.norm(trajectory_extent))
    reprojection = quality.get("median_reprojection_error")
    metrics = {
        "registered_images": registered,
        "registration_ratio": round(ratio, 4),
        "points3D": len(points),
        "trajectory_span_units": trajectory_span,
        "median_reprojection_error": reprojection,
    }
    if registered < 12 or ratio < 0.35:
        return QualityResult(False, "相机注册率过低，请沿房间缓慢移动并保持相邻画面充分重叠", metrics)
    if len(points) < 500:
        return QualityResult(False, "SFM 有效三维点过少，请增加墙角、家具等纹理区域的覆盖", metrics)
    if trajectory_span < 0.05:
        return QualityResult(False, "相机轨迹基线不足，请勿站在原地旋转，应沿房间边缘移动", metrics)
    if reprojection is not None and reprojection > 3.0:
        return QualityResult(False, "SFM 重投影误差过大，当前相机位姿不可靠", metrics)
    return QualityResult(True, None, metrics)


def assess_gaussians(means: np.ndarray, source_points: np.ndarray) -> QualityResult:
    """检测训练数值发散及相对初始 SFM 几何的异常膨胀。"""
    means = np.asarray(means, dtype=np.float64).reshape(-1, 3)
    source_points = np.asarray(source_points, dtype=np.float64).reshape(-1, 3)
    metrics = {"gaussian_count": len(means)}
    if len(means) < 100 or not np.isfinite(means).all():
        return QualityResult(False, "3DGS 训练产生无效数值", metrics)
    source_extent = _robust_extent(source_points)
    result_extent = _robust_extent(means)
    source_diag = float(np.linalg.norm(source_extent))
    result_diag = float(np.linalg.norm(result_extent))
    ratio = result_diag / max(source_diag, 1e-9)
    metrics.update({
        "source_extent": source_extent.tolist(),
        "gaussian_extent": result_extent.tolist(),
        "extent_ratio": ratio,
    })
    if source_diag < 1e-6 or ratio < 0.2 or ratio > 4.0:
        return QualityResult(False, "3DGS 几何相对 SFM 点云发生异常收缩或膨胀", metrics)
    return QualityResult(True, None, metrics)


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
