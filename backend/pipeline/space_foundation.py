"""独立的二维通道分析与空间基础数据导出。

本模块只读取 ``structure(_calibrated).json`` 与 ``measurements.json``：

* 不读取或修改点云；
* 不修改结构图、结构数据或家具识别；
* 不执行风险评分，只提供后续评分所需的客观尺寸与几何状态。

输出：

* ``passage_analysis.png``：结构平面图的独立通道标注副本；
* ``passage_analysis.json``：门到床路径、净宽和关键距离；
* ``spatial_foundation.json``：房间/门窗/家具/通道统一数据契约。
"""
from __future__ import annotations

import hashlib
import heapq
import json
import math
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
import numpy as np
from scipy.ndimage import distance_transform_edt

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon, Rectangle  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

LABEL_CN = {
    "bed": "床", "wardrobe": "衣柜", "sofa": "沙发", "desk": "书桌",
    "table": "桌子", "cabinet": "柜子", "bookshelf": "书架",
    "chair": "椅子", "stool": "凳子", "small_table": "小桌",
    "storage_rack": "小收纳架", "box": "箱子", "unknown_obstacle": "障碍物",
}
DEFAULT_DIRECT_PASS_WIDTH_M = 0.45
DEFAULT_SIDEWAYS_PASS_WIDTH_M = 0.30


def _geometric_passage_class(
    width_m: float | None,
    direct_width_m: float,
    sideways_width_m: float,
) -> str:
    """Return geometric passability only; this is not a safety/risk rating."""
    if width_m is None:
        return "unknown"
    if width_m >= direct_width_m:
        return "normal_pass"
    if width_m >= sideways_width_m:
        return "sideways_pass"
    return "not_passable"


