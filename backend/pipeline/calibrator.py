"""尺度标定：默认使用用户提供的 2～3 个已知物体尺寸恢复米制比例。

文件末尾保留的 A4/门高函数仅用于旧扫描兼容，不参与当前默认管道。
"""
import numpy as np

A4_SHORT = 0.210  # m
A4_LONG = 0.297   # m
DOOR_STANDARD_HEIGHT = 2.0  # m

REFERENCE_LABELS = {
    "door": "门",
    "bed": "床",
    "sofa": "沙发",
    "table": "桌子",
    "cabinet": "柜子",
}


def _object_extents(points: np.ndarray) -> np.ndarray | None:
    """以 PCA 主轴计算物体稳健包围盒，避免依赖 SFM 世界轴方向。"""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if len(points) < 20:
        return None
    center = np.median(points, axis=0)
    _, _, axes = np.linalg.svd(points - center, full_matrices=False)
    projected = (points - center) @ axes.T
    extents = np.percentile(projected, 95, axis=0) - np.percentile(projected, 5, axis=0)
    return np.sort(extents)[::-1]


def estimate_scale_from_references(
    points: np.ndarray,
    semantic_point_ids: dict[str, list[int]],
    measurements: list[dict],
) -> tuple[float | None, dict]:
    """由 2～3 个已知物体尺寸稳健估计米/模型单位比例。"""
    candidates = []
    details = []
    rank_by_type = {
        ("door", "height"): 0, ("door", "width"): 1,
        ("bed", "length"): 0, ("bed", "width"): 1, ("bed", "height"): 2,
        ("sofa", "length"): 0, ("sofa", "width"): 1, ("sofa", "height"): 2,
        ("table", "length"): 0, ("table", "width"): 1, ("table", "height"): 2,
        ("cabinet", "height"): 0, ("cabinet", "width"): 1, ("cabinet", "length"): 1,
    }
    for item in measurements:
        object_type = item.get("object_type")
        label = REFERENCE_LABELS.get(object_type)
        ids = semantic_point_ids.get(label, []) if label else []
        extents = _object_extents(points[np.asarray(ids, dtype=int)]) if ids else None
        rank = rank_by_type.get((object_type, item.get("dimension")))
        if extents is None or rank is None or rank >= len(extents) or extents[rank] <= 1e-6:
            details.append({**item, "status": "not_detected"})
            continue
        scale = float(item["meters"]) / float(extents[rank])
        if np.isfinite(scale) and 0.02 < scale < 20.0:
            candidates.append(scale)
            details.append({**item, "status": "used", "model_units": float(extents[rank]), "scale": scale})
    metrics = {"references": details, "used_count": len(candidates)}
    if len(candidates) < 2:
        metrics["reason"] = "至少需要两个被模型成功识别的参考尺寸"
        return None, metrics
    # 三个参考中一个分割边界不完整时，不应让单个离群值否决另外两个一致参考。
    # 寻找容差 25% 内的最大共识簇；仍要求至少两个参考达成一致。
    tolerance = 0.25
    best_indices: list[int] = []
    for center in candidates:
        indices = [
            index for index, value in enumerate(candidates)
            if abs(value - center) / max(center, 1e-9) <= tolerance
        ]
        if len(indices) > len(best_indices):
            best_indices = indices
    if len(best_indices) < 2:
        median = float(np.median(candidates))
        metrics.update({
            "scale": median,
            "max_relative_disagreement": float(
                max(abs(value - median) / median for value in candidates)
            ),
            "reason": "多个参考尺寸推导的比例不一致",
        })
        return None, metrics

    inliers = [candidates[index] for index in best_indices]
    scale = float(np.median(inliers))
    for candidate_index, detail in enumerate(details):
        if detail.get("status") != "used":
            continue
        used_index = sum(
            1 for previous in details[:candidate_index]
            if previous.get("status") == "used"
        )
        if used_index not in best_indices:
            detail["status"] = "outlier"
    relative_error = float(max(abs(value - scale) / scale for value in inliers))
    metrics.update({
        "scale": scale,
        "used_count": len(inliers),
        "candidate_count": len(candidates),
        "max_relative_disagreement": relative_error,
    })
    return scale, metrics


def compute_scale_from_pixel(pixel_len: float, physical_len: float, distance: float, focal: float) -> float:
    """由已知物理长度物体在成像中的像素长度，求尺度因子 (米/单位)。

    相机模型: pixel_len = focal * physical_len / depth_meters
    → depth_meters = focal * physical_len / pixel_len（物体到相机的真实深度）
    distance 为同一物体到相机的深度（SFM 单位）。返回 depth_meters / distance（米/单位）。
    """
    if pixel_len <= 0 or distance <= 0 or focal <= 0:
        raise ValueError("pixel_len/distance/focal 必须为正")
    depth_meters = focal * physical_len / pixel_len
    return depth_meters / distance


def scale_from_door_prior(door_height_units: float, standard_height: float = DOOR_STANDARD_HEIGHT) -> float:
    """门高先验兜底：标准门高 2.0m / 点云测得的门高(单位)。"""
    if door_height_units <= 0:
        raise ValueError("door_height_units 必须为正")
    return standard_height / door_height_units


def detect_a4_in_image(rgb: np.ndarray, mask: np.ndarray | None = None) -> tuple[float, float, float, float] | None:
    """在图片中检测 A4 纸（白纸矩形），返回 (长边像素, 短边像素, 中心x, 中心y)；找不到返回 None。

    实现：转灰度 → 自适应阈值 → 轮廓 → 多边形逼近找 4 点凸四边形 →
    按宽高比接近 sqrt(2) 且面积足够大者判为 A4，取其外接矩形长宽与中心（像素）。
    若传入 mask（SAM 分割出的"纸/文档"区域），仅在该区域内检测。
    """
    import cv2

    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    if mask is not None:
        gray = cv2.bitwise_and(gray, gray, mask=mask.astype(np.uint8) * 255)
    th = cv2.adaptiveThreshold(gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 31, 10)
    contours, _ = cv2.findContours(th, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    best = None
    for cnt in contours:
        peri = cv2.arcLength(cnt, True)
        approx = cv2.approxPolyDP(cnt, 0.02 * peri, True)
        if len(approx) not in (4, 5, 6):
            continue
        rect = cv2.minAreaRect(cnt)
        (cx, cy), (w, h), _ = rect
        if min(w, h) < 30:
            continue
        ratio = max(w, h) / min(w, h)
        if 1.2 < ratio < 1.8:  # A4 = sqrt(2) ≈ 1.414
            if best is None or (w * h) > best[0] * best[1]:
                best = (max(w, h), min(w, h), cx, cy)
    return best
