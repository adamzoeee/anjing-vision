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
    ordered_cameras = sorted(cameras, key=lambda item: item.get("name", ""))
    centers = np.asarray([c["center"] for c in ordered_cameras], dtype=np.float64).reshape(-1, 3)
    trajectory_extent = _robust_extent(centers) if len(centers) >= 2 else np.zeros(3)
    trajectory_span = float(np.linalg.norm(trajectory_extent))
    reprojection = quality.get("median_reprojection_error")
    consecutive = np.linalg.norm(np.diff(centers, axis=0), axis=1) if len(centers) >= 2 else np.array([])
    median_step = float(np.median(consecutive)) if len(consecutive) else 0.0
    max_step = float(np.max(consecutive)) if len(consecutive) else 0.0
    jump_ratio = max_step / max(median_step, 1e-9)
    metrics = {
        "registered_images": registered,
        "registration_ratio": round(ratio, 4),
        "points3D": len(points),
        "trajectory_span_units": trajectory_span,
        "median_reprojection_error": reprojection,
        "trajectory_median_step_units": median_step,
        "trajectory_max_step_units": max_step,
        "trajectory_jump_ratio": jump_ratio,
        "component_count": int(quality.get("component_count", 1)),
        "component_registered_images": quality.get("component_registered_images", [registered]),
    }
    if registered < 20 or ratio < 0.70:
        return QualityResult(False, "相机注册率过低，请沿房间缓慢移动并保持相邻画面充分重叠", metrics)
    component_sizes = list(quality.get("component_registered_images") or [registered])
    if len(component_sizes) > 1 and component_sizes[1] >= max(10, int(np.ceil(total_frames * 0.10))):
        return QualityResult(
            False,
            "相机轨迹被拆成多个独立三维片段，无法形成统一房间；请避免快速转身并保证转角前后持续重叠",
            metrics,
        )
    if len(points) < 1500:
        return QualityResult(False, "SFM 有效三维点过少，请增加墙角、家具等纹理区域的覆盖", metrics)
    if trajectory_span < 0.05:
        return QualityResult(False, "相机轨迹基线不足，请勿站在原地旋转，应沿房间边缘移动", metrics)
    if jump_ratio > 30.0:
        return QualityResult(False, "相机轨迹存在严重断层，请避免快速转身或漏拍并保持连续重叠", metrics)
    if reprojection is not None and reprojection > 2.0:
        return QualityResult(False, "SFM 重投影误差过大，当前相机位姿不可靠", metrics)
    return QualityResult(True, None, metrics)


def assess_gaussians(
    means: np.ndarray,
    source_points: np.ndarray,
    training_metrics: dict | None = None,
) -> QualityResult:
    """检测训练数值发散及相对初始 SFM 几何的异常膨胀。"""
    means = np.asarray(means, dtype=np.float64).reshape(-1, 3)
    source_points = np.asarray(source_points, dtype=np.float64).reshape(-1, 3)
    metrics = {"gaussian_count": len(means), **(training_metrics or {})}
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
    psnr_mean = metrics.get("validation_psnr_mean")
    coverage = metrics.get("validation_alpha_coverage_min")
    if psnr_mean is not None and float(psnr_mean) < 16.0:
        return QualityResult(False, "3DGS 多视角清晰度不足，请检查拍摄重叠并重新录制", metrics)
    if coverage is not None and float(coverage) < 0.65:
        return QualityResult(False, "3DGS 部分视角覆盖严重缺失，请沿房间边缘补充拍摄", metrics)
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