def _load(path: Path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _object_rect(item: dict) -> tuple[np.ndarray, np.ndarray, float]:
    center = np.asarray(item.get("center") or [0, 0, 0], dtype=float)[:2]
    size = np.asarray([
        item.get("length_m") or (item.get("size") or [0.2, 0.2])[0],
        item.get("width_m") or (item.get("size") or [0.2, 0.2])[1],
    ], dtype=float)
    return center, size, float(item.get("rotation_z_deg") or 0.0)


def _corners(center: np.ndarray, size: np.ndarray, yaw_deg: float) -> np.ndarray:
    theta = math.radians(yaw_deg)
    c, s = math.cos(theta), math.sin(theta)
    result = []
    for sx, sy in ((-1, -1), (-1, 1), (1, 1), (1, -1)):
        dx, dy = sx * size[0] / 2, sy * size[1] / 2
        result.append([center[0] + dx * c - dy * s, center[1] + dx * s + dy * c])
    return np.asarray(result, dtype=float)


def _point_segment_distance(point: np.ndarray, a: np.ndarray, b: np.ndarray) -> float:
    delta = b - a
    denom = float(np.dot(delta, delta))
    if denom <= 1e-12:
        return float(np.linalg.norm(point - a))
    t = float(np.clip(np.dot(point - a, delta) / denom, 0.0, 1.0))
    return float(np.linalg.norm(point - (a + t * delta)))


def _segments_intersect(a, b, c, d) -> bool:
    def orient(p, q, r):
        first, second = q - p, r - p
        return float(first[0] * second[1] - first[1] * second[0])

    def on_segment(p, q, r):
        return (
            min(p[0], r[0]) - 1e-9 <= q[0] <= max(p[0], r[0]) + 1e-9
            and min(p[1], r[1]) - 1e-9 <= q[1] <= max(p[1], r[1]) + 1e-9
        )

    o1, o2, o3, o4 = orient(a, b, c), orient(a, b, d), orient(c, d, a), orient(c, d, b)
    if o1 * o2 < -1e-9 and o3 * o4 < -1e-9:
        return True
    return (
        (abs(o1) <= 1e-9 and on_segment(a, c, b))
        or (abs(o2) <= 1e-9 and on_segment(a, d, b))
        or (abs(o3) <= 1e-9 and on_segment(c, a, d))
        or (abs(o4) <= 1e-9 and on_segment(c, b, d))
    )


def _point_in_convex(point: np.ndarray, polygon: np.ndarray) -> bool:
    signs = []
    for index in range(len(polygon)):
        a, b = polygon[index], polygon[(index + 1) % len(polygon)]
        edge, delta = b - a, point - a
        cross = edge[0] * delta[1] - edge[1] * delta[0]
        if abs(cross) > 1e-9:
            signs.append(cross > 0)
    return bool(signs) and all(value == signs[0] for value in signs)


def _rect_distance(a: dict, b: dict) -> float:
    ca, sa, ra = _object_rect(a)
    cb, sb, rb = _object_rect(b)
    pa, pb = _corners(ca, sa, ra), _corners(cb, sb, rb)
    edges_a = [(pa[i], pa[(i + 1) % 4]) for i in range(4)]
    edges_b = [(pb[i], pb[(i + 1) % 4]) for i in range(4)]
    if (
        any(_segments_intersect(x1, x2, y1, y2) for x1, x2 in edges_a for y1, y2 in edges_b)
        or _point_in_convex(pa[0], pb)
        or _point_in_convex(pb[0], pa)
    ):
        return 0.0
    distances = []
    for point in pa:
        distances.extend(_point_segment_distance(point, x1, x2) for x1, x2 in edges_b)
    for point in pb:
        distances.extend(_point_segment_distance(point, x1, x2) for x1, x2 in edges_a)
    return float(min(distances))


def _rasterize_rect(grid: np.ndarray, origin: np.ndarray, cell: float, item: dict) -> None:
    center, size, yaw = _object_rect(item)
    yy, xx = np.indices(grid.shape)
    world_x = origin[0] + (xx + 0.5) * cell
    world_y = origin[1] + (yy + 0.5) * cell
    theta = math.radians(yaw)
    dx, dy = world_x - center[0], world_y - center[1]
    local_x = dx * math.cos(theta) + dy * math.sin(theta)
    local_y = -dx * math.sin(theta) + dy * math.cos(theta)
    grid[(np.abs(local_x) <= size[0] / 2) & (np.abs(local_y) <= size[1] / 2)] = True


def _cell_of(point: np.ndarray, origin: np.ndarray, cell: float, shape) -> tuple[int, int]:
    col = int(np.floor((point[0] - origin[0]) / cell))
    row = int(np.floor((point[1] - origin[1]) / cell))
    return (int(np.clip(row, 0, shape[0] - 1)), int(np.clip(col, 0, shape[1] - 1)))


def _nearest_free(seed: tuple[int, int], free: np.ndarray, max_radius: int = 30) -> tuple[int, int] | None:
    if free[seed]:
        return seed
    row, col = seed
    for radius in range(1, max_radius + 1):
        candidates = []
        for rr in range(max(0, row - radius), min(free.shape[0], row + radius + 1)):
            for cc in range(max(0, col - radius), min(free.shape[1], col + radius + 1)):
                if free[rr, cc]:
                    candidates.append((rr, cc))
        if candidates:
            return min(candidates, key=lambda value: (value[0] - row) ** 2 + (value[1] - col) ** 2)
    return None


def _astar_clearance(
    occupied: np.ndarray,
    start: tuple[int, int],
    goals: set[tuple[int, int]],
    clearance_m: np.ndarray,
    cell: float,
) -> list[tuple[int, int]] | None:
    if not goals:
        return None
    goal_arr = np.asarray(list(goals), dtype=float)

    def heuristic(node):
        distance = np.linalg.norm(goal_arr - np.asarray(node, dtype=float), axis=1).min()
        return float(distance) * cell

    queue = [(heuristic(start), 0.0, start)]
    cost = {start: 0.0}
    parent = {start: None}
    while queue:
        _, current_cost, node = heapq.heappop(queue)
        if current_cost > cost.get(node, float("inf")) + 1e-9:
            continue
        if node in goals:
            path = []
            while node is not None:
                path.append(node)
                node = parent[node]
            return path[::-1]
        row, col = node
        for dr, dc in ((-1, -1), (-1, 0), (-1, 1), (0, -1), (0, 1), (1, -1), (1, 0), (1, 1)):
            rr, cc = row + dr, col + dc
            if not (0 <= rr < occupied.shape[0] and 0 <= cc < occupied.shape[1]):
                continue
            if occupied[rr, cc]:
                continue
            step = cell * (math.sqrt(2.0) if dr and dc else 1.0)
            # 在同样可达的情况下偏好净空更大的中心线，不引入安全评分阈值。
            penalty = 0.035 * step / max(float(clearance_m[rr, cc]), cell)
            candidate = current_cost + step + penalty
            if candidate + 1e-9 < cost.get((rr, cc), float("inf")):
                cost[(rr, cc)] = candidate
                parent[(rr, cc)] = node
                heapq.heappush(queue, (candidate + heuristic((rr, cc)), candidate, (rr, cc)))
    return None


def _object_name(item: dict) -> str:
    kind = str(item.get("type") or item.get("label") or "object")
    return LABEL_CN.get(kind, kind)


def _display_names(objects: list[dict]) -> dict[str, str]:
    totals: dict[str, int] = {}
    for item in objects:
        kind = str(item.get("type") or item.get("label") or "object")
        totals[kind] = totals.get(kind, 0) + 1
    seen: dict[str, int] = {}
    result = {}
    for item in objects:
        kind = str(item.get("type") or item.get("label") or "object")
        seen[kind] = seen.get(kind, 0) + 1
        base = LABEL_CN.get(kind, kind)
        name = f"{base}{seen[kind]}" if totals[kind] > 1 else base
        result[str(item.get("instance_id") or item.get("id"))] = name
    return result


def _connected_component(mask: np.ndarray, seed: tuple[int, int] | None) -> np.ndarray:
    connected = np.zeros_like(mask, dtype=bool)
    if seed is None or not mask[seed]:
        return connected
    stack = [seed]
    connected[seed] = True
    while stack:
        row, col = stack.pop()
        for dr, dc in ((-1, 0), (1, 0), (0, -1), (0, 1)):
            rr, cc = row + dr, col + dc
            if 0 <= rr < mask.shape[0] and 0 <= cc < mask.shape[1] and mask[rr, cc] and not connected[rr, cc]:
                connected[rr, cc] = True
                stack.append((rr, cc))
    return connected


def _prepare_inputs(measurements: dict, structure: dict):
    room = measurements.get("room") or {}
    bounds = (structure.get("room") or {}).get("bounds_xy") or {}
    minimum = np.asarray(bounds.get("min") or [0.0, 0.0], dtype=float)
    maximum = np.asarray(bounds.get("max") or [room.get("length_m", 5), room.get("width_m", 4)], dtype=float)
    objects = [
        item for item in measurements.get("objects", [])
        if item.get("measurement_status") == "verified"
        and item.get("center")
        and item.get("length_m") is not None
        and item.get("width_m") is not None
    ]
    openings = [
        item for item in measurements.get("openings", [])
        if item.get("measurement_status") == "verified" and item.get("center")
    ]
    return room, minimum, maximum, objects, openings


def analyze_structure_passages(
    measurements: dict,
    structure: dict,
    *,
    cell: float = 0.04,
    person_width_m: float = DEFAULT_DIRECT_PASS_WIDTH_M,
    sideways_width_m: float = DEFAULT_SIDEWAYS_PASS_WIDTH_M,
) -> dict:
    """仅根据已有二维结构分析门到床通道，不读取点云。"""
    room, minimum, maximum, objects, openings = _prepare_inputs(measurements, structure)
    shape = np.maximum(np.ceil((maximum - minimum) / cell).astype(int), 3)
    occupied = np.zeros((int(shape[1]), int(shape[0])), dtype=bool)
    # 房间边界作为障碍；门洞位置随后打开。
    occupied[[0, -1], :] = True
    occupied[:, [0, -1]] = True

    bed = next((item for item in objects if item.get("type") == "bed"), None)
    door = next((item for item in openings if item.get("type") == "door"), None)
    for item in objects:
        _rasterize_rect(occupied, minimum, cell, item)

    if door is not None:
        c = np.asarray(door["center"], dtype=float)[:2]
        width = float(door.get("width_m") or 0.8)
        yaw = float(door.get("rotation_z_deg") or 0.0)
        # 清空门洞附近的边界单元，门内侧仍由房间边界限制通行宽度。
        opening_box = {
            "center": [float(c[0]), float(c[1]), 0.0],
            "length_m": width if int(round(yaw)) % 180 == 0 else 0.18,
            "width_m": 0.18 if int(round(yaw)) % 180 == 0 else width,
            "rotation_z_deg": 0.0,
        }
        door_mask = np.zeros_like(occupied)
        _rasterize_rect(door_mask, minimum, cell, opening_box)
        occupied[door_mask] = False

    free = ~occupied
    clearance = distance_transform_edt(free) * cell
    person_radius_m = person_width_m / 2
    person_free = free & (clearance >= person_radius_m)
    status = "not_evaluable"
    reason = "door_or_bed_missing"
    path_cells = None
    person_path_found = False
    path_xy: list[list[float]] = []
    min_width = None
    narrowest_xy = None
    path_length = None

    start = None
    if door is not None and bed is not None:
        room_center = (minimum + maximum) / 2
        door_xy = np.asarray(door["center"], dtype=float)[:2]
        inward = room_center - door_xy
        inward /= max(float(np.linalg.norm(inward)), 1e-9)
        start_seed = _cell_of(door_xy + inward * 0.18, minimum, cell, occupied.shape)
        start = _nearest_free(start_seed, free)
        person_start = _nearest_free(start_seed, person_free, max_radius=15)

        bed_center, bed_size, bed_yaw = _object_rect(bed)
        # 目标是床外沿附近的可达单元，不穿过床本体。
        yy, xx = np.indices(occupied.shape)
        world_x = minimum[0] + (xx + 0.5) * cell
        world_y = minimum[1] + (yy + 0.5) * cell
        theta = math.radians(bed_yaw)
        dx, dy = world_x - bed_center[0], world_y - bed_center[1]
        lx = dx * math.cos(theta) + dy * math.sin(theta)
        ly = -dx * math.sin(theta) + dy * math.cos(theta)
        approach_margin = max(0.18, person_radius_m + 0.08)
        ring = (
            (np.abs(lx) <= bed_size[0] / 2 + approach_margin)
            & (np.abs(ly) <= bed_size[1] / 2 + approach_margin)
            & ~((np.abs(lx) <= bed_size[0] / 2) & (np.abs(ly) <= bed_size[1] / 2))
            & free
        )
        goals = {tuple(value) for value in np.argwhere(ring)}
        person_goals = {goal for goal in goals if person_free[goal]}
        # 优先寻找能容纳配置人体宽度的路线；只有没有人体路线时才回退到
        # 几何中心线，用于说明“空间连通但人宽不足”。
        path_cells = (
            _astar_clearance(~person_free, person_start, person_goals, clearance, cell)
            if person_start and person_goals else None
        )
        person_path_found = bool(path_cells)
        if not path_cells:
            path_cells = _astar_clearance(occupied, start, goals, clearance, cell) if start else None
        if path_cells:
            status, reason = "ok", None
            path_xy = [
                [round(float(minimum[0] + (col + 0.5) * cell), 4),
                 round(float(minimum[1] + (row + 0.5) * cell), 4)]
                for row, col in path_cells
            ]
            path_length = sum(
                math.dist(path_xy[index - 1], path_xy[index])
                for index in range(1, len(path_xy))
            )
            # 忽略目标家具外沿的最后 20cm，否则“接近床”会被误认为最窄通道。
            trim = max(1, int(round(0.12 / cell)))
            evaluated = path_cells[:-trim] if len(path_cells) > trim + 2 else path_cells
            widths = [2.0 * float(clearance[row, col]) for row, col in evaluated]
            if widths:
                narrow_index = int(np.argmin(widths))
                min_width = min(widths[narrow_index], float(door.get("width_m") or widths[narrow_index]))
                row, col = evaluated[narrow_index]
                narrowest_xy = [
                    round(float(minimum[0] + (col + 0.5) * cell), 4),
                    round(float(minimum[1] + (row + 0.5) * cell), 4),
                ]
        else:
            status, reason = "blocked", "no_geometric_path_in_current_structure"

    # 从门内侧出发、按人体半宽腐蚀后的连通自由区；这是“人可走区域”，
    # 与仅能通过一个几何中心点的 free 区域严格区分。
    person_start = _nearest_free(start, person_free, max_radius=15) if start is not None else None
    door_connected = _connected_component(person_free, person_start)

    attached_pairs = {
        frozenset((str(item.get("instance_id")), str(item.get("attached_to"))))
        for item in structure.get("semantic_instances", [])
        if item.get("instance_id") and item.get("attached_to")
    }
    names = _display_names(objects)
    pair_distances = []
    for index, first in enumerate(objects):
        for second in objects[index + 1:]:
            pair = frozenset((str(first.get("instance_id")), str(second.get("instance_id"))))
            if pair in attached_pairs:
                continue
            clearance_value = _rect_distance(first, second)
            pair_distances.append({
                "between": [first.get("instance_id"), second.get("instance_id")],
                "between_labels": [
                    names.get(str(first.get("instance_id")), _object_name(first)),
                    names.get(str(second.get("instance_id")), _object_name(second)),
                ],
                "clearance_m": round(clearance_value, 3),
                "geometry_relation": "overlap_or_touch" if clearance_value <= 1e-6 else "separated",
                "can_person_pass_by_width": bool(clearance_value >= person_width_m),
                "geometric_passage_class": _geometric_passage_class(
                    clearance_value, person_width_m, sideways_width_m,
                ),
                "passability_basis": (
                    f"direct_{person_width_m:.2f}m_sideways_{sideways_width_m:.2f}m"
                ),
            })
    pair_distances.sort(key=lambda item: item["clearance_m"])

    return {
        "schema_version": "1.0",
        "analysis_basis": "existing_2d_structure_only",
        "coordinate_unit": "meter",
        "resolution_m": cell,
        "analysis_profile": {
            "person_width_m": person_width_m,
            "person_radius_m": person_radius_m,
            "direct_pass_width_m": person_width_m,
            "sideways_pass_width_m": sideways_width_m,
            "profile_type": "configurable_geometric_passability_not_safety_standard",
        },
        "status": status,
        "reason": reason,
        "primary_route": {
            "id": "door_to_bed",
            "from": door.get("id") if door else None,
            "to": bed.get("instance_id") if bed else None,
            "path_exists": bool(path_cells),
            "path_blocked": not bool(path_cells),
            "path_length_m": round(float(path_length), 3) if path_length is not None else None,
            "minimum_clear_width_m": round(float(min_width), 3) if min_width is not None else None,
            "can_person_pass": bool(path_cells and person_path_found),
            "geometric_passage_class": _geometric_passage_class(
                min_width, person_width_m, sideways_width_m,
            ),
            "passability_basis": (
                f"direct_{person_width_m:.2f}m_sideways_{sideways_width_m:.2f}m"
            ),
            "narrowest_point_xy": narrowest_xy,
            "path_xy": path_xy,
            "requirement_status": "pending_rule_definition",
        },
        "walkable_regions": {
            "door_connected_area_m2": round(float(door_connected.sum()) * cell * cell, 3),
            "minimum_required_clearance_m": person_radius_m,
            "basis": "2d_structure_occupancy_eroded_by_person_radius",
        },
        "furniture_clearances": pair_distances,
        "risk_scoring_included": False,
    }


def build_spatial_foundation(measurements: dict, structure: dict, passage: dict) -> dict:
    room, _, _, objects, openings = _prepare_inputs(measurements, structure)
    doors = []
    windows = []
    for item in openings:
        record = {
            "id": item.get("id"), "position_xyz": item.get("center"),
            "width_m": item.get("width_m"), "height_m": item.get("height_m"),
            "rotation_z_deg": item.get("rotation_z_deg", 0.0),
            "measurement_status": item.get("measurement_status"),
            "confidence": item.get("confidence"),
        }
        (doors if item.get("type") == "door" else windows).append(record)

    furniture = []
    for item in objects:
        furniture.append({
            "id": item.get("instance_id") or item.get("id"),
            "type": item.get("type") or item.get("label"),
            "position_xyz": item.get("center"),
            "length_m": item.get("length_m"), "width_m": item.get("width_m"),
            "height_m": item.get("height_m"), "rotation_z_deg": item.get("rotation_z_deg", 0.0),
            "measurement_status": item.get("measurement_status"),
            "confidence": item.get("confidence"),
        })

    route = passage.get("primary_route") or {}
    return {
        "schema_version": "1.0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "coordinate_system": {"unit": "meter", "horizontal_axes": ["x", "y"], "vertical_axis": "z"},
        "room": {
            "length_m": room.get("length_m"), "width_m": room.get("width_m"),
            "height_m": room.get("height_m"),
            "area_m2": round(float(room.get("length_m")) * float(room.get("width_m")), 3)
            if room.get("length_m") is not None and room.get("width_m") is not None else None,
            "floor_polygon": (structure.get("room") or {}).get("floor_polygon"),
            "measurement_status": room.get("measurement_status"), "confidence": room.get("confidence"),
        },
        "doors": doors,
        "windows": windows,
        "furniture": furniture,
        "passages": [route],
        "clearances": passage.get("furniture_clearances", []),
        "risk_input_metrics": {
            "passage": {
                "minimum_width_m": route.get("minimum_clear_width_m"),
                "length_m": route.get("path_length_m"),
                "path_exists": route.get("path_exists"),
                "path_blocked": route.get("path_blocked"),
                "can_person_pass": route.get("can_person_pass"),
                "passability_basis": route.get("passability_basis"),
                "requirement_status": "pending_rule_definition",
            },
            "door_regions": [{
                "door_id": item["id"], "width_m": item["width_m"], "height_m": item["height_m"],
                "obstruction_status": "derived_from_route" if route.get("from") == item["id"] else "not_analyzed",
            } for item in doors],
            "furniture_dimensions": [{
                "id": item["id"], "type": item["type"], "length_m": item["length_m"],
                "width_m": item["width_m"], "height_m": item["height_m"],
            } for item in furniture],
            "obstacle_metrics": {
                "path_obstruction_detected": route.get("path_blocked"),
                "collision_risk": "not_scored",
            },
        },
        "scope": {
            "provides_measurements_only": True,
            "risk_rules_included": False,
            "risk_score_included": False,
            "final_safety_level_included": False,
        },
    }


def render_passage_analysis(
    measurements: dict, structure: dict, passage: dict, output_png: Path
) -> Path:
    room, minimum, maximum, objects, openings = _prepare_inputs(measurements, structure)
    fig, ax = plt.subplots(figsize=(10, 8), dpi=130)
    ax.set_facecolor("#f7f7f3")
    floor = (structure.get("room") or {}).get("floor_polygon") or [
        [minimum[0], minimum[1]], [maximum[0], minimum[1]],
        [maximum[0], maximum[1]], [minimum[0], maximum[1]],
    ]
    ax.add_patch(Polygon([[p[0], p[1]] for p in floor], closed=True,
                         facecolor="#edf4ef", edgecolor="#273444", linewidth=2.4))

    # 按结构家具占用和配置的人体半宽绘制“真正可走”的门连通区域。
    cell = float(passage.get("resolution_m") or 0.04)
    profile = passage.get("analysis_profile") or {}
    person_width = float(profile.get("direct_pass_width_m") or DEFAULT_DIRECT_PASS_WIDTH_M)
    sideways_width = float(profile.get("sideways_pass_width_m") or DEFAULT_SIDEWAYS_PASS_WIDTH_M)
    shape = np.maximum(np.ceil((maximum - minimum) / cell).astype(int), 3)
    occupied = np.zeros((int(shape[1]), int(shape[0])), dtype=bool)
    occupied[[0, -1], :] = True
    occupied[:, [0, -1]] = True
    for item in objects:
        _rasterize_rect(occupied, minimum, cell, item)
    door_item = next((item for item in openings if item.get("type") == "door"), None)
    seed = None
    if door_item:
        door_xy = np.asarray(door_item["center"], dtype=float)[:2]
        room_center = (minimum + maximum) / 2
        inward = room_center - door_xy
        inward /= max(float(np.linalg.norm(inward)), 1e-9)
        # 展示图沿用分析结果的入口内侧种子。
        seed = _cell_of(door_xy + inward * 0.18, minimum, cell, occupied.shape)
    clearance_grid = distance_transform_edt(~occupied) * cell
    person_free = (~occupied) & (clearance_grid >= person_width / 2)
    seed = _nearest_free(seed, person_free, 15) if seed is not None else None
    connected = _connected_component(person_free, seed)
    overlay = np.ma.masked_where(~connected, connected.astype(float))
    ax.imshow(
        overlay,
        origin="lower",
        extent=[minimum[0], minimum[0] + occupied.shape[1] * cell,
                minimum[1], minimum[1] + occupied.shape[0] * cell],
        cmap=matplotlib.colors.ListedColormap(["#72d68c"]),
        alpha=0.34,
        interpolation="nearest",
        zorder=1,
    )

    display_names = _display_names(objects)
    for item in objects:
        center, size, yaw = _object_rect(item)
        points = _corners(center, size, yaw)
        ax.add_patch(Polygon(points, closed=True, facecolor="#f2c14e",
                             edgecolor="#9b6b00", linewidth=1.3, alpha=0.78))
        name = display_names.get(str(item.get("instance_id") or item.get("id")), _object_name(item))
        ax.text(center[0], center[1], f"{name}\n{size[0]:.2f}×{size[1]:.2f}m",
                ha="center", va="center", fontsize=8, color="#332500")

    for item in openings:
        center = np.asarray(item["center"], dtype=float)[:2]
        width = float(item.get("width_m") or 0.8)
        kind = item.get("type")
        color = "#1976d2" if kind == "door" else "#2e8b57"
        ax.add_patch(Rectangle((center[0] - width / 2, center[1] - 0.035), width, 0.07,
                               facecolor=color, edgecolor="white", linewidth=1.0))
        ax.text(center[0], center[1] + 0.11, f"{'门' if kind == 'door' else '窗'} {width:.2f}m",
                ha="center", fontsize=8, color=color)

    route = passage.get("primary_route") or {}
    path = np.asarray(route.get("path_xy") or [], dtype=float)
    if len(path):
        ax.plot(path[:, 0], path[:, 1], color="#d62828", linewidth=2.8,
                marker=".", markersize=2.5, label="门到床主要通道")
    narrow = route.get("narrowest_point_xy")
    if narrow and route.get("minimum_clear_width_m") is not None:
        ax.scatter([narrow[0]], [narrow[1]], s=55, color="#7b2cbf", zorder=8)
        ax.annotate(f"最小净宽 {route['minimum_clear_width_m']:.2f}m", xy=narrow,
                    xytext=(10, -18), textcoords="offset points", fontsize=8.5, color="#5a189a")

    length = route.get("path_length_m")
    width = route.get("minimum_clear_width_m")
    passage_labels = {
        "normal_pass": "正常通过", "sideways_pass": "侧身通过",
        "not_passable": "几何不可过", "unknown": "未知",
    }
    pass_text = passage_labels.get(route.get("geometric_passage_class"), "未知")
    summary = (
        f"主要通道：门→床\n路径长度：{length:.2f}m\n沿途最小净宽：{width:.2f}m\n"
        f"几何分级：{pass_text}（直行≥{person_width:.2f}m，侧身≥{sideways_width:.2f}m）"
        if length is not None and width is not None
        else "主要通道：当前结构中不可达或数据不足"
    )
    ax.text(0.015, 0.985, summary, transform=ax.transAxes, ha="left", va="top",
            fontsize=10, bbox={"boxstyle": "round,pad=0.5", "fc": "white", "ec": "#68778d", "alpha": 0.92})
    all_clearances = [
        item for item in passage.get("furniture_clearances", [])
        if item.get("geometry_relation") == "separated"
    ]
    bed_clearances = [item for item in all_clearances if any(label.startswith("床") for label in item["between_labels"])]
    remaining = [item for item in all_clearances if item not in bed_clearances]
    clearances = (bed_clearances + remaining)[:7]
    if clearances:
        details = "关键家具净距\n" + "\n".join(
            f"{item['between_labels'][0]} - {item['between_labels'][1]}：{item['clearance_m']:.2f}m "
            f"({passage_labels.get(item.get('geometric_passage_class'), '未知')})"
            for item in clearances
        )
        ax.text(0.985, 0.985, details, transform=ax.transAxes, ha="right", va="top",
                fontsize=8.5, bbox={"boxstyle": "round,pad=0.45", "fc": "white", "ec": "#68778d", "alpha": 0.92})
    ax.text(0.015, 0.02, "绿色=按0.45m直行轮廓可达区域；分级仅描述几何通行，不包含风险评分或安全等级",
            transform=ax.transAxes, fontsize=8.5, color="#6b7280")
    ax.set_xlim(minimum[0] - 0.45, maximum[0] + 0.45)
    ax.set_ylim(minimum[1] - 0.45, maximum[1] + 0.45)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()
    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, facecolor="#f7f7f3")
    plt.close(fig)
    return output_png


def build_space_foundation_files(
    measurements_json: Path,
    structure_json: Path,
    output_dir: Path,
) -> dict:
    measurements_json, structure_json = Path(measurements_json), Path(structure_json)
    measurements, structure = _load(measurements_json), _load(structure_json)
    passage = analyze_structure_passages(measurements, structure)
    foundation = build_spatial_foundation(measurements, structure, passage)
    provenance = {
        "measurements_sha256": _sha256(measurements_json),
        "structure_sha256": _sha256(structure_json),
        "inputs_modified": False,
    }
    passage["provenance"] = provenance
    foundation["provenance"] = provenance
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    passage_path = output_dir / "passage_analysis.json"
    foundation_path = output_dir / "spatial_foundation.json"
    figure_path = output_dir / "passage_analysis.png"
    passage_path.write_text(json.dumps(passage, ensure_ascii=False, indent=2), encoding="utf-8")
    foundation_path.write_text(json.dumps(foundation, ensure_ascii=False, indent=2), encoding="utf-8")
    render_passage_analysis(measurements, structure, passage, figure_path)
    return {
        "passage_analysis": passage_path,
        "spatial_foundation": foundation_path,
        "passage_figure": figure_path,
        "result": passage,
    }
