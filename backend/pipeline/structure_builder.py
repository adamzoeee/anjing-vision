"""从米制对齐点云和 SpatialLM 候选生成稳定的 2.5D 房间结构契约。"""
from __future__ import annotations

import json
import math
import hashlib
import logging
from pathlib import Path

import numpy as np
import open3d as o3d

logger = logging.getLogger("anjing.pipeline.structure")


LARGE_OBJECTS = {
    "bed", "sofa", "wardrobe", "cabinet", "bookshelf", "desk", "table",
    "dining_table", "coffee_table", "nightstand", "tv_stand",
}
SMALL_OBJECTS = {"stool", "chair", "bin", "trash_bin", "box", "small_table", "lamp", "suitcase"}
DISPLAY_OBJECTS = LARGE_OBJECTS | SMALL_OBJECTS
LABEL_ALIASES = {
    "multifunctional_combination_bed": "bed", "combination_sofa": "sofa",
    "dining_chair": "chair", "bar_chair": "chair",
    "coffee_table": "small_table", "side_table": "small_table", "dining_table": "table",
    "tv_cabinet": "cabinet", "sideboard": "cabinet", "cupboard": "cabinet",
    "bookcase": "bookshelf",
}
# 每个类别的合理性先验：(高度下限, 高度上限, 水平长边下限)。拟合结果落在
# 范围外即拒——防止 SpatialLM 把墙面/吊柜/椅子误当床和柜子等系统性错误。
LABEL_PLAUSIBILITY: dict[str, tuple[float, float, float]] = {
    "bed": (0.12, 0.85, 1.1),
    "sofa": (0.15, 1.1, 0.8),
    "desk": (0.10, 1.1, 0.25),
    "table": (0.10, 1.1, 0.25),
    "dining_table": (0.30, 1.2, 0.4),
    "small_table": (0.15, 0.8, 0.15),
    "coffee_table": (0.15, 0.8, 0.15),
    "nightstand": (0.20, 1.0, 0.2),
    "wardrobe": (0.6, 2.8, 0.3),
    "cabinet": (0.3, 2.8, 0.2),
    "bookshelf": (0.30, 2.8, 0.3),
    "tv_stand": (0.2, 0.9, 0.3),
    "chair": (0.25, 1.2, 0.15),
    "stool": (0.10, 0.8, 0.15),
}


