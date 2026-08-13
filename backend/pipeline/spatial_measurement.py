"""房间局部坐标系、语义实例清理与类别化三维尺寸。"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from scipy.spatial import cKDTree


@dataclass(frozen=True)
class RoomFrame:
    origin: np.ndarray
    axes: np.ndarray  # columns: room X, room Y, up Z
    ground_inlier_ratio: float
    confidence: str
    horizontal_method: str = "pca"


def _camera_up_hint(cameras: list[dict] | None) -> np.ndarray | None:
    """由多帧相机的图像向上方向估计重力反向；随机滚转会被中位数抑制。"""
    if not cameras:
        return None
    candidates = []
    for camera in cameras:
        rotation = np.asarray(camera.get("R"), dtype=np.float64)
        if rotation.shape == (3, 3):
            up = rotation.T @ np.array([0.0, -1.0, 0.0])
            length = np.linalg.norm(up)
            if np.isfinite(length) and length > 1e-6:
                candidates.append(up / length)
    if not candidates:
        return None
    reference = candidates[0]
    aligned = [vector if vector @ reference >= 0 else -vector for vector in candidates]
    hint = np.median(np.asarray(aligned), axis=0)
    return hint / max(np.linalg.norm(hint), 1e-9)


def estimate_room_frame(
    points: np.ndarray,
    cameras: list[dict] | None = None,
    *,
    seed: int = 42,
    iterations: int = 400,
) -> RoomFrame | None:
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    points = points[np.isfinite(points).all(axis=1)]
    if len(points) < 100:
        return None
    rng = np.random.default_rng(seed)
    diagonal = float(np.linalg.norm(np.percentile(points, 99, axis=0) - np.percentile(points, 1, axis=0)))
    threshold = max(diagonal * 0.012, 1e-4)
    up_hint = _camera_up_hint(cameras)
    best: tuple[np.ndarray, float, np.ndarray] | None = None
    for _ in range(iterations):
        sample = points[rng.choice(len(points), 3, replace=False)]
        normal = np.cross(sample[1] - sample[0], sample[2] - sample[0])
        length = np.linalg.norm(normal)
        if length < 1e-9:
            continue
        normal /= length
        offset = float(normal @ sample[0])
        distances = np.abs(points @ normal - offset)
        inliers = distances <= threshold
        # 房间主平面应有较大支撑；更偏好其一侧包含绝大多数点的边界平面。
        signed = points @ normal - offset
        sidedness = max(float(np.mean(signed >= -threshold)), float(np.mean(signed <= threshold)))
        alignment = abs(float(normal @ up_hint)) if up_hint is not None else 1.0
        # 有相机姿态时明显排斥竖墙；否则仍以边界平面支撑率为依据并降低置信度。
        score = float(inliers.mean()) * (0.5 + 0.5 * sidedness) * (0.15 + 0.85 * alignment**2)
        if best is None or score > best[1]:
            best = (normal.copy(), score, inliers)
    if best is None or float(best[2].mean()) < 0.04:
        return None
    normal, _score, inliers = best
    if up_hint is not None and abs(float(normal @ up_hint)) < 0.65:
        return None
    origin = np.median(points[inliers], axis=0)
    signed = (points - origin) @ normal
    if up_hint is not None and normal @ up_hint < 0:
        normal = -normal
    elif up_hint is None and np.percentile(signed, 80) < abs(np.percentile(signed, 20)):
        normal = -normal
    # 优先从竖墙恢复 Manhattan 水平轴。家具点云分布不均时，整体 PCA 会把房间
    # 长宽方向旋向床或柜子；竖墙法向则直接对应房间水平主方向。
    best_wall_normal = None
    best_wall_support = 0.0
    for _ in range(max(100, iterations // 2)):
        sample = points[rng.choice(len(points), 3, replace=False)]
        candidate = np.cross(sample[1] - sample[0], sample[2] - sample[0])
        length = np.linalg.norm(candidate)
        if length < 1e-9:
            continue
        candidate /= length
        if abs(float(candidate @ normal)) > 0.25:
            continue
        candidate -= normal * float(candidate @ normal)
        candidate /= max(np.linalg.norm(candidate), 1e-9)
        offset = float(candidate @ sample[0])
        support = float(np.mean(np.abs(points @ candidate - offset) <= threshold))
        if support > best_wall_support:
            best_wall_normal, best_wall_support = candidate, support
    horizontal_method = "manhattan_walls" if best_wall_normal is not None and best_wall_support >= 0.035 else "pca"
    if horizontal_method == "manhattan_walls":
        x_axis = best_wall_normal
    else:
        horizontal = points - origin - np.outer((points - origin) @ normal, normal)
        covariance = horizontal.T @ horizontal
        values, vectors = np.linalg.eigh(covariance)
        x_axis = vectors[:, int(np.argmax(values))]
    x_axis -= normal * float(x_axis @ normal)
    x_axis /= max(np.linalg.norm(x_axis), 1e-9)
    y_axis = np.cross(normal, x_axis)
    y_axis /= max(np.linalg.norm(y_axis), 1e-9)
    ratio = float(inliers.mean())
    confidence = "high" if up_hint is not None and ratio >= 0.12 and horizontal_method == "manhattan_walls" else "medium" if ratio >= 0.07 else "low"
    return RoomFrame(
        origin=origin,
        axes=np.stack([x_axis, y_axis, normal], axis=1),
        ground_inlier_ratio=ratio,
        confidence=confidence,
        horizontal_method=horizontal_method,
    )


def _clusters(points: np.ndarray, min_points: int = 20) -> list[np.ndarray]:
    if len(points) < min_points:
        return []
    tree = cKDTree(points)
    nearest = tree.query(points, k=min(4, len(points)))[0][:, -1]
    radius = max(float(np.median(nearest)) * 2.5, 1e-5)
    visited = np.zeros(len(points), dtype=bool)
    components: list[np.ndarray] = []
    for start in range(len(points)):
        if visited[start]:
            continue
        stack = [start]
        visited[start] = True
        component = []
        while stack:
            current = stack.pop()
            component.append(current)
            for neighbor in tree.query_ball_point(points[current], radius):
                if not visited[neighbor]:
                    visited[neighbor] = True
                    stack.append(neighbor)
        if len(component) >= min_points:
            components.append(np.asarray(component, dtype=int))
    return sorted(components, key=len, reverse=True)


def clean_object_points(points: np.ndarray, frame: RoomFrame | None) -> tuple[np.ndarray, dict]:
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    points = points[np.isfinite(points).all(axis=1)]
    if len(points) < 20:
        return np.empty((0, 3)), {"confidence": "low", "reason": "too_few_points"}
    lower, upper = np.percentile(points, [2, 98], axis=0)
    robust = points[np.all((points >= lower) & (points <= upper), axis=1)]
    if frame is not None:
        height = (robust - frame.origin) @ frame.axes[:, 2]
        # 去掉紧贴地面的投影噪声；保留床等低矮物体的主体。
        robust = robust[height > np.percentile(height, 2) - 1e-9]
    components = _clusters(robust)
    if not components:
        return np.empty((0, 3)), {"confidence": "low", "reason": "no_stable_cluster"}
    selected = robust[components[0]]
    retained = len(selected) / max(len(points), 1)
    confidence = "high" if len(selected) >= 150 and retained >= 0.45 else "medium" if len(selected) >= 50 else "low"
    return selected, {"confidence": confidence, "point_count": len(selected), "retained_ratio": retained, "cluster_count": len(components)}


def measure_object(points: np.ndarray, label: str, frame: RoomFrame | None) -> dict:
    cleaned, quality = clean_object_points(points, frame)
    if len(cleaned) < 20 or frame is None:
        return {"label": label, "status": "unknown", **quality}
    local = (cleaned - frame.origin) @ frame.axes
    vertical = float(np.percentile(local[:, 2], 97) - np.percentile(local[:, 2], 3))
    horizontal = local[:, :2] - np.median(local[:, :2], axis=0)
    _, _, axes = np.linalg.svd(horizontal, full_matrices=False)
    projected = horizontal @ axes.T
    horizontal_extents = np.percentile(projected, 97, axis=0) - np.percentile(projected, 3, axis=0)
    long_axis, short_axis = sorted(map(float, horizontal_extents), reverse=True)
    dimensions = {"length": long_axis, "width": short_axis, "height": vertical}
    if label == "门":
        dimensions = {"width": long_axis, "height": vertical, "thickness": short_axis}
    elif label in {"柜子", "书架"}:
        dimensions = {"length": long_axis, "width": short_axis, "height": vertical}
    elif label in {"床", "沙发", "桌子"}:
        dimensions = {"length": long_axis, "width": short_axis, "height": vertical}
    if min(dimensions.values()) <= 1e-5 or quality["confidence"] == "low":
        return {"label": label, "status": "unknown", **quality}
    return {"label": label, "status": "measured", "dimensions": dimensions, **quality}


def measure_semantic_objects(points: np.ndarray, semantic_point_ids: dict[str, list[int]], frame: RoomFrame | None) -> dict[str, dict]:
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    results = {}
    for label, ids in semantic_point_ids.items():
        valid = np.asarray([index for index in ids if 0 <= int(index) < len(points)], dtype=int)
        results[label] = measure_object(points[valid], label, frame) if len(valid) else {"label": label, "status": "unknown", "confidence": "low", "reason": "no_points"}
    return results


def measure_room(points: np.ndarray, frame: RoomFrame | None) -> dict:
    """在房间局部坐标系中输出稳健长/宽/高；坐标系不可靠时拒绝给数字。"""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if frame is None or frame.confidence == "low" or len(points) < 100:
        return {"status": "unknown", "confidence": "low", "reason": "room_frame_unreliable"}
    local = (points - frame.origin) @ frame.axes
    extents = np.percentile(local, 98, axis=0) - np.percentile(local, 2, axis=0)
    horizontal = sorted(map(float, extents[:2]), reverse=True)
    if min(*horizontal, float(extents[2])) <= 1e-5:
        return {"status": "unknown", "confidence": "low", "reason": "degenerate_extent"}
    return {
        "status": "measured",
        "confidence": frame.confidence,
        "dimensions": {"length": horizontal[0], "width": horizontal[1], "height": float(extents[2])},
    }


def evaluate_dimension_accuracy(
    predictions: dict[str, dict],
    ground_truth: list[dict],
    calibration_references: list[dict],
) -> dict:
    """仅用未参与尺度标定的卷尺真值验收自动尺寸，避免数据泄漏。"""
    reference_keys = {
        (str(item.get("object_type")), str(item.get("dimension")))
        for item in calibration_references
    }
    label_by_type = {
        "door": "门", "bed": "床", "sofa": "沙发", "table": "桌子",
        "cabinet": "柜子", "bookshelf": "书架",
    }
    comparisons = []
    for truth in ground_truth:
        key = (str(truth.get("object_type")), str(truth.get("dimension")))
        if key in reference_keys:
            continue
        label = label_by_type.get(key[0])
        prediction = predictions.get(label or "", {})
        predicted = prediction.get("dimensions", {}).get(key[1])
        actual = truth.get("meters")
        if predicted is None or actual is None or float(actual) <= 0:
            comparisons.append({**truth, "status": "unknown"})
            continue
        absolute = abs(float(predicted) - float(actual))
        comparisons.append({
            **truth,
            "status": "compared",
            "predicted_m": float(predicted),
            "absolute_error_m": absolute,
            "relative_error": absolute / float(actual),
        })
    compared = [item for item in comparisons if item["status"] == "compared"]
    return {
        "comparisons": comparisons,
        "compared_count": len(compared),
        "unknown_count": len(comparisons) - len(compared),
        "mean_relative_error": float(np.mean([item["relative_error"] for item in compared])) if compared else None,
        "max_relative_error": float(np.max([item["relative_error"] for item in compared])) if compared else None,
    }
