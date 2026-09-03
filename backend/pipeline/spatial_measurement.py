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
    floor_plane: np.ndarray | None = None  # (a,b,c,d)，ax+by+cz+d=0，法向为单位向量
    wall_normals: tuple = ()  # 归一化墙面法向（世界坐标）
    ground_support: float = 0.0  # 地面内点水平散布 / 场景对角线


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


def _cluster_directions(
    normals: list[np.ndarray], supports: list[float], *, direction_dot: float = 0.9
) -> list[tuple[np.ndarray, float]]:
    """按方向聚类候选平面法向；返回 (代表法向, 最大支撑) 按支撑降序。

    同一面墙的 RANSAC 候选会同时出现 n 与 -n，用 |dot| 合并为同一方向。
    """
    clusters: list[list[int]] = []
    for index, normal in enumerate(normals):
        merged = False
        for group in clusters:
            if abs(float(normal @ normals[group[0]])) >= direction_dot:
                group.append(index)
                merged = True
                break
        if not merged:
            clusters.append([index])
    representatives = []
    for group in clusters:
        best_index = max(group, key=lambda index: supports[index])
        representatives.append((normals[best_index], float(supports[best_index])))
    return sorted(representatives, key=lambda item: -item[1])


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
        # 最低带约束：地面应位于点云主质量的下方；位于点云中部的平面
        # （桌面、床面）必须被显著惩罚，避免误当地面。
        median_projection = float(np.median(signed + offset))
        lowest_band = 1.0 if offset <= median_projection else 0.15
        # 有相机姿态时明显排斥竖墙；否则仍以边界平面支撑率为依据并降低置信度。
        score = float(inliers.mean()) * (0.5 + 0.5 * sidedness) * (0.15 + 0.85 * alignment**2) * lowest_band
        if best is None or score > best[1]:
            best = (normal.copy(), score, inliers)
    if best is None or float(best[2].mean()) < (0.10 if up_hint is None else 0.04):
        # 无相机姿态提示时只能依赖几何本身，要求更高的平面支撑，
        # 避免把家具内部平面误判为地面。
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
    # 收集多条墙候选并聚类：两条近似正交的墙 → manhattan；仅一条 → single_wall。
    wall_candidates: list[np.ndarray] = []
    wall_supports: list[float] = []
    for _ in range(max(200, iterations)):
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
        if support >= 0.03:
            wall_candidates.append(candidate)
            wall_supports.append(support)
    wall_directions = _cluster_directions(wall_candidates, wall_supports)
    wall_normals: tuple = ()
    horizontal_method = "pca"
    if wall_directions:
        primary = wall_directions[0][0]
        orthogonal = next(
            (direction for direction, _support in wall_directions[1:]
             if abs(float(direction @ primary)) < 0.5),
            None,
        )
        x_axis = primary
        if orthogonal is not None:
            horizontal_method = "manhattan_walls"
            wall_normals = (primary, orthogonal)
        else:
            horizontal_method = "single_wall"
            wall_normals = (primary,)
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
    # 地面内点水平散布：过低说明“地面”只是小平面（桌面/床面）。
    ground_points = points[inliers]
    ground_spread = np.array([
        float(np.percentile((ground_points - origin) @ x_axis, 90)
              - np.percentile((ground_points - origin) @ x_axis, 10)),
        float(np.percentile((ground_points - origin) @ y_axis, 90)
              - np.percentile((ground_points - origin) @ y_axis, 10)),
    ])
    ground_support = float(np.min(ground_spread) / max(diagonal, 1e-9))
    confidence = (
        "high"
        if up_hint is not None and ratio >= 0.12 and horizontal_method == "manhattan_walls"
        else "medium"
        if ratio >= 0.07 or (up_hint is not None and horizontal_method == "single_wall")
        else "low"
    )
    if ground_support < 0.08:
        # “地面”水平覆盖过小，大概率把桌面/床面当地面；最高只给 medium。
        confidence = "low" if confidence == "high" else confidence
    floor_plane = np.array([normal[0], normal[1], normal[2], -float(normal @ origin)])
    return RoomFrame(
        origin=origin,
        axes=np.stack([x_axis, y_axis, normal], axis=1),
        ground_inlier_ratio=ratio,
        confidence=confidence,
        horizontal_method=horizontal_method,
        floor_plane=floor_plane,
        wall_normals=wall_normals,
        ground_support=ground_support,
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
    """在房间局部坐标系中输出稳健长/宽/高；坐标系不可靠时拒绝给数字。

    优先：地面点水平范围 → 长/宽；非地面点（墙/家具/天花板）垂直范围 → 高。
    地面或墙面支撑不足时退回全点云稳健范围，并降级 confidence、在 metadata
    中说明测量方法。
    """
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if frame is None or frame.confidence == "low" or len(points) < 100:
        return {"status": "unknown", "confidence": "low", "reason": "room_frame_unreliable"}
    local = (points - frame.origin) @ frame.axes
    heights = local[:, 2]
    diagonal = float(np.linalg.norm(np.percentile(points, 99, axis=0) - np.percentile(points, 1, axis=0)))
    floor_band = max(0.05, diagonal * 0.006)
    floor_mask = np.abs(heights) <= floor_band
    floor_points = local[floor_mask]
    above_points = local[~floor_mask]
    metadata = {
        "floor_point_count": int(floor_mask.sum()),
        "above_floor_point_count": int(len(above_points)),
        "floor_band": round(floor_band, 4),
    }
    if len(floor_points) >= 100:
        length = float(np.percentile(floor_points[:, 0], 98) - np.percentile(floor_points[:, 0], 2))
        width = float(np.percentile(floor_points[:, 1], 98) - np.percentile(floor_points[:, 1], 2))
        length, width = sorted([length, width], reverse=True)
        if len(above_points) >= 50:
            height = float(np.percentile(above_points[:, 2], 97))
            method = "floor_and_vertical_structure"
            confidence = "high" if frame.confidence == "high" else "medium"
        else:
            height = float(np.percentile(local[:, 2], 97) - np.percentile(local[:, 2], 2))
            method = "floor_only"
            confidence = "medium"
        metadata["method"] = method
    else:
        # 地面覆盖不足（重建缺失/地面被家具遮挡）：退回稳健全点云范围。
        extents = np.percentile(local, 98, axis=0) - np.percentile(local, 2, axis=0)
        horizontal = sorted(map(float, extents[:2]), reverse=True)
        length, width = horizontal[0], horizontal[1]
        height = float(extents[2])
        metadata["method"] = "scene_extent_fallback"
        confidence = "low" if frame.confidence != "high" else "medium"
    horizontal = [length, width]
    if min(*horizontal, height) <= 1e-5:
        return {"status": "unknown", "confidence": "low", "reason": "degenerate_extent", **metadata}
    return {
        "status": "measured",
        "confidence": confidence,
        "dimensions": {"length": horizontal[0], "width": horizontal[1], "height": height},
        **metadata,
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


# ---------------------------------------------------------------------------
# 第二阶段：3D 语义实例分离、稳健几何与统一物体数据结构
# ---------------------------------------------------------------------------

INSTANCE_PREFIXES = {
    "床": "bed", "柜子": "cabinet", "书架": "bookshelf", "桌子": "table",
    "沙发": "sofa", "椅子": "chair", "门": "door", "纸箱": "box",
    "收纳箱": "box", "杂物": "clutter", "盆栽": "plant", "宠物": "pet",
    "水桶": "bucket", "行李箱": "suitcase",
}
# 尺寸命名在各类别中的语义（下一阶段通道分析直接按此解释）。
DIMENSION_ROLES = {
    "床": {"length": "沿床头床尾方向", "width": "床宽", "height": "床面以上垂直范围"},
    "沙发": {"length": "沿靠背方向", "width": "坐深方向", "height": "坐面以上垂直范围"},
    "桌子": {"length": "长边", "width": "短边", "height": "桌面垂直范围"},
    "椅子": {"length": "水平长边", "width": "水平短边", "height": "椅面以上垂直范围"},
    "柜子": {"length": "宽度（沿墙）", "width": "深度", "height": "柜体高度"},
    "书架": {"length": "宽度（沿墙）", "width": "深度", "height": "架体高度"},
    "门": {"length": None, "width": "门洞净宽", "height": "门洞净高"},
    "纸箱": {"length": "水平长边", "width": "水平短边", "height": "箱体高度"},
    "收纳箱": {"length": "水平长边", "width": "水平短边", "height": "箱体高度"},
    "杂物": {"length": "水平长边", "width": "水平短边", "height": "垂直范围"},
    "盆栽": {"length": "水平长边", "width": "水平短边", "height": "垂直范围"},
}
_CONFIDENCE_ORDER = {"high": 2, "medium": 1, "low": 0, "unknown": -1}
_METRIC_SCALE_STATUSES = {"metric_apriltag", "metric_references"}


def _worst_confidence(*values: str) -> str:
    """置信度取各项中的最差档。"""
    return min(values, key=lambda value: _CONFIDENCE_ORDER.get(value, -1))


def _dense_clusters(
    points: np.ndarray, min_points: int = 20, core_quantile: float = 0.25, radius_factor: float = 3.0
) -> list[np.ndarray]:
    """按“致密核心”半径连通聚类：半径取最近邻距离低分位的数倍。

    相比全局中位 NN，低分位半径只把致密结构连成簇；弥漫离群点
    要么被切碎成小碎片（被 min_points 丢弃），要么作为单独大簇
    交给主簇致密性守卫判断。
    """
    if len(points) < min_points:
        return []
    tree = cKDTree(points)
    if len(points) >= 2:
        nearest = tree.query(points, k=min(5, len(points)))[0][:, -1]
        core = float(np.percentile(nearest, core_quantile * 100.0))
        radius = max(core * radius_factor, 1e-5)
    else:
        radius = 1e-5
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


def cluster_semantic_instances(
    points: np.ndarray, indices: list[int], *, min_points: int = 20
) -> list[np.ndarray]:
    """同类别语义点按致密连通聚类，形成候选 3D 实例。"""
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    ids = np.asarray([int(index) for index in indices if 0 <= int(index) < len(points)], dtype=int)
    if len(ids) < min_points:
        return []
    components = _dense_clusters(points[ids], min_points=min_points)
    return [ids[component] for component in components]


def _label_detection_hits(
    points: np.ndarray,
    flat_ids: np.ndarray,
    view_records: list[dict],
    label: str,
    *,
    depth_tolerance: float = 0.03,
) -> list[tuple[int, int, set[int]]]:
    """返回 [(view_index, detection_index, 命中点本地索引集合)]。

    命中索引是 ``flat_ids`` 内的位置（0..len-1），供簇合并使用。
    """
    from .semantic import _nearest_surface_filter, project_points_to_view

    hits = []
    for view_index, record in enumerate(view_records):
        camera = record.get("camera")
        image_shape = record.get("image_shape")
        if camera is None or not image_shape:
            continue
        shape = tuple(int(value) for value in image_shape)
        uv, depth, valid = project_points_to_view(points[flat_ids], camera, image_shape=shape)
        valid_ids = np.where(valid)[0]
        height, width = shape
        pixel_x = np.zeros(len(valid), dtype=int)
        pixel_y = np.zeros(len(valid), dtype=int)
        # rint 会把边界像素舍入到图像尺寸外，必须 clamp。
        pixel_x[valid_ids] = np.clip(np.rint(uv[valid_ids, 0]).astype(int), 0, width - 1)
        pixel_y[valid_ids] = np.clip(np.rint(uv[valid_ids, 1]).astype(int), 0, height - 1)
        for detection_index, detection in enumerate(record.get("detections") or []):
            if str(detection.get("label", "")) != label:
                continue
            mask = np.asarray(detection.get("mask"), dtype=bool)
            if mask.shape != shape or not bool(np.any(mask)):
                continue
            inside = valid & mask[pixel_y, pixel_x]
            candidates = valid_ids[inside[valid_ids]]
            if len(candidates) == 0:
                continue
            surfaced = _nearest_surface_filter(
                candidates,
                pixel_x[candidates],
                pixel_y[candidates],
                depth[candidates],
                shape,
                depth_tolerance=depth_tolerance,
            )
            hits.append((view_index, detection_index, set(int(i) for i in surfaced)))
    return hits


def merge_fragmented_clusters(
    points: np.ndarray,
    clusters: list[np.ndarray],
    view_records: list[dict],
    label: str,
    *,
    hit_ratio: float = 0.25,
    min_hits: int = 4,
) -> list[np.ndarray]:
    """多视角 mask 关联：同一 2D 实例 mask 同时命中的空间断裂簇合并。

    遮挡可能把同一柜子切成两段 3D 簇；若某个视角的同一 SAM mask 同时显著
    命中两簇，它们属于同一物体，合并。空间上确实分离的两个柜子不会共享
    同一 2D 实例，因此保持独立。
    """
    if len(clusters) <= 1:
        return clusters
    flat_ids = np.concatenate(clusters)
    local_sets: list[set[int]] = []
    offset = 0
    for cluster in clusters:
        local_sets.append(set(range(offset, offset + len(cluster))))
        offset += len(cluster)
    parent = list(range(len(clusters)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        root_left, root_right = find(left), find(right)
        if root_left != root_right:
            parent[root_left] = root_right

    for _view_index, _detection_index, hit_set in _label_detection_hits(
        points, flat_ids, view_records, label
    ):
        members = [
            index
            for index, local in enumerate(local_sets)
            if len(local & hit_set) >= max(min_hits, int(hit_ratio * len(local)))
        ]
        if len(members) >= 2:
            for other in members[1:]:
                union(members[0], other)
    groups: dict[int, list[int]] = {}
    for index in range(len(clusters)):
        groups.setdefault(find(index), []).append(index)
    merged = [
        np.asarray(np.concatenate([flat_ids[list(local_sets[i])] for i in group]), dtype=int)
        for group in groups.values()
    ]
    return sorted(merged, key=len, reverse=True)


def _instance_geometry(
    points: np.ndarray, ids: np.ndarray, label: str, frame: RoomFrame | None,
    scene_nn: float | None = None,
) -> dict:
    """实例级稳健几何：分位裁剪 → 主簇 → 水平 OBB + 垂直范围。

    ``scene_nn`` 为全场景点云的低分位近邻距离（重建分辨率参考）；
    实例密度显著低于该分辨率时视为弥漫离群簇，拒绝测量。
    返回几何中间结果与 geometry_confidence；数值位于模型单位（未缩放）。
    """
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    sub = points[ids]
    empty = {
        "status": "insufficient",
        "reason": "too_few_points",
        "point_count": int(len(sub)),
        "cleaned_count": 0,
        "retained_ratio": 0.0,
        "cluster_count": 0,
        "obb_stability": None,
        "center_3d": None,
        "axes_3d": None,
        "obb_corners": None,
        "footprint": None,
        "dimensions": None,
        "confidence": "unknown",
    }
    if len(sub) < 20:
        return empty
    # 预裁剪只负责剔除极端离群点（0.5..99.5%），避免双重重分位造成大偏差。
    lower, upper = np.percentile(sub, [0.5, 99.5], axis=0)
    robust = sub[np.all((sub >= lower) & (sub <= upper), axis=1)]
    if frame is not None:
        height = (robust - frame.origin) @ frame.axes[:, 2]
        # 只剔除紧贴地面的投影噪声（0.5% 分位），避免吃掉柜体/门框的正常底部。
        robust = robust[height > np.percentile(height, 0.5) - 1e-9]
    components = _clusters(robust)
    if not components:
        return {**empty, "reason": "no_stable_cluster", "point_count": int(len(sub))}
    selected = robust[components[0]]
    retained = len(selected) / max(len(sub), 1)
    # 主簇疑似弥漫离群点（范围≈全体）：若存在明显更致密的次簇则改选次簇，
    # 否则拒绝测量——大量离群点绝不硬算尺寸。
    if len(components) > 1:
        overall_extent = float(np.linalg.norm(
            np.percentile(robust, 98, axis=0) - np.percentile(robust, 2, axis=0)
        ))
        cluster_extent = float(np.linalg.norm(
            np.percentile(selected, 98, axis=0) - np.percentile(selected, 2, axis=0)
        ))
        if cluster_extent / max(overall_extent, 1e-9) > 0.85:
            tree = cKDTree(selected)
            dominant_density = float(np.median(tree.query(selected, k=min(4, len(selected)))[0][:, -1]))
            rescued = None
            for other in components[1:]:
                other_points = robust[other]
                if len(other_points) < max(20, int(0.05 * len(selected))):
                    continue
                if float(np.linalg.norm(
                    np.percentile(other_points, 98, axis=0) - np.percentile(other_points, 2, axis=0)
                )) >= 0.5 * cluster_extent:
                    continue
                other_tree = cKDTree(other_points)
                other_density = float(np.median(
                    other_tree.query(other_points, k=min(4, len(other_points)))[0][:, -1]
                ))
                if other_density < dominant_density / 3.0:
                    rescued = other_points
                    break
            if rescued is None:
                return {**empty, "reason": "no_compact_cluster", "point_count": int(len(sub))}
            selected = rescued
            retained = len(selected) / max(len(sub), 1)
    if scene_nn is not None and scene_nn > 0:
        selected_tree = cKDTree(selected)
        selected_nn = float(np.median(
            selected_tree.query(selected, k=min(4, len(selected)))[0][:, -1]
        ))
        if selected_nn > 2.5 * scene_nn:
            # 实例点密度远低于重建分辨率：弥漫离群簇，绝不硬算尺寸。
            return {**empty, "reason": "diffuse_cluster", "point_count": int(len(sub))}
    if frame is None:
        return {**empty, "reason": "room_frame_unavailable", "point_count": int(len(sub))}
    local = (selected - frame.origin) @ frame.axes
    vertical_extent = float(np.percentile(local[:, 2], 99.5) - np.percentile(local[:, 2], 0.5))
    top_height = float(max(np.percentile(local[:, 2], 90), 0.0))
    top_height_high = float(max(np.percentile(local[:, 2], 99), 0.0))
    horizontal = local[:, :2] - np.median(local[:, :2], axis=0)
    _, _, axes_2d = np.linalg.svd(horizontal, full_matrices=False)
    projected = horizontal @ axes_2d.T
    extents_97 = np.percentile(projected, 99, axis=0) - np.percentile(projected, 1, axis=0)
    extents_90 = np.percentile(projected, 90, axis=0) - np.percentile(projected, 10, axis=0)
    stability = float(np.mean(np.abs(extents_97 - extents_90) / np.maximum(extents_97, 1e-9)))
    if min(float(extents_97.min()), vertical_extent) <= 1e-5:
        return {**empty, "reason": "degenerate_extent", "point_count": int(len(sub))}
    order = np.argsort(extents_97)[::-1]
    axes_2d = axes_2d[order]
    extents = extents_97[order]
    up = frame.axes[:, 2]
    long_axis = frame.axes[:, :2] @ axes_2d[0]
    long_axis /= max(np.linalg.norm(long_axis), 1e-9)
    # SVD 主轴符号随机：固定长轴指向物体中心偏移一侧，短轴由叉积保证
    # 右手系，避免 footprint/OBB 相对 center 镜像。
    horizontal_offset = (np.median(selected, axis=0) - frame.origin) @ frame.axes[:, :2]
    if float(axes_2d[0] @ horizontal_offset) < 0:
        long_axis = -long_axis
    short_axis = np.cross(up, long_axis)
    short_axis /= max(np.linalg.norm(short_axis), 1e-9)
    center = np.median(selected, axis=0)
    horizontal_center = (center - frame.origin) @ frame.axes[:, :2]
    center_ground = (
        frame.origin
        + horizontal_center[0] * frame.axes[:, 0]
        + horizontal_center[1] * frame.axes[:, 1]
    )
    footprint_corners = np.asarray([
        center_ground
        + sign_x * extents[0] / 2 * long_axis
        + sign_y * extents[1] / 2 * short_axis
        for sign_x in (-1.0, 1.0)
        for sign_y in (-1.0, 1.0)
    ])
    z_center = float(np.median(local[:, 2]))
    obb_corners = np.asarray([
        center_ground
        + sign_x * extents[0] / 2 * long_axis
        + sign_y * extents[1] / 2 * short_axis
        + (z_center + sign_z * vertical_extent / 2) * up
        for sign_x in (-1.0, 1.0)
        for sign_y in (-1.0, 1.0)
        for sign_z in (-1.0, 1.0)
    ])
    axes_3d = np.stack([long_axis, short_axis, up], axis=1)
    if len(selected) >= 150 and retained >= 0.45 and stability <= 0.20:
        confidence = "high"
    elif len(selected) >= 50 and retained >= 0.25:
        confidence = "medium"
    else:
        confidence = "low"
    return {
        "status": "ok",
        "reason": None,
        "point_count": int(len(sub)),
        "cleaned_count": int(len(selected)),
        "retained_ratio": float(retained),
        "cluster_count": len(components),
        "obb_stability": float(stability),
        "center_3d": center,
        "axes_3d": axes_3d,
        "obb_corners": obb_corners,
        "footprint": {
            "corners": footprint_corners,
            "area": float(extents[0] * extents[1]),
        },
        "dimensions": {
            "length": float(extents[0]),
            "width": float(extents[1]),
            "height": vertical_extent,
            "top_height": top_height,
            "top_height_high": top_height_high,
        },
        "confidence": confidence,
    }


def _door_opening(
    points: np.ndarray, ids: np.ndarray, frame: RoomFrame, geometry: dict
) -> dict:
    """门洞宽高：优先沿门面方向对上部点做直方图双峰（左右门框柱）检测。

    门框柱比门板/过梁密度更高，沿水平长轴投影后形成两个主峰；
    两峰间距即门洞净宽，顶部范围即门洞净高。双峰不可靠时退回
    水平 OBB 并标记 fallback（测量置信度相应降级）。
    """
    fallback = {
        "method": "horizontal_obb_fallback",
        "confidence_penalty": True,
        "jamb_column_count": None,
    }
    local = (points[np.asarray(ids, dtype=int)] - frame.origin) @ frame.axes
    if len(local) < 20 or geometry.get("dimensions") is None:
        return {**fallback, "width": None, "height": None}
    height_range = float(geometry["dimensions"]["height"])  # 稳健垂直范围
    if height_range <= 1e-5:
        return {**fallback, "width": None, "height": None}
    long_axis_world = geometry["axes_3d"][:, 0]
    long_axis_room = frame.axes[:, :2].T @ long_axis_world
    long_axis_room /= max(np.linalg.norm(long_axis_room), 1e-9)
    upper = local[local[:, 2] >= 0.5 * float(np.percentile(local[:, 2], 97))]
    if len(upper) >= 30:
        projection = upper[:, :2] @ long_axis_room
        counts, edges = np.histogram(projection, bins=48)
        centers = (edges[:-1] + edges[1:]) / 2.0
        # 门框柱密度远高于门板/过梁背景；0.35×峰值只保留真正的柱状主峰。
        peak_mask = counts >= 0.35 * float(counts.max())
        groups: list[list[int]] = []
        current: list[int] = []
        for index, is_peak in enumerate(peak_mask):
            if is_peak:
                current.append(index)
            elif current:
                groups.append(current)
                current = []
        if current:
            groups.append(current)
        if len(groups) >= 2:
            peak_centers = [
                float(np.sum(counts[group] * centers[group]) / max(np.sum(counts[group]), 1e-9))
                for group in groups
            ]
            width = float(max(peak_centers) - min(peak_centers))
            if 0.3 * height_range <= width <= 2.0 * height_range:
                return {
                    "method": "door_jamb_columns",
                    "confidence_penalty": False,
                    "jamb_column_count": len(groups),
                    "width": width,
                    "height": height_range,
                }
    geometry_dims = geometry.get("dimensions") or {}
    return {
        **fallback,
        "width": geometry_dims.get("length"),
        "height": geometry_dims.get("height"),
    }


def _semantic_confidence(score: float, consistency: float, supporting_views: int) -> str:
    if score >= 0.6 and consistency >= 0.75 and supporting_views >= 3:
        return "high"
    if score >= 0.4 and supporting_views >= 2:
        return "medium"
    return "low"


def _instance_dimensions(
    label: str,
    geometry: dict | None,
    opening: dict | None,
) -> tuple[dict, str | None]:
    """生成统一尺寸字典（模型单位数值；是否对外以米制呈现由 status 决定）。"""
    unknown = {"length_m": None, "width_m": None, "height_m": None}
    if geometry is None or geometry.get("dimensions") is None:
        return unknown, geometry.get("reason") if geometry else "too_few_points"
    dims = geometry["dimensions"]
    if label == "门" and opening is not None and opening.get("width") is not None:
        return {
            "length_m": None,
            "width_m": float(opening["width"]),
            "height_m": float(opening["height"]),
        }, None
    # 表面类物体（床/桌/椅）高度取顶面距地高度（p90）；
    # 沙发含靠背，取高位分位顶高（p99）；柜体/书架取垂直范围。
    if label in {"床", "桌子", "椅子", "盆栽"}:
        height = float(dims.get("top_height", dims["height"]))
    elif label == "沙发":
        height = float(dims.get("top_height_high", dims.get("top_height", dims["height"])))
    else:
        height = float(dims["height"])
    return {
        "length_m": float(dims["length"]),
        "width_m": float(dims["width"]),
        "height_m": height,
    }, None


@dataclass(frozen=True)
class SemanticObject3D:
    """统一的 3D 语义物体实例。下一阶段通道/门宽/家具间距直接消费。"""

    instance_id: str
    label: str
    point_indices: np.ndarray
    center_3d: np.ndarray | None
    axes_3d: np.ndarray | None
    obb_corners: np.ndarray | None
    footprint: dict | None
    dimensions: dict
    supporting_views: int
    visible_views: int
    semantic_confidence: str
    geometry_confidence: str
    measurement_confidence: str
    status: str
    reason: str | None
    metadata: dict

    def to_dict(self) -> dict:
        def _list(value):
            return None if value is None else np.asarray(value).tolist()

        footprint = None
        if self.footprint is not None:
            footprint = {
                "corners": _list(self.footprint.get("corners")),
                "area": self.footprint.get("area"),
            }
        return {
            "instance_id": self.instance_id,
            "label": self.label,
            "status": self.status,
            "reason": self.reason,
            "point_count": int(len(self.point_indices)),
            "center_3d": _list(self.center_3d),
            "axes_3d": _list(self.axes_3d),
            "obb_corners": _list(self.obb_corners),
            "footprint": footprint,
            "dimensions": dict(self.dimensions),
            "supporting_views": self.supporting_views,
            "visible_views": self.visible_views,
            "semantic_confidence": self.semantic_confidence,
            "geometry_confidence": self.geometry_confidence,
            "measurement_confidence": self.measurement_confidence,
            "metadata": dict(self.metadata),
        }


def build_semantic_objects(
    points: np.ndarray,
    fusion,
    view_records: list[dict],
    frame: RoomFrame | None,
    *,
    unit: str = "meters",
    metric_scale_status: str = "metric_apriltag",
    min_instance_points: int = 20,
) -> tuple[list[SemanticObject3D], dict]:
    """融合结果 → 独立 3D 实例 → 稳健几何与真实尺寸。

    返回 (实例列表, 统计信息)。无米制尺度时实例语义照常输出，
    但所有 dimensions 为 unknown。
    """
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    # 单凭调用方传入 unit="meters" 不能证明场景已经完成尺度恢复。
    # 必须同时具有明确的米制标定状态，避免 relative/calibration_failed
    # 场景因参数误用而输出伪造米制尺寸。
    metric_available = unit == "meters" and metric_scale_status in _METRIC_SCALE_STATUSES
    # 全场景致密参考密度：重建分辨率（5% 分位近邻距离），供弥漫簇守卫使用。
    scene_nn: float | None = None
    if len(points) >= 50:
        scene_tree = cKDTree(points)
        scene_nn = float(np.percentile(
            scene_tree.query(points, k=min(5, len(points)))[0][:, -1], 5
        ))
    objects: list[SemanticObject3D] = []
    statistics: dict = {
        "labeled_point_count": len(fusion.point_labels),
        "metric_available": metric_available,
        "per_label": {},
    }
    labels = sorted({label for label in fusion.point_labels.values()})
    for label in labels:
        ids = np.asarray(fusion.label_point_ids(label), dtype=int)
        clusters = cluster_semantic_instances(points, ids.tolist(), min_points=min_instance_points)
        if not clusters:
            if len(ids) >= 5:
                # 点数不足聚类门槛：仍保留为“证据不足”实例，供明确报 unknown。
                clusters = [ids]
            else:
                statistics["per_label"][label] = {"instance_count": 0, "cluster_count": 0}
                continue
        clusters = merge_fragmented_clusters(points, clusters, view_records, label)
        prefix = INSTANCE_PREFIXES.get(label, "obj")
        label_objects: list[SemanticObject3D] = []
        for cluster in sorted(clusters, key=len, reverse=True):
            supports = [
                int(fusion.supporting_views.get(int(pid), {}).get(label, 0)) for pid in cluster
            ]
            scores = [float(fusion.semantic_score.get(int(pid), 0.0)) for pid in cluster]
            consistencies = [float(fusion.consistency.get(int(pid), 0.0)) for pid in cluster]
            visibles = [
                int(fusion.visible_views[int(pid)]) if int(pid) < len(fusion.visible_views) else 0
                for pid in cluster
            ]
            supporting_views = int(np.median(supports)) if supports else 0
            visible_views = int(np.median(visibles)) if visibles else 0
            semantic_score = float(np.mean(scores)) if scores else 0.0
            consistency = float(np.mean(consistencies)) if consistencies else 0.0
            semantic_confidence = _semantic_confidence(semantic_score, consistency, supporting_views)
            geometry = _instance_geometry(points, cluster, label, frame, scene_nn=scene_nn)
            geometry_confidence = geometry["confidence"]
            opening = None
            if label == "门" and geometry["status"] == "ok" and frame is not None:
                opening = _door_opening(points, cluster, frame, geometry)
            frame_confidence = frame.confidence if frame is not None else "unknown"
            metric_confidence = "high" if metric_available else "low"
            measurement_confidence = _worst_confidence(
                semantic_confidence, geometry_confidence, frame_confidence, metric_confidence
            )
            unknown_dims = {"length_m": None, "width_m": None, "height_m": None}
            if frame is None:
                dimensions, reason = unknown_dims, "room_frame_unavailable"
            elif frame.confidence == "low":
                dimensions, reason = unknown_dims, "room_frame_unreliable"
            elif geometry["status"] != "ok":
                dimensions, reason = unknown_dims, geometry["reason"]
            elif geometry_confidence == "low":
                dimensions, reason = unknown_dims, "insufficient_geometry_evidence"
            elif semantic_confidence == "low":
                dimensions, reason = unknown_dims, "low_semantic_support"
            else:
                dimensions, reason = _instance_dimensions(label, geometry, opening)
            if reason is not None:
                status = "unknown"
            elif not metric_available:
                status = "unknown"
                reason = "metric_scale_unavailable"
            else:
                status = "measured" if any(
                    value is not None for value in dimensions.values()
                ) else "unknown"
            metadata = {
                "semantic_score": round(semantic_score, 4),
                "vote_consistency": round(consistency, 4),
                "detection_view_count": int(supporting_views),
                "cleaned_point_count": int(geometry.get("cleaned_count") or 0),
                "retained_ratio": round(float(geometry.get("retained_ratio") or 0.0), 4),
                "cluster_count": int(geometry.get("cluster_count") or 0),
                "obb_stability": round(float(geometry["obb_stability"]), 4)
                if geometry.get("obb_stability") is not None
                else None,
                "dimension_roles": DIMENSION_ROLES.get(label, {}),
            }
            if label == "门":
                metadata["door_measurement"] = {
                    "method": opening.get("method") if opening else None,
                    "jamb_column_count": opening.get("jamb_column_count") if opening else None,
                    "estimated_opening_width_m": opening.get("width") if opening else None,
                    "estimated_opening_height_m": opening.get("height") if opening else None,
                    "fallback_used": bool(opening.get("confidence_penalty")) if opening else None,
                }
            label_objects.append(
                SemanticObject3D(
                    instance_id=f"{prefix}_{len(label_objects) + 1:02d}",
                    label=label,
                    point_indices=cluster,
                    center_3d=geometry.get("center_3d"),
                    axes_3d=geometry.get("axes_3d"),
                    obb_corners=geometry.get("obb_corners"),
                    footprint=geometry.get("footprint"),
                    dimensions=dimensions,
                    supporting_views=int(supporting_views),
                    visible_views=int(visible_views),
                    semantic_confidence=semantic_confidence,
                    geometry_confidence=geometry_confidence,
                    measurement_confidence=measurement_confidence,
                    status=status,
                    reason=reason,
                    metadata=metadata,
                )
            )
        objects.extend(label_objects)
        statistics["per_label"][label] = {
            "instance_count": len(label_objects),
            "cluster_count": len(clusters),
        }
    statistics["object_count"] = len(objects)
    statistics["measured_count"] = sum(1 for obj in objects if obj.status == "measured")
    return objects, statistics


def room_frame_to_dict(frame: RoomFrame | None) -> dict | None:
    if frame is None:
        return None

    def _list(value):
        return None if value is None else np.asarray(value).tolist()

    return {
        "origin": _list(frame.origin),
        "axes": _list(frame.axes),
        "ground_inlier_ratio": frame.ground_inlier_ratio,
        "confidence": frame.confidence,
        "horizontal_method": frame.horizontal_method,
        "floor_plane": _list(frame.floor_plane),
        "wall_normals": [_list(normal) for normal in frame.wall_normals],
        "ground_support": frame.ground_support,
    }


def room_dimensions_for_space(
    points: np.ndarray, frame: RoomFrame | None, *, unit: str = "meters"
) -> dict:
    """语义空间输出用的房间尺寸；无米制尺度时返回 unknown。"""
    metric_available = unit == "meters"
    measured = measure_room(points, frame)
    unknown = {"length_m": None, "width_m": None, "height_m": None}
    if metric_available and measured.get("status") == "measured":
        dims = measured["dimensions"]
        return {
            "status": "measured",
            "confidence": measured["confidence"],
            "unit": "meters",
            "dimensions": {
                "length_m": float(dims["length"]),
                "width_m": float(dims["width"]),
                "height_m": float(dims["height"]),
            },
            "metadata": {
                key: value
                for key, value in measured.items()
                if key not in {"status", "confidence", "dimensions"}
            },
        }
    return {
        "status": "unknown",
        "confidence": measured.get("confidence", "low"),
        "unit": "model_units",
        "dimensions": unknown,
        "reason": "metric_scale_unavailable" if not metric_available else measured.get("reason"),
        "metadata": {
            key: value
            for key, value in measured.items()
            if key not in {"status", "confidence", "dimensions", "reason"}
        },
    }


def rescale_semantic_space(space: dict, scale: float, *, unit: str = "meters") -> dict:
    """把模型单位下的语义空间整体缩放为米制（位置/尺寸同步缩放）。

    只用于旧兼容标定分支把最终尺度写回；AprilTag 主线的 scale 恒为 1。
    第二阶段自身不估计 scale，本函数也不做任何标定判断。
    """
    import copy

    scale = float(scale)
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("scale 必须为正有限值")
    if space.get("metric_available") or space.get("unit") == "meters":
        raise ValueError("语义空间已经是米制，禁止重复应用 scale")
    result = copy.deepcopy(space)
    for obj in result.get("objects", []):
        for key in ("center_3d",):
            if obj.get(key):
                obj[key] = [float(value) * scale for value in obj[key]]
        if obj.get("obb_corners"):
            obj["obb_corners"] = [
                [float(value) * scale for value in corner] for corner in obj["obb_corners"]
            ]
        footprint = obj.get("footprint")
        if footprint:
            if footprint.get("corners"):
                footprint["corners"] = [
                    [float(value) * scale for value in corner] for corner in footprint["corners"]
                ]
            if footprint.get("area") is not None:
                footprint["area"] = float(footprint["area"]) * scale * scale
        dims = obj.get("dimensions") or {}
        for key, value in dims.items():
            if value is not None:
                dims[key] = float(value) * scale
        if unit == "meters" and obj.get("status") != "measured":
            # 仅在“尺度已确认”时把模型单位数值正式转为米制测量。
            if obj.get("reason") == "metric_scale_unavailable" and any(
                value is not None for value in dims.values()
            ):
                obj["status"] = "measured"
                obj["reason"] = None
                obj["measurement_confidence"] = _worst_confidence(
                    obj.get("semantic_confidence", "low"),
                    obj.get("geometry_confidence", "low"),
                    "high",
                )
        door_meta = (obj.get("metadata") or {}).get("door_measurement")
        if door_meta:
            for key in ("estimated_opening_width_m", "estimated_opening_height_m"):
                if door_meta.get(key) is not None:
                    door_meta[key] = float(door_meta[key]) * scale
    room_frame = result.get("room_frame")
    if room_frame:
        if room_frame.get("origin"):
            room_frame["origin"] = [float(value) * scale for value in room_frame["origin"]]
        if room_frame.get("floor_plane"):
            plane = room_frame["floor_plane"]
            room_frame["floor_plane"] = [plane[0], plane[1], plane[2], float(plane[3]) * scale]
    room_dims = result.get("room_dimensions") or {}
    if room_dims.get("dimensions"):
        for key, value in room_dims["dimensions"].items():
            if value is not None:
                room_dims["dimensions"][key] = float(value) * scale
    result["unit"] = unit
    result["metric_available"] = unit == "meters"
    statistics = result.get("statistics")
    if isinstance(statistics, dict):
        statistics["metric_available"] = unit == "meters"
    return result


def _object_relations(objects: list[SemanticObject3D], *, top_k: int = 2) -> dict:
    """物体间位置关系：中心距离 + 地面 footprint 近似间隙（升序取最近 top_k）。

    下一阶段通道/家具间距分析可直接消费，无需重新计算两两距离。
    """
    relations: dict[str, list[dict]] = {}
    positioned = [
        obj for obj in objects
        if obj.status == "measured" and obj.center_3d is not None
    ]
    for obj in positioned:
        entries = []
        for other in positioned:
            if other.instance_id == obj.instance_id:
                continue
            center_distance = float(np.linalg.norm(
                np.asarray(obj.center_3d) - np.asarray(other.center_3d)
            ))
            footprint_gap = None
            if (
                obj.footprint and other.footprint
                and obj.footprint.get("corners") is not None
                and other.footprint.get("corners") is not None
            ):
                own = np.asarray(obj.footprint["corners"])
                others = np.asarray(other.footprint["corners"])
                footprint_gap = float(np.min(np.linalg.norm(
                    own[:, None, :] - others[None, :, :], axis=2
                )))
            entries.append({
                "instance_id": other.instance_id,
                "label": other.label,
                "center_distance": round(center_distance, 4),
                "footprint_gap": round(footprint_gap, 4) if footprint_gap is not None else None,
            })
        relations[obj.instance_id] = sorted(
            entries, key=lambda item: item["center_distance"]
        )[:top_k]
    return relations


def build_semantic_space(
    points: np.ndarray,
    fusion,
    view_records: list[dict],
    frame: RoomFrame | None,
    *,
    unit: str = "meters",
    metric_scale_status: str = "metric_apriltag",
) -> dict:
    """第二阶段统一输出：语义 3D 空间（实例 + 房间坐标系 + 房间尺寸）。"""
    metric_available = unit == "meters" and metric_scale_status in _METRIC_SCALE_STATUSES
    effective_unit = "meters" if metric_available else "model_units"
    objects, statistics = build_semantic_objects(
        points,
        fusion,
        view_records,
        frame,
        unit=effective_unit,
        metric_scale_status=metric_scale_status,
    )
    return {
        "unit": effective_unit,
        "metric_scale_status": metric_scale_status,
        "metric_available": metric_available,
        "room_frame": room_frame_to_dict(frame),
        "room_dimensions": room_dimensions_for_space(points, frame, unit=effective_unit),
        "objects": [obj.to_dict() for obj in objects],
        "object_relations": _object_relations(objects),
        "object_count": len(objects),
        "statistics": statistics,
        "fusion": fusion.diagnostics if hasattr(fusion, "diagnostics") else {},
    }