def _load_json(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _room_bounds(points: np.ndarray, alignment: dict) -> tuple[np.ndarray, np.ndarray, float]:
    ext = alignment.get("extents_m") or {}
    lo = np.array([
        (ext.get("x") or [np.percentile(points[:, 0], 1)])[0],
        (ext.get("y") or [np.percentile(points[:, 1], 1)])[0],
    ], dtype=float)
    hi = np.array([
        (ext.get("x") or [0, np.percentile(points[:, 0], 99)])[1],
        (ext.get("y") or [0, np.percentile(points[:, 1], 99)])[1],
    ], dtype=float)
    z = ext.get("z") or [np.percentile(points[:, 2], 1), np.percentile(points[:, 2], 99)]
    height = float(np.clip(z[1] - max(0.0, z[0]), 1.8, 4.5))
    return lo, hi, height


def _walls(lo: np.ndarray, hi: np.ndarray, height: float, thickness: float = 0.06) -> list[dict]:
    cx, cy = (lo + hi) / 2
    sx, sy = hi - lo
    return [
        {"id": 0, "center": [cx, lo[1], height / 2], "size": [sx, thickness, height], "rotation_z_deg": 0.0},
        {"id": 1, "center": [hi[0], cy, height / 2], "size": [sy, thickness, height], "rotation_z_deg": 90.0},
        {"id": 2, "center": [cx, hi[1], height / 2], "size": [sx, thickness, height], "rotation_z_deg": 0.0},
        {"id": 3, "center": [lo[0], cy, height / 2], "size": [sy, thickness, height], "rotation_z_deg": 90.0},
    ]


def _opening_wall_score(
    points: np.ndarray, wall_id: int, center: np.ndarray, width: float, opening_height: float,
    kind: str, lo: np.ndarray, hi: np.ndarray,
) -> tuple[float, float, int]:
    """用墙面局部“开口稀疏、周边墙体密集”证据判断门窗属于哪面墙。"""
    wall_coord = (lo[1], hi[0], hi[1], lo[0])[wall_id]
    vertical_wall = wall_id in (1, 3)
    along = points[:, 1] if vertical_wall else points[:, 0]
    normal = points[:, 0] if vertical_wall else points[:, 1]
    candidate_along = center[1] if vertical_wall else center[0]
    near_plane = np.abs(normal - wall_coord) < 0.12
    if kind == "door":
        z0, z1 = 0.03, min(opening_height, 2.4)
    else:
        z0 = max(0.35, center[2] - opening_height / 2)
        z1 = center[2] + opening_height / 2
    opening = near_plane & (np.abs(along - candidate_along) <= width / 2) & (points[:, 2] >= z0) & (points[:, 2] <= z1)
    surround = near_plane & (np.abs(along - candidate_along) <= width / 2 + 0.28) & ~opening & (points[:, 2] >= 0.03) & (points[:, 2] <= min(z1 + 0.2, 3.2))
    opening_area = max(width * max(z1 - z0, 0.2), 0.05)
    surround_area = max((width + 0.56) * min(z1 + 0.2, 3.2) - opening_area, 0.05)
    opening_density = float(opening.sum()) / opening_area
    surround_density = float(surround.sum()) / surround_area
    band = near_plane & (np.abs(along - candidate_along) <= width / 2)
    below = band & (points[:, 2] >= 0.03) & (points[:, 2] < max(z0 - 0.05, 0.04))
    above = band & (points[:, 2] > z1 + 0.05) & (points[:, 2] <= min(z1 + 0.45, 3.2))
    below_height = max(z0 - 0.08, 0.05)
    above_height = max(min(z1 + 0.45, 3.2) - z1 - 0.05, 0.05)
    below_density = float(below.sum()) / max(width * below_height, 0.05)
    above_density = float(above.sum()) / max(width * above_height, 0.05)
    # 分数越大越像真实开口。周边没有墙点时不允许仅凭“这里很空”通过。
    evidence = surround_density / (opening_density + 20.0)
    if kind == "door":
        # 门洞必须从地面开始；底部若仍有密集墙点，明显不像门。
        evidence *= 1.0 / (1.0 + below_density / 40.0)
    else:
        # 窗必须同时有窗台以下墙体和窗顶以上墙体。
        frame_support = min(below_density, above_density)
        evidence *= float(np.clip(frame_support / 80.0, 0.0, 1.0))
    return evidence, surround_density, int(opening.sum())


def _map_source_walls(source_walls: list[dict], shell_walls: list[dict]) -> dict[int, int]:
    """把 SpatialLM 原始墙 id 映射到清理后的房间壳墙 id。"""
    mapping: dict[int, int] = {}
    for source in source_walls:
        source_center = np.asarray(source.get("center", [0, 0, 0]), dtype=float)
        source_angle = float(source.get("rotation_z_deg", 0.0)) % 180
        best = min(
            shell_walls,
            key=lambda wall: (
                np.linalg.norm(source_center[:2] - np.asarray(wall["center"][:2]))
                + 0.02 * min(abs(source_angle - wall["rotation_z_deg"]), 180 - abs(source_angle - wall["rotation_z_deg"]))
            ),
        )
        mapping[int(source.get("id", len(mapping)))] = int(best["id"])
    return mapping


def _snap_opening(
    item: dict, walls: list[dict], lo: np.ndarray, hi: np.ndarray, height: float,
    points: np.ndarray, wall_mapping: dict[int, int],
) -> dict:
    center = np.asarray(item.get("center", [0, 0, 0]), dtype=float)
    width = float(np.clip(item.get("size", [0.8, 0.1, 2.0])[0], 0.35, 2.5))
    opening_height = float(np.clip(item.get("size", [0.8, 0.1, 2.0])[2], 0.35, height))
    kind = str(item.get("kind", "opening"))
    distances = [abs(center[1] - lo[1]), abs(center[0] - hi[0]), abs(center[1] - hi[1]), abs(center[0] - lo[0])]
    nearest_wall = int(np.argmin(distances))
    scored = [(_opening_wall_score(points, wall_id, center, width, opening_height, kind, lo, hi), wall_id) for wall_id in range(4)]
    source_wall_id = item.get("wall_id")
    preferred_wall = wall_mapping.get(int(source_wall_id)) if source_wall_id is not None else None
    if kind == "window" and preferred_wall is not None:
        # 窗常被窗帘填满，几何空洞评分不稳定；原始“属于哪面墙”的拓扑关系更可靠。
        wall_id = preferred_wall
        evidence, surround_density, opening_points = scored[wall_id][0]
    else:
        # 门通常有从地面开始的明显开口，可以在四面墙中靠几何证据纠正错误候选位置。
        (evidence, surround_density, opening_points), wall_id = max(scored, key=lambda pair: pair[0][0])
    wall = walls[wall_id]
    if wall_id in (0, 2):
        center[0] = np.clip(center[0], lo[0] + width / 2, hi[0] - width / 2)
        center[1] = wall["center"][1]
    else:
        center[1] = np.clip(center[1], lo[1] + width / 2, hi[1] - width / 2)
        center[0] = wall["center"][0]
    center[2] = float(np.clip(center[2], opening_height / 2, height - opening_height / 2))
    strict_verified = surround_density >= 30 and evidence >= 1.75
    # 窗常被窗帘/玻璃反射填满，无法表现为纯几何空洞。若 SpatialLM 窗候选本身
    # 紧贴合理墙面且尺寸/高度正常，则以 semantic_supported 中置信度保留。
    semantic_window = (
        kind == "window" and preferred_wall is not None and distances[preferred_wall] <= 0.45
        and 0.45 <= width <= 2.5 and 0.45 <= opening_height <= 2.0
        and 0.35 <= center[2] - opening_height / 2
        and center[2] + opening_height / 2 <= height + 0.15
        and surround_density >= 10
    )
    semantic_door = (
        kind == "door" and preferred_wall is not None
        and distances[preferred_wall] <= 0.45
        and 0.50 <= width <= 1.25 and 1.50 <= opening_height <= min(height, 2.40)
    )
    status = (
        "verified" if strict_verified
        else "semantic_supported" if semantic_window or semantic_door
        else "rejected"
    )
    return {
        "kind": kind, "center": center.tolist(),
        "size": [width, 0.10, opening_height], "rotation_z_deg": wall["rotation_z_deg"],
        "wall_id": wall_id, "source_wall_id": source_wall_id, "geometry_status": status,
        "geometry_confidence": float(max(0.55 if (semantic_window or semantic_door) else 0.0, np.clip(evidence / 5.0, 0.0, 1.0))),
        "verification_method": (
            "pointcloud_opening" if strict_verified
            else "layout_wall_prior" if semantic_door
            else "semantic_wall_prior" if semantic_window
            else "insufficient_evidence"
        ),
        "opening_points": opening_points,
    }


def _candidate_points(points: np.ndarray, item: dict) -> np.ndarray:
    center = np.asarray(item["center"], dtype=float)
    size = np.asarray(item["size"], dtype=float)
    theta = math.radians(float(item.get("rotation_z_deg", 0.0)))
    delta = points - center
    local = np.empty_like(delta)
    local[:, 0] = delta[:, 0] * math.cos(theta) + delta[:, 1] * math.sin(theta)
    local[:, 1] = -delta[:, 0] * math.sin(theta) + delta[:, 1] * math.cos(theta)
    local[:, 2] = delta[:, 2]
    margin = np.array([0.12, 0.12, 0.08])
    return points[np.all(np.abs(local) <= size / 2 + margin, axis=1)]


def _remove_wall_sheet(points: np.ndarray, lo: np.ndarray, hi: np.ndarray,
                       tol: float = 0.06, cell: float = 0.08, z_span: float = 0.8) -> np.ndarray:
    """剔除候选裁剪盒内“整面墙”的点，但保留贴墙物体的边缘。

    长度识别最隐蔽的错误源之一：SpatialLM 的家具候选盒经常把背后的墙包进去
    （书桌/床头贴墙时尤其明显）。墙面点稠密且连片，DBSCAN 会把墙当成主簇，
    盒子随之被拉成“墙的尺寸”。
    判定按网格逐格做：贴墙点所在格若在竖直方向连成 >z_span 高的薄片，才是墙；
    这样书桌背边、床头板等贴墙物体边缘（竖直跨度小）不会被误删。
    """
    keep = np.ones(len(points), dtype=bool)
    for coord_idx, wall_val in ((0, lo[0]), (0, hi[0]), (1, lo[1]), (1, hi[1])):
        near = np.abs(points[:, coord_idx] - wall_val) <= tol
        if int(near.sum()) < 30:
            continue
        other_idx = 1 - coord_idx
        keys = np.floor(points[near, other_idx] / cell).astype(np.int64)
        near_indices = np.flatnonzero(near)
        for key in np.unique(keys):
            in_cell = keys == key
            zs = points[near][in_cell, 2]
            if zs.max() - zs.min() > z_span:
                keep[near_indices[in_cell]] = False
    if int(keep.sum()) < max(20, int(len(points) * 0.25)):
        return points  # 剔除过狠说明候选本身贴墙/点太少，回退原样
    return points[keep]


def _remove_wall_strips(points: np.ndarray, lo: np.ndarray, hi: np.ndarray, *,
                        max_depth: float = 0.38, max_z: float = 0.55,
                        min_length: float = 1.0, min_z_span: float = 0.12,
                        cell: float = 0.08) -> np.ndarray:
    """剔除贴墙低矮长条物（矮柜/长凳/窗台类），防止它们混进床/桌等平放物体的簇。

    这是“床被拉成对角”的根因修正：贴墙长矮柜与床面在空间上相连、高度相近，
    DBSCAN 会把两者并成一个簇。规则：距墙 0.03~max_depth、z≤max_z、沿墙方向
    连通的低矮条带（长度≥min_length、有 z 厚度）视为独立长条物剔除。
    床头板/床尾板的竖直跨度与该规则不同（床头板更矮更贴墙的部分会被剔除，
    但损失 <5cm 级，由贴墙吸附与视频修正补回）；房间中间的物体不受影响。
    """
    keep = np.ones(len(points), dtype=bool)
    for coord_idx, wall_val in ((0, lo[0]), (0, hi[0]), (1, lo[1]), (1, hi[1])):
        inward = 1.0 if wall_val == lo[coord_idx] else -1.0
        depth = (points[:, coord_idx] - wall_val) * inward
        band = (depth >= 0.03) & (depth <= max_depth) & (points[:, 2] <= max_z)
        if int(band.sum()) < 60:
            continue
        along_idx = 1 - coord_idx
        # 关键判别 1：该沿墙格上方若还有更高点（柜体延续向上），不是“低矮长条”。
        all_keys = np.floor(points[:, along_idx] / cell).astype(np.int64)
        cell_zmax: dict[int, float] = {}
        for key in np.unique(all_keys):
            cell_zmax[int(key)] = float(points[all_keys == key, 2].max())
        keys = np.floor(points[band, along_idx] / cell).astype(np.int64)
        band_indices = np.flatnonzero(band)
        groups: dict[int, np.ndarray] = {}
        for key in np.unique(keys):
            if cell_zmax.get(int(key), 0.0) > max_z + 0.05:
                continue  # 高柜底座，不是低矮长条
            in_key = keys == key
            zs = points[band][in_key, 2]
            if zs.max() - zs.min() < min_z_span:
                continue  # 单层薄片（地板）不是“柜”
            # 关键判别 2：必须“坐在地上”（z 起于地面附近），否则是床头板等高起结构。
            # 放宽到 0.25：长矮柜紧贴床头板时，其底座常被床头板遮挡、z 起点抬升。
            if zs.min() > 0.25:
                continue
            groups[int(key)] = band_indices[in_key]
        visited: set[int] = set()
        for key in sorted(groups):
            if key in visited:
                continue
            stack = [key]
            component: list[int] = []
            while stack:
                current = stack.pop()
                if current in visited:
                    continue
                visited.add(current)
                component.append(current)
                for delta in (-1, 1):
                    neighbor = current + delta
                    if neighbor in groups and neighbor not in visited:
                        stack.append(neighbor)
            if len(component) * cell < min_length:
                continue
            for k in component:
                keep[groups[k]] = False
    if int(keep.sum()) < max(20, int(len(points) * 0.25)):
        return points  # 剔除过狠说明候选本身贴墙/点太少，回退原样
    return points[keep]


def _support_extents(values: np.ndarray, bin_w: float, *,
                     fallback: tuple[float, float] = (2.0, 98.0),
                     min_points: int = 40, keep_frac: float = 0.15) -> tuple[float, float]:
    """按点云密度轮廓（而非固定百分位）估计沿一条轴的真实外廓。

    固定百分位（p2/p98）是长度不准的根源之一：稀疏簇尾部的孤立噪声点会把
    盒子拉大，而缺面时又把盒子截短。密度轮廓法先求该轴的支撑直方图，再把
    外廓收在“支撑仍 ≥ 峰值 15%”的最外侧格上，天然对尾部噪声稳健。
    外廓只会比百分位更“收”，绝不更“扩”——百分位外廓是上界。
    """
    values = np.asarray(values, dtype=float)
    if len(values) < min_points or values.max() - values.min() < 8 * bin_w:
        return tuple(np.percentile(values, fallback))
    edges = np.arange(np.floor(values.min() / bin_w) * bin_w,
                      np.ceil(values.max() / bin_w) * bin_w + bin_w, bin_w)
    counts, edges = np.histogram(values, bins=edges)
    peak = int(counts.max())
    if peak <= 0:
        return tuple(np.percentile(values, fallback))
    kept = np.flatnonzero(counts >= max(peak * keep_frac, 3.0))
    if kept.size == 0:
        return tuple(np.percentile(values, fallback))
    low = float(edges[kept[0]] - bin_w / 2)
    high = float(edges[kept[-1] + 1] + bin_w / 2)
    p_low, p_high = np.percentile(values, fallback)
    return max(low, p_low), min(high, p_high)


def _snap_to_walls(center: np.ndarray, size: np.ndarray, theta: float,
                   lo: np.ndarray, hi: np.ndarray, tol: float = 0.12,
                   cap_ratio: float = 0.20, cap_abs: float = 0.0) -> tuple[np.ndarray, np.ndarray]:
    """把贴墙物体“缺失的那一面”补到墙平面。

    贴墙物体的墙面一侧几乎永远没有点（墙与物体之间无空隙），SVD 盒子会把
    这一面截短。若盒面离墙不足 tol 且面法向确实指向该墙，就把该面推到墙
    平面、另一面保持不动——只补缺失面，不整体平移，房间中间的物体不受影响。
    """
    ax = np.array([math.cos(theta), math.sin(theta), 0.0])
    ay = np.array([-math.sin(theta), math.cos(theta), 0.0])
    faces = [(ax, size[0] / 2, 0), (-ax, size[0] / 2, 0),
             (ay, size[1] / 2, 1), (-ay, size[1] / 2, 1)]
    for normal, half, axis_id in faces:
        face = center + normal * half
        for wall_coord, wall_val in ((0, lo[0]), (0, hi[0]), (1, lo[1]), (1, hi[1])):
            inward = 1.0 if wall_val == lo[wall_coord] else -1.0  # 指向房间内的法向
            toward_wall = -normal[wall_coord] * inward  # 面法向朝墙的分量
            if toward_wall < 0.7:
                continue  # 盒子斜放/该面并非朝这面墙，不吸附
            dist = (face[wall_coord] - wall_val) * inward  # 面在墙内侧为正
            if 0.0 < dist <= tol:
                shift = min(dist, max(cap_ratio * size[axis_id], cap_abs) + 1e-6)
                center[wall_coord] -= shift * inward / 2
                size[axis_id] += shift * toward_wall
    return center, size


def _flat_surface_band(cropped: np.ndarray) -> np.ndarray:
    """平放物体只保留“表面所在高度”以上的点（单侧截断）。

    主表面带不能取 z 直方图峰值±0.14：裁剪盒里常混进更低矮的杂物
    （矮柜/长凳面），峰值会被它们抢走，反而把床面/桌面排除掉。
    单侧 p35 截断保留目标物体的顶面与立面，配合他物 footprint 互斥
    与密度轮廓外推共同分离粘连。
    """
    z = cropped[:, 2]
    z_lo = float(np.percentile(z, 35))
    return cropped[z > z_lo - 0.02]


def _main_cluster(points: np.ndarray, small: bool, diagnostic: dict | None = None) -> np.ndarray:
    if len(points) == 0:
        if diagnostic is not None:
            diagnostic.update(points_after_downsample=0, cluster_count=0, selected_cluster_points=0)
        return points
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    cloud = cloud.voxel_down_sample(0.025 if small else 0.035)
    pts = np.asarray(cloud.points)
    if diagnostic is not None:
        diagnostic["points_after_downsample"] = int(len(pts))
    if len(pts) < (20 if small else 60):
        if diagnostic is not None:
            diagnostic.update(cluster_count=1 if len(pts) else 0, selected_cluster_points=int(len(pts)))
        return pts
    labels = np.asarray(cloud.cluster_dbscan(eps=0.11 if small else 0.16, min_points=8, print_progress=False))
    valid = labels >= 0
    if not valid.any():
        if diagnostic is not None:
            diagnostic.update(cluster_count=0, selected_cluster_points=0)
        return np.empty((0, 3))
    ids, counts = np.unique(labels[valid], return_counts=True)
    selected = pts[labels == ids[np.argmax(counts)]]
    if diagnostic is not None:
        diagnostic.update(cluster_count=int(len(ids)), selected_cluster_points=int(len(selected)))
    return selected


def _fit_object(points: np.ndarray, candidate: dict, lo: np.ndarray, hi: np.ndarray,
                other_objects: list[dict] | None = None, diagnostic: dict | None = None) -> dict:
    source_label = str(candidate.get("category", "unknown")).lower().strip()
    label = LABEL_ALIASES.get(source_label, source_label)
    result = dict(candidate)
    # 保留模型原始候选边界。后续多视角实例若只观测到桌面/书架中段，可用
    # 同位置且语义一致的候选补全整体边界；拟合后的 center/size 仍单独保存。
    result["candidate_center"] = list(candidate.get("center", []))
    result["candidate_size"] = list(candidate.get("size", []))
    result["candidate_rotation_z_deg"] = float(candidate.get("rotation_z_deg", 0.0))
    result["label"] = label
    result["source_label"] = source_label
    geometry_diagnostic = diagnostic.setdefault("geometry", {}) if diagnostic is not None else None
    if label not in DISPLAY_OBJECTS:
        result.update(geometry_status="rejected", rejection_reason="unsupported_category")
        return result
    small = label in SMALL_OBJECTS
    flat = label in {"bed", "sofa", "table", "desk", "small_table", "coffee_table", "dining_table", "nightstand"}
    # 桌类候选的 z 常低于真实桌面（SpatialLM 只猜到桌腿高度），向上扩裁剪盒
    # 让桌面进入裁剪区——桌面是这类物体唯一大面积的水平表面，是拟合的关键。
    # 只对“小候选”扩展：墙面被误标成 table/wardrobe 的大长条候选不扩。
    if (label in {"desk", "table", "small_table", "nightstand", "dining_table", "coffee_table"}
            and max(float(candidate["size"][0]), float(candidate["size"][1])) <= 0.6):
        candidate = dict(candidate)
        expanded = list(candidate.get("size", [1, 1, 1]))
        expanded[2] = float(expanded[2]) + 0.9
        candidate["size"] = expanded
    # 高柜（衣柜/柜子/书架）候选的 z 常被 SpatialLM 压扁（把 2m 高的书架
    # 猜成 0.28m 的小盒），向上扩裁剪盒让整个柜体进入裁剪区——否则高度
    # 会被量成 0.4m 级别（“0.49 米书架”错误的根因）。
    if label in {"wardrobe", "cabinet", "bookshelf", "tv_stand"}:
        candidate = dict(candidate)
        expanded = list(candidate.get("size", [1, 1, 1]))
        expanded[2] = float(expanded[2]) + 1.5
        candidate["size"] = expanded
    cropped = _candidate_points(points, candidate)
    if geometry_diagnostic is not None:
        geometry_diagnostic["points_before_filter"] = int(len(cropped))
    # 根因修正 1：候选裁剪盒常把背后墙面包进来，先剔除整面墙的薄片，
    # 否则 DBSCAN 会选中“墙”而不是物体（书桌/床贴墙时长度被墙拉长）。
    cropped = _remove_wall_sheet(cropped, lo, hi)
    if geometry_diagnostic is not None:
        geometry_diagnostic["points_after_wall_filter"] = int(len(cropped))
    # 根因修正 2：剔除贴墙低矮长条物（矮柜/长凳），防止床/桌与它们粘连成对角簇。
    cropped = _remove_wall_strips(cropped, lo, hi)
    if geometry_diagnostic is not None:
        geometry_diagnostic["points_after_wall_strip_filter"] = int(len(cropped))
    # 根因修正 3：平放物体取表面带。桌类取“最高面带”（桌面），甩掉椅子等
    # 更低杂物；床/沙发取 p35 单侧截断（保留床面与床头板）。
    # 高柜（衣柜/柜子/书架）取 z>0.45 的“柜体带”：床面/桌面都在 0.45 以下，
    # 高柜与床角相邻时按高度天然分离，不再互相吃。
    if flat and len(cropped) > 0:
        if label in {"desk", "table", "small_table", "nightstand", "dining_table", "coffee_table"}:
            z_max = float(cropped[:, 2].max())
            cropped = cropped[cropped[:, 2] > z_max - 0.30]
        else:
            cropped = _flat_surface_band(cropped)
    elif label in {"wardrobe", "cabinet", "bookshelf", "tv_stand"} and len(cropped) > 0:
        cropped = cropped[cropped[:, 2] > 0.45]
    if geometry_diagnostic is not None:
        geometry_diagnostic["points_after_height_filter"] = int(len(cropped))
    for other in (other_objects or []):
        if label in {"wardrobe", "cabinet", "bookshelf", "tv_stand"}:
            break  # 高柜用 z>0.45 柜体带分离，不再做 XY 互斥（避免被床角切掉柜体）
        if not other.get("center"):
            continue
        oc = np.asarray(other["center"], dtype=float)
        osz = np.asarray(other["size"], dtype=float)
        oth = math.radians(float(other.get("rotation_z_deg", 0.0)))
        od = cropped - oc
        olx = od[:, 0] * math.cos(oth) + od[:, 1] * math.sin(oth)
        oly = -od[:, 0] * math.sin(oth) + od[:, 1] * math.cos(oth)
        keep = ~(
            (np.abs(olx) <= osz[0] / 2 + 0.10) & (np.abs(oly) <= osz[1] / 2 + 0.10)
        )
        if keep.sum() > 50:
            cropped = cropped[keep]
    if geometry_diagnostic is not None:
        geometry_diagnostic["points_after_neighbor_filter"] = int(len(cropped))
        geometry_diagnostic["points_after_filter"] = int(len(cropped))
    cluster = _main_cluster(cropped, small, geometry_diagnostic)
    minimum = 18 if small else 55
    if len(cluster) < minimum:
        result.update(geometry_status="rejected", rejection_reason="insufficient_cluster", support_points=int(len(cluster)))
        return result
    # 旋转取 SVD 主轴（候选框角度常错 90°/45°，直接用会把盒子撑大）
    xy = cluster[:, :2]
    center_xy = np.median(xy, axis=0)
    _, _, axes2d = np.linalg.svd(xy - center_xy, full_matrices=False)
    local = (xy - center_xy) @ axes2d.T
    # 根因修正 2：外廓用“密度轮廓”而非固定百分位——百分位被稀疏尾部噪声
    # 拉大（书桌薄面 +20% 误差的来源），密度轮廓只收到支撑还在的位置。
    # 桌类旁边常贴着椅子，尾部修剪更狠（keep_frac 0.25）。
    support_keep = 0.25 if label in {"desk", "table", "small_table", "nightstand",
                                     "dining_table", "coffee_table"} else 0.15
    theta = float(math.atan2(axes2d[0, 1], axes2d[0, 0]))
    bed_axis_aligned = False
    # 根因修正 3：床/沙发与墙平行（先验）。床头板/邻物混入会让 SVD 轴偏转
    # 十几度，斜盒子把宽度撑大——±20° 内吸附到 90° 倍数。
    if label in {"bed", "sofa"}:
        snapped = round(theta / (math.pi / 2)) * (math.pi / 2)
        if abs(theta - snapped) <= math.radians(20):
            theta = snapped
            axes2d = np.array([[math.cos(theta), math.sin(theta)],
                               [-math.sin(theta), math.cos(theta)]])
            local = (xy - center_xy) @ axes2d.T
            bed_axis_aligned = True
    if bed_axis_aligned:
        # 长轴（床头板/床尾板是真实结构，低支撑也是真的）用百分位；
        # 短轴（床体实心，两侧面板是边界）用密度轮廓，尾部修剪收紧。
        low_x, high_x = np.percentile(local[:, 0], [2, 98])
        low_y, high_y = _support_extents(local[:, 1], 0.03, keep_frac=0.25)
    else:
        low_x, high_x = _support_extents(local[:, 0], 0.03, keep_frac=support_keep)
        low_y, high_y = _support_extents(local[:, 1], 0.03, keep_frac=support_keep)
    low_z, high_z = np.percentile(cluster[:, 2], [3, 97])
    size = np.array([high_x - low_x, high_y - low_y, high_z - low_z])
    local_center = np.array([(low_x + high_x) / 2, (low_y + high_y) / 2])
    center_xy_world = center_xy + local_center @ axes2d
    center = np.array([center_xy_world[0], center_xy_world[1], (low_z + high_z) / 2])
    if geometry_diagnostic is not None:
        geometry_diagnostic["bbox"] = {
            "center": center.tolist(), "size": size.tolist(), "rotation_z_deg": theta,
        }
    if np.any(size < 0.06) or np.any(size > np.array([5.0, 5.0, 3.5])):
        result.update(geometry_status="rejected", rejection_reason="implausible_size", support_points=int(len(cluster)))
        return result
    # 根因修正 3：贴墙物体缺失“靠墙那一面”，把该面补到墙平面（只补缺失面，
    # 不整体平移；房间中间的物体不受影响）。高柜类贴墙是常态，吸附更宽松。
    if label in {"wardrobe", "cabinet", "bookshelf", "tv_stand"}:
        center, size = _snap_to_walls(center, size, theta, lo, hi, tol=0.30, cap_ratio=0.20, cap_abs=0.30)
    else:
        center, size = _snap_to_walls(center, size, theta, lo, hi)
    inside = lo[0] - 0.1 <= center[0] <= hi[0] + 0.1 and lo[1] - 0.1 <= center[1] <= hi[1] + 0.1
    floor_gap = low_z
    # 桌类的主簇常是“桌面”（悬在 0.6-0.8m 高），但桌腿落在地上；悬空判定
    # 应看裁剪盒内的最低点（地面），而不是桌面高度。
    if label in {"desk", "table", "small_table", "nightstand", "dining_table", "coffee_table"}:
        floor_gap = float(np.percentile(cropped[:, 2], 10)) if len(cropped) else low_z
    if not inside or floor_gap > (0.35 if small else 0.55):
        result.update(geometry_status="rejected", rejection_reason="outside_or_floating", support_points=int(len(cluster)))
        return result
    # 根因修正 4：逐类别合理性先验——墙面被当成床、吊柜被当衣柜这类
    # 系统性错标，直接按高度/长边范围拒绝，而不是带进后续测量。
    plausible = LABEL_PLAUSIBILITY.get(label)
    if plausible is not None:
        h_lo, h_hi, min_long = plausible
        if not (h_lo <= size[2] <= h_hi and max(size[0], size[1]) >= min_long):
            result.update(geometry_status="rejected", rejection_reason="implausible_for_label",
                          support_points=int(len(cluster)))
            return result
    result.update(
        center=center.tolist(), size=size.tolist(), rotation_z_deg=theta,
        geometry_status="verified", support_points=int(len(cluster)),
        geometry_confidence=float(np.clip(len(cluster) / (220 if small else 800), 0.35, 1.0)),
    )
    if geometry_diagnostic is not None:
        geometry_diagnostic["bbox"] = {
            "center": result["center"], "size": result["size"],
            "rotation_z_deg": result["rotation_z_deg"],
        }
        geometry_diagnostic["geometry_confidence"] = result["geometry_confidence"]
    return result


def _rect_overlap(a: dict, b: dict) -> float:
    """轴对齐近似足够用于去除 SpatialLM 同一位置的多标签重复候选。"""
    ac, bc = np.asarray(a["center"][:2]), np.asarray(b["center"][:2])
    ah, bh = np.asarray(a["size"][:2]) / 2, np.asarray(b["size"][:2]) / 2
    overlap = np.maximum(0.0, np.minimum(ac + ah, bc + bh) - np.maximum(ac - ah, bc - bh))
    inter = float(overlap[0] * overlap[1])
    amin = min(float(np.prod(a["size"][:2])), float(np.prod(b["size"][:2])))
    return inter / max(amin, 1e-6)


def _deduplicate_objects(objects: list[dict]) -> tuple[list[dict], list[dict]]:
    priority = {"bed": 9, "wardrobe": 8, "sofa": 7, "table": 6, "desk": 6, "cabinet": 5,
                "bookshelf": 5, "chair": 4, "small_table": 3, "stool": 3}
    kept: list[dict] = []
    rejected: list[dict] = []
    ordered = sorted(objects, key=lambda x: (priority.get(x["label"], 1), x.get("geometry_confidence", 0), np.prod(x["size"])), reverse=True)
    for item in ordered:
        duplicate = next((other for other in kept if _rect_overlap(item, other) > 0.60 and abs(item["center"][2] - other["center"][2]) < 0.55), None)
        if duplicate is None:
            item["instance_id"] = f"{item['label']}_{1 + sum(x['label'] == item['label'] for x in kept):02d}"
            kept.append(item)
        else:
            dropped = dict(item)
            dropped.update(geometry_status="rejected", rejection_reason=f"duplicate_of:{duplicate.get('instance_id', duplicate['label'])}")
            rejected.append(dropped)
    return kept, rejected


def _inside_box(points: np.ndarray, item: dict, margin: float = 0.08) -> np.ndarray:
    center = np.asarray(item["center"], dtype=float)
    half = np.asarray(item["size"], dtype=float) / 2 + margin
    theta = math.radians(float(item.get("rotation_z_deg", 0.0)))
    delta = points - center
    lx = delta[:, 0] * math.cos(theta) + delta[:, 1] * math.sin(theta)
    ly = -delta[:, 0] * math.sin(theta) + delta[:, 1] * math.cos(theta)
    return (np.abs(lx) <= half[0]) & (np.abs(ly) <= half[1]) & (np.abs(delta[:, 2]) <= half[2])


def _geometric_obstacles(points: np.ndarray, objects: list[dict], lo: np.ndarray, hi: np.ndarray, height: float) -> list[dict]:
    """提取无可靠类别、但确实占据房间空间的几何聚类。

    只接受离墙、离地噪声带之外且具有连续体积的聚类。细长低点数物体仍需视频2D融合。
    """
    mask = (
        (points[:, 2] > 0.07) & (points[:, 2] < min(height - 0.12, 1.85))
        & (points[:, 0] > lo[0] + 0.12) & (points[:, 0] < hi[0] - 0.12)
        & (points[:, 1] > lo[1] + 0.12) & (points[:, 1] < hi[1] - 0.12)
    )
    for item in objects:
        mask &= ~_inside_box(points, item, 0.12)
    residual = points[mask]
    if len(residual) < 40:
        return []
    # 俯视高度占用图比纯 3D DBSCAN 更适合不完整点云：只要连续地面区域上
    # 存在足够多的高于地面点，就保留其占地轮廓和高度范围。
    from scipy import ndimage

    cell = 0.08
    shape = np.maximum(np.ceil((hi - lo) / cell).astype(int), 1)
    indices = np.floor((residual[:, :2] - lo) / cell).astype(int)
    valid = np.all((indices >= 0) & (indices < shape), axis=1)
    residual, indices = residual[valid], indices[valid]
    counts = np.zeros(tuple(shape), dtype=np.int32)
    height85 = np.zeros(tuple(shape), dtype=np.float32)
    height05 = np.zeros(tuple(shape), dtype=np.float32)
    for key in np.unique(indices, axis=0):
        z = residual[np.all(indices == key, axis=1), 2]
        i, j = key
        counts[i, j] = len(z)
        height05[i, j], height85[i, j] = np.percentile(z, [5, 85])
    occupied = (counts >= 20) & (height85 >= 0.12)
    occupied = ndimage.binary_opening(occupied, structure=np.ones((2, 2), dtype=bool))
    labels, label_count = ndimage.label(occupied, structure=np.ones((3, 3), dtype=np.int8))
    obstacles: list[dict] = []
    for cluster_id in range(1, label_count + 1):
        cells = np.argwhere(labels == cluster_id)
        if len(cells) < 18:  # 只保留“大箱子”级别的落地物，小碎片/小杂物不画
            continue
        cell_min, cell_max = cells.min(axis=0), cells.max(axis=0) + 1
        low = np.array([*(lo + cell_min * cell), float(np.percentile(height05[labels == cluster_id], 20))])
        high = np.array([*(np.minimum(lo + cell_max * cell, hi)), float(np.percentile(height85[labels == cluster_id], 90))])
        size = high - low
        footprint = float(size[0] * size[1])
        volume = float(np.prod(size))
        # 排除零厚度碎片、巨大残余墙片；保留小箱子/凳子/未知落地物。
        if np.any(size < 0.055) or footprint < 0.012 or footprint > 1.5 or volume > 1.8:
            continue
        if low[2] > 0.38:  # 悬空物通常是灯/墙饰，不属于地面通行障碍
            continue
        center = (low + high) / 2
        obstacles.append({
            "instance_id": "", "label": "box", "center": center.tolist(),
            "size": size.tolist(), "rotation_z_deg": 0.0,
            "footprint": [[low[0], low[1]], [high[0], low[1]], [high[0], high[1]], [low[0], high[1]]],
            "height_range_m": [float(low[2]), float(high[2])],
            "geometry_status": "geometric_only", "support_points": int(counts[labels == cluster_id].sum()),
            "occupied_cells": int(len(cells)),
            "geometry_confidence": float(np.clip(len(cells) / 18.0, 0.35, 0.85)),
        })
    obstacles.sort(key=lambda item: (item["center"][0], item["center"][1]))
    for index, item in enumerate(obstacles, 1):
        item["instance_id"] = f"box_{index:02d}"
    return obstacles


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _new_object_diagnostic(index: int, candidate: dict) -> dict:
    """创建候选对象的只读追踪记录；不参与任何接受/拒绝决策。"""
    source_label = str(candidate.get("category", "unknown")).lower().strip()
    normalized_label = LABEL_ALIASES.get(source_label, source_label)
    return {
        "candidate_id": f"candidate_{index:03d}",
        "instance_id": None,
        "spatiallm_candidate": {
            "label": source_label,
            "normalized_label": normalized_label,
            "center": candidate.get("center"),
            "size": candidate.get("size"),
            "rotation_z_deg": candidate.get("rotation_z_deg", 0.0),
            "confidence": candidate.get("confidence", candidate.get("score")),
        },
        # 阶段 1 只建立追踪链。语义推理从阶段 2 才接入，零值必须明确表示
        # “尚未运行”，不能被误解为模型运行后没有检测到目标。
        "semantic_evidence": {
            "status": "not_run",
            "support_views": 0,
            "groundingdino_detections": 0,
            "sam_masks": 0,
            "semantic_votes": {},
            "semantic_point_count": 0,
        },
        "geometry": {
            "points_before_filter": 0,
            "points_after_filter": 0,
            "cluster_count": 0,
            "selected_cluster_points": 0,
            "bbox": None,
            "geometry_confidence": None,
        },
        "video_geometry_evidence": {"status": "not_available", "support_views": 0},
        "status": "pending",
        "reject_reason": None,
    }


def _write_object_diagnostics(
    path: Path,
    records: list[dict],
    accepted: list[dict],
    rejected: list[dict],
    *,
    video_fusion_status: str,
    source_files: dict,
) -> dict:
    """将最终状态回填到候选追踪记录并独立写盘。"""
    by_id = {record["candidate_id"]: record for record in records}
    for item in accepted:
        record = by_id.get(item.get("_diagnostic_id"))
        if record is None:
            continue
        record["instance_id"] = item.get("instance_id")
        record["status"] = "accepted"
        record["reject_reason"] = None
        record["geometry"]["bbox"] = {
            "center": item.get("center"), "size": item.get("size"),
            "rotation_z_deg": item.get("rotation_z_deg", 0.0),
        }
        record["geometry"]["geometry_confidence"] = item.get("geometry_confidence")
        refinement = item.get("video_refinement") or {}
        record["video_geometry_evidence"] = {
            "status": refinement.get("status", "not_available"),
            "support_views": int(refinement.get("views_used", 0) or 0),
            "faces": refinement.get("faces", {}),
        }
    for item in rejected:
        record = by_id.get(item.get("_diagnostic_id"))
        if record is None:
            continue
        record["instance_id"] = item.get("instance_id")
        record["status"] = "rejected"
        record["reject_reason"] = item.get("rejection_reason", "geometry_not_verified")
        if item.get("geometry_confidence") is not None:
            record["geometry"]["geometry_confidence"] = item.get("geometry_confidence")
        refinement = item.get("video_refinement") or {}
        if refinement:
            record["video_geometry_evidence"] = {
                "status": refinement.get("status", "unknown"),
                "support_views": int(refinement.get("views_used", 0) or 0),
                "faces": refinement.get("faces", {}),
            }
    payload = {
        "schema_version": 1,
        "diagnostic_stage": "geometry_baseline",
        "decision_behavior": "observational_only",
        "semantic_pipeline_status": "not_run",
        "video_fusion_status": video_fusion_status,
        "source": source_files,
        "counts": {
            "candidates": len(records),
            "accepted": sum(record["status"] == "accepted" for record in records),
            "rejected": sum(record["status"] == "rejected" for record in records),
        },
        "objects": records,
    }
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload


def build_structure(
    aligned_ply: Path, layout_json: Path, alignment_json: Path, output_json: Path,
    object_layout_json: Path | None = None,
    cameras_json: Path | None = None,
    images_dir: Path | None = None,
    diagnostics_json: Path | None = None,
) -> dict:
    """生成 structure.json；不修改任何输入点云或原始 SpatialLM 结果。

    cameras_json / images_dir 提供时，用多视角视频边界证据修正家具盒子尺寸
    （点云+视频融合的长度识别）；缺失时自动退化为纯点云路径。
    """
    cloud = o3d.io.read_point_cloud(str(aligned_ply))
    points = np.asarray(cloud.points, dtype=float)
    if len(points) < 1000:
        raise RuntimeError("结构提取所需点云不足")
    layout = _load_json(layout_json)
    object_layout = _load_json(object_layout_json) if object_layout_json and Path(object_layout_json).is_file() else layout
    alignment = _load_json(alignment_json)
    lo, hi, height = _room_bounds(points, alignment)
    walls = _walls(lo, hi, height)
    wall_mapping = _map_source_walls(layout.get("walls", []), walls)
    door_candidates = [_snap_opening({**item, "kind": "door"}, walls, lo, hi, height, points, wall_mapping) for item in layout.get("doors", [])]
    window_candidates = [_snap_opening({**item, "kind": "window"}, walls, lo, hi, height, points, wall_mapping) for item in layout.get("windows", [])]
    doors = [item for item in door_candidates if item["geometry_status"] in {"verified", "semantic_supported"}]
    windows = [item for item in window_candidates if item["geometry_status"] in {"verified", "semantic_supported"}]
    priority = {"desk": 10, "table": 10, "small_table": 10, "nightstand": 10,
                "dining_table": 10, "coffee_table": 10, "bed": 9, "wardrobe": 8, "sofa": 7,
                "cabinet": 5, "bookshelf": 5, "chair": 4, "stool": 3}
    candidates = sorted(
        object_layout.get("objects", []),
        key=lambda item: -priority.get(LABEL_ALIASES.get(str(item.get("category", "")).lower().strip(),
                                                          str(item.get("category", "")).lower().strip()), 1),
    )
    all_objects = []
    fitted_boxes: list[dict] = []
    object_diagnostics: list[dict] = []
    for index, item in enumerate(candidates, 1):
        diagnostic = _new_object_diagnostic(index, item)
        fitted = _fit_object(points, item, lo, hi, other_objects=fitted_boxes, diagnostic=diagnostic)
        fitted["_diagnostic_id"] = diagnostic["candidate_id"]
        object_diagnostics.append(diagnostic)
        all_objects.append(fitted)
        # 大件已验证物体参与后续候选的 footprint 互斥（床不吃书桌、书桌不吃床）。
        # 小物（椅/凳）的拟合不稳定，参与互斥反而会误删大件区域，故不参与。
        if fitted.get("geometry_status") == "verified" and fitted.get("label") in LARGE_OBJECTS:
            fitted_boxes.append(fitted)
    verified_raw = [item for item in all_objects if item.get("geometry_status") == "verified"]
    # 根因修正：贴墙的高薄片与已验证门洞重叠 = 关着的门板本身（SpatialLM 常把
    # 门板误标成 wardrobe/cabinet）。真衣柜深度 ≥0.25m；门板 <0.25m 且 1.7m+ 高。
    def _door_panel_conflict(item: dict) -> bool:
        if item.get("label") not in {"wardrobe", "cabinet", "bookshelf"}:
            return False
        size = np.asarray(item["size"], dtype=float)
        if size[2] < 1.7 or min(size[0], size[1]) >= 0.25:
            return False
        center = np.asarray(item["center"], dtype=float)
        along_half = max(size[0], size[1]) / 2
        for door in doors:
            d_center = np.asarray(door["center"], dtype=float)
            d_width = float(door["size"][0])
            wall_id = int(door["wall_id"])
            wall_coord = 0 if wall_id in (1, 3) else 1
            along_coord = 1 - wall_coord
            near_wall = abs(center[wall_coord] - d_center[wall_coord]) < 0.40
            along_gap = abs(center[along_coord] - d_center[along_coord]) - (along_half + d_width / 2)
            if near_wall and along_gap < 0.10:
                return True
        return False

    for item in verified_raw:
        if _door_panel_conflict(item):
            item["geometry_status"] = "rejected"
            item["rejection_reason"] = "door_panel"
    verified_raw = [item for item in verified_raw if item.get("geometry_status") == "verified"]
    # 根因修正 4（点云+视频融合）：点云不完整/粘连造成的边界偏差，用多视角
    # 视频轮廓（图像梯度边界）逐面修正。无相机位姿/图像时跳过，不影响纯点云路径。
    video_fusion_status = "not_available"
    if cameras_json is not None and images_dir is not None and Path(cameras_json).is_file():
        try:
            from pipeline.video_box_refiner import refine_objects

            verified_raw = refine_objects(verified_raw, points, Path(cameras_json), Path(images_dir))
            video_fusion_status = "applied"
        except Exception as exc:  # noqa: BLE001 - 视频修正失败不阻断结构生成
            logger.warning("video_box_refinement_skipped reason=%s", str(exc)[:300])
            video_fusion_status = f"failed: {str(exc)[:120]}"
    rejected = [item for item in all_objects if item.get("geometry_status") != "verified"]
    verified, duplicate_rejected = _deduplicate_objects(verified_raw)
    rejected.extend(duplicate_rejected)
    obstacles = _geometric_obstacles(points, verified, lo, hi, height)
    if diagnostics_json is not None:
        _write_object_diagnostics(
            diagnostics_json, object_diagnostics, verified, rejected,
            video_fusion_status=video_fusion_status,
            source_files={
                "geometry": Path(aligned_ply).name,
                "architecture_candidates": Path(layout_json).name,
                "object_candidates": Path(object_layout_json).name if object_layout_json else Path(layout_json).name,
            },
        )
    # 内部关联键只服务于独立 diagnostic 文件，不进入既有 structure.json 契约。
    for item in verified + rejected:
        item.pop("_diagnostic_id", None)
    payload = {
        "schema_version": 1, "coordinate_unit": "meters", "z_up": True,
        "source": {"geometry": "scene_aligned.ply", "architecture_candidates": Path(layout_json).name,
                   "object_candidates": Path(object_layout_json).name if object_layout_json else Path(layout_json).name,
                   "sha256": {"scene_aligned.ply": _sha256(aligned_ply), "alignment.json": _sha256(alignment_json),
                              Path(layout_json).name: _sha256(layout_json)}},
        "room": {
            "height_m": height,
            "floor_polygon": [[lo[0], lo[1], 0.0], [hi[0], lo[1], 0.0], [hi[0], hi[1], 0.0], [lo[0], hi[1], 0.0]],
            "bounds_xy": {"min": lo.tolist(), "max": hi.tolist()},
        },
        "walls": walls, "doors": doors, "windows": windows, "objects": verified,
        "geometric_obstacles": obstacles,
        "obstacle_detection_status": "requires_video_2d_fusion_for_small_or_thin_objects",
        "video_fusion_status": video_fusion_status,
        "rejected_objects": rejected,
        "rejected_openings": [item for item in door_candidates + window_candidates if item["geometry_status"] == "rejected"],
        "counts": {"walls": len(walls), "doors": len(doors), "windows": len(windows), "objects": len(verified),
                   "geometric_obstacles": len(obstacles), "rejected": len(rejected)},
    }
    output_json = Path(output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    return payload
