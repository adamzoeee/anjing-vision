"""真实几何模拟引擎：从结构中的家具盒子重新计算全部正式空间指标。

原则：
- 只读 structure_calibrated.json（semantic_instances / objects / walls / doors / room）；
- 所有修改都在内存副本上执行，不写任何真实数据；
- 指标全部由盒子几何重新计算（栅格 + OBB 距离），不预设安全阈值；
- 评分交给 pipeline.risk_assessment 的正式体系。
"""
from __future__ import annotations

import math
from copy import deepcopy

import numpy as np

from pipeline.spatial_metrics import METRIC_DEFINITION_BY_CODE, build_metric

CELL = 0.05
DOOR_MIN_WIDTH = 0.90


def _obb_gap(a: dict, b: dict) -> float:
    """两个绕 z 旋转的 2D 矩形边缘最短距离（SAT）。"""
    def corners(item):
        c, s = math.cos(item["rot"]), math.sin(item["rot"])
        cx, cy = item["center"][0], item["center"][1]
        hx, hy = item["size"][0] / 2, item["size"][1] / 2
        # 角点必须按周界顺序排列，保证四条边都参与 SAT（否则只剩两条边+两条对角线）
        return np.array([
            [cx + sx * hx * c - sy * hy * s, cy + sx * hx * s + sy * hy * c]
            for sx, sy in ((-1, -1), (1, -1), (1, 1), (-1, 1))
        ])
    rects = [corners(a), corners(b)]
    separated, min_gap = False, float("inf")
    for rect in rects:
        for i in range(4):
            edge = rect[(i + 1) % 4] - rect[i]
            n = np.array([-edge[1], edge[0]], dtype=float)
            n /= max(np.linalg.norm(n), 1e-12)
            proj = [r @ n for r in rects]
            lo, hi = max(p.min() for p in proj), min(p.max() for p in proj)
            if lo > hi + 1e-9:
                separated = True
                min_gap = min(min_gap, float(lo - hi))
    return min_gap if separated else 0.0


def load_geometry(structure: dict, measurements: dict | None = None) -> dict:
    """从结构契约构建几何模型（家具/墙/门/房间，单位米）。"""
    # semantic_instances 的 label 常为 None，用 measurements.objects 的 id→type 补全
    type_by_id = {}
    if measurements:
        for item in measurements.get("objects", []):
            if item.get("id"):
                type_by_id[str(item["id"])] = item.get("type") or "object"
    objs = structure.get("semantic_instances") or structure.get("objects") or []
    furniture = []
    for item in objs:
        item_id = item.get("instance_id") or item.get("id") or f"obj_{len(furniture)}"
        label = item.get("label") or item.get("category") or type_by_id.get(str(item_id)) or "object"
        furniture.append({
            "id": item_id,
            "label": label,
            "center": np.asarray(item["center"], dtype=float).copy(),
            "size": np.asarray(item["size"], dtype=float).copy(),
            "rot": math.radians(float(item.get("rotation_z_deg") or 0.0)),
            "kind": "furniture",
        })
    walls = [{
        "id": f"wall_{w.get('id', i)}", "label": "wall",
        "center": np.asarray(w["center"], dtype=float).copy(),
        "size": np.asarray(w["size"], dtype=float).copy(),
        "rot": math.radians(float(w.get("rotation_z_deg") or 0.0)),
        "kind": "wall",
    } for i, w in enumerate(structure.get("walls", []))]
    obstacles = [{
        "id": o.get("instance_id") or f"box_{i}", "label": "box",
        "center": np.asarray(o["center"], dtype=float).copy(),
        "size": np.asarray(o["size"], dtype=float).copy(),
        "rot": math.radians(float(o.get("rotation_z_deg") or 0.0)),
        "kind": "obstacle",
    } for i, o in enumerate(structure.get("geometric_obstacles", []))]
    doors = structure.get("doors", [])
    door = doors[0] if doors else None
    room = structure.get("room", {})
    bounds = room.get("bounds_xy", {})
    lo = np.asarray(bounds.get("min", [0, 0]), dtype=float)
    hi = np.asarray(bounds.get("max", [1, 1]), dtype=float)
    return {
        "furniture": furniture, "walls": walls, "obstacles": obstacles,
        "door": door, "lo": lo, "hi": hi,
        "height": float(room.get("height_m") or 2.6),
    }


def _box_cells(box, origin, cell, shape) -> set:
    """把盒子 footprint 栅格化（含旋转）。

    采样范围必须覆盖旋转后的外接正方形（max(size)/2），
    否则 90° 旋转的盒子（桌子/书架/东西墙）只会栅格化出中间一小条。
    """
    half = max(box["size"][0], box["size"][1]) / 2
    xs = np.arange(box["center"][0] - half, box["center"][0] + half, cell / 2)
    ys = np.arange(box["center"][1] - half, box["center"][1] + half, cell / 2)
    gx, gy = np.meshgrid(xs, ys)
    pts = np.stack([gx.ravel(), gy.ravel()], axis=1) - box["center"][:2]
    c, s = math.cos(box["rot"]), math.sin(box["rot"])
    lx = np.abs(pts[:, 0] * c + pts[:, 1] * s)
    ly = np.abs(-pts[:, 0] * s + pts[:, 1] * c)
    keep = (lx <= box["size"][0] / 2) & (ly <= box["size"][1] / 2)
    cols = np.floor((gx.ravel()[keep] - origin[0]) / cell).astype(int)
    rows = np.floor((gy.ravel()[keep] - origin[1]) / cell).astype(int)
    valid = (cols >= 0) & (cols < shape[1]) & (rows >= 0) & (rows < shape[0])
    return {(int(r), int(c)) for r, c in zip(rows[valid], cols[valid])}


def build_grid(geo: dict) -> tuple[np.ndarray, np.ndarray, float]:
    lo, hi = geo["lo"], geo["hi"]
    origin = np.floor(lo / CELL) * CELL
    shape = (int(np.ceil((hi[1] - origin[1]) / CELL)), int(np.ceil((hi[0] - origin[0]) / CELL)))
    grid = np.zeros(shape, dtype=bool)
    for box in [*geo["walls"], *geo["furniture"], *geo["obstacles"]]:
        for r, c in _box_cells(box, origin, CELL, shape):
            grid[r, c] = True
    # 门洞开孔：把门宽度范围内的墙格释放，保证门→床通路成立
    for door in [geo.get("door"), *geo.get("doors", [])]:
        if not isinstance(door, dict):
            continue
        center = np.asarray(door["center"], dtype=float)
        size = np.asarray(door["size"], dtype=float)
        half_w, half_t = size[0] / 2 + CELL, size[1] / 2 + CELL
        c0 = int(np.floor((center[0] - half_w - origin[0]) / CELL))
        c1 = int(np.ceil((center[0] + half_w - origin[0]) / CELL))
        r0 = int(np.floor((center[1] - half_t - origin[1]) / CELL))
        r1 = int(np.ceil((center[1] + half_t - origin[1]) / CELL))
        grid[max(r0, 0):min(r1, shape[0]), max(c0, 0):min(c1, shape[1])] = False
    return grid, origin, CELL


def _bfs(grid, origin, start_xy, goal_xy, goal_box=None):
    """多源/多目标 BFS：起点取门洞占用区外沿，目标取床体盒子的自由外沿。

    目标必须用床盒子自身的栅格外沿（goal_box），不能对“床+相连墙体”的
    连通域取外沿，否则目标会膨胀到整个墙边（门洞旁也算），路径失真。
    """
    from collections import deque

    def cell_of(xy):
        return int((xy[1] - origin[1]) / CELL), int((xy[0] - origin[0]) / CELL)

    def neighbors(cell):
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                if dr == 0 and dc == 0:
                    continue
                r, c = cell[0] + dr, cell[1] + dc
                if 0 <= r < grid.shape[0] and 0 <= c < grid.shape[1]:
                    yield (r, c)

    def blocked_region(cell):
        seen = {cell}
        stack = [cell]
        while stack:
            cur = stack.pop()
            for nb in neighbors(cur):
                if grid[nb] and nb not in seen:
                    seen.add(nb)
                    stack.append(nb)
        return seen

    def free_rim(cell):
        region = blocked_region(cell)
        return {nb for cur in region for nb in neighbors(cur) if not grid[nb]}

    start_cell = cell_of(start_xy)
    goal_cell = cell_of(goal_xy)
    if not (0 <= start_cell[0] < grid.shape[0] and 0 <= start_cell[1] < grid.shape[1]):
        return None
    if not (0 <= goal_cell[0] < grid.shape[0] and 0 <= goal_cell[1] < grid.shape[1]):
        return None
    sources = {start_cell} if not grid[start_cell] else free_rim(start_cell)
    goals = None
    if goal_box is not None:
        goal_cells = _box_cells(goal_box, origin, CELL, grid.shape)
        if goal_cells:
            goals = {nb for cur in goal_cells for nb in neighbors(cur) if not grid[nb]}
    if not goals:
        goals = {goal_cell} if not grid[goal_cell] else free_rim(goal_cell)
    if not sources or not goals:
        return None
    parent = {s: None for s in sources}
    queue = deque(sources)
    while queue:
        cur = queue.popleft()
        if cur in goals:
            path = []
            node = cur
            while node is not None:
                path.append(node)
                node = parent[node]
            path.reverse()
            return [[origin[0] + c * CELL, origin[1] + r * CELL] for r, c in path]
        for nb in neighbors(cur):
            if grid[nb] or nb in parent:
                continue
            parent[nb] = cur
            queue.append(nb)
    return None


def _corridor_width(grid, origin, start_xy, goal_xy) -> tuple[float | None, list | None]:
    """沿门→床主轴扫描线的最窄自由宽度（与 passage_metrics 同思路）。"""
    if grid.size == 0:
        return None, None
    def cell_of(xy):
        return int((xy[1] - origin[1]) / CELL), int((xy[0] - origin[0]) / CELL)
    start = np.asarray(cell_of(start_xy), dtype=float)
    goal = np.asarray(cell_of(goal_xy), dtype=float)
    axis = goal - start
    length = float(np.linalg.norm(axis))
    if length < 1e-9:
        return None, None
    axis_u = axis / length
    perp = np.array([1.0, 0.0]) if abs(axis_u[1]) > abs(axis_u[0]) else np.array([0.0, 1.0])
    best = None
    best_point = None
    for t in range(int(length) + 1):
        base = np.array([round(start[0] + axis_u[0] * t), round(start[1] + axis_u[1] * t)])
        segments, run = [], []
        for s in range(-400, 401):
            rr = int(round(base[0] + perp[0] * s))
            cc = int(round(base[1] + perp[1] * s))
            free = 0 <= rr < grid.shape[0] and 0 <= cc < grid.shape[1] and not grid[rr, cc]
            if free:
                run.append(s)
            else:
                if run:
                    segments.append((run[0], run[-1]))
                run = []
        if run:
            segments.append((run[0], run[-1]))
        if not segments:
            continue
        if any(lo <= 0 <= hi for lo, hi in segments):
            seg = next((lo, hi) for lo, hi in segments if lo <= 0 <= hi)
        else:
            seg = min(segments, key=lambda pair: min(abs(pair[0]), abs(pair[1])))
        width = float(seg[1] - seg[0] + 1) * CELL
        if best is None or width < best:
            best = width
            best_point = [origin[0] + base[1] * CELL, origin[1] + base[0] * CELL, 0.0]
    return best, best_point


def _path_obstacle_squeeze(grid, origin, cell, path, obstacles) -> float | None:
    """路径沿线被放置物(箱子等)挤压出的最窄净宽；没有放置物时返回 None。"""
    if not path or not obstacles:
        return None
    from scipy.ndimage import distance_transform_edt
    obs = np.zeros(grid.shape, dtype=bool)
    for box in obstacles:
        for r, c in _box_cells(box, origin, cell, grid.shape):
            obs[r, c] = True
    if not obs.any():
        return None
    dist = distance_transform_edt(~obs) * cell  # 到最近放置物的距离(米)
    best = None
    for p in path:
        r = int((p[1] - origin[1]) / cell)
        c = int((p[0] - origin[0]) / cell)
        if not (0 <= r < dist.shape[0] and 0 <= c < dist.shape[1]):
            continue
        d = float(dist[r, c])
        if d < 2.0:  # 只统计放置物附近的挤压
            w = 2.0 * d
            if best is None or w < best:
                best = w
    return best


def _door_connected_area(grid, origin, cell, door_xy, person_radius: float = 0.225) -> float:
    """门连接可行走区域面积：先按人半径侵蚀再 4 邻域洪泛，与 passage_analysis 口径一致。"""
    from collections import deque
    from scipy.ndimage import distance_transform_edt
    start = (int((door_xy[1] - origin[1]) / cell), int((door_xy[0] - origin[0]) / cell))
    if not (0 <= start[0] < grid.shape[0] and 0 <= start[1] < grid.shape[1]):
        return 0.0
    free = ~grid
    dist = distance_transform_edt(free) * cell  # 自由格到最近占用格距离
    walkable = dist >= person_radius
    if walkable[start]:
        pass
    else:
        for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            nb = (start[0] + dr, start[1] + dc)
            if 0 <= nb[0] < grid.shape[0] and 0 <= nb[1] < grid.shape[1] and walkable[nb]:
                start = nb
                break
    if not walkable[start]:
        return 0.0
    seen = {start}
    queue = deque([start])
    while queue:
        r, c = queue.popleft()
        for dr, dc in ((0, 1), (1, 0), (0, -1), (-1, 0)):
            nb = (r + dr, c + dc)
            if nb in seen or not (0 <= nb[0] < grid.shape[0] and 0 <= nb[1] < grid.shape[1]):
                continue
            if not walkable[nb]:
                continue
            seen.add(nb)
            queue.append(nb)
    return len(seen) * cell * cell


def compute_metrics(geo: dict) -> list[dict]:
    """从当前几何重新计算全部正式空间指标（metric_record 列表）。"""
    metrics = []
    f = geo["furniture"]
    door = geo["door"]
    room_area = float((geo["hi"][0] - geo["lo"][0]) * (geo["hi"][1] - geo["lo"][1]))
    door_center = np.asarray(door["center"], dtype=float) if door else None

    def record(code, value, status="measured", position=None, reason=None):
        metrics.append(build_metric(
            code, value=value, status=status, confidence=0.9 if status != "not_evaluable" else None,
            position=position, source={"simulation": True}, reason=reason,
        ))

    # 门宽
    if door is not None:
        record("door_width", round(float(door["size"][0]), 3),
               position={"opening_id": door.get("instance_id")})
    # 家具间距 / 床侧净空 / 床周边 / 离墙
    gaps = []
    bed = next((b for b in f if b["label"] in {"bed", "床"}), None)
    bed_gaps = []
    wall_gaps = []
    for i in range(len(f)):
        for j in range(i + 1, len(f)):
            gap = _obb_gap(f[i], f[j])
            gaps.append((gap, f[i], f[j]))
        for w in geo["walls"]:
            g = _obb_gap(f[i], w)
            wall_gaps.append((g, f[i], w))
        if bed is not None and f[i] is not bed:
            bed_gaps.append((_obb_gap(bed, f[i]), f[i]))
    if gaps:
        gap, a, b = min(gaps, key=lambda t: t[0])
        record("furniture_spacing", round(gap, 3),
               position={"object_ids": [a["id"], b["id"]]})
    if bed is not None and bed_gaps:
        gap, other = min(bed_gaps, key=lambda t: t[0])
        record("bedside_clearance", round(gap, 3), position={"object_ids": [bed["id"], other["id"]]})
    if bed is not None:
        # 床周边最小净空：同时考虑其他家具与墙（与正式指标口径一致，贴墙即 0）
        bed_surround = min([g for g, _ in bed_gaps] +
                           [_obb_gap(bed, w) for w in geo["walls"]], default=0.0)
        record("bed_surrounding_space", round(bed_surround, 3),
               position={"object_ids": [bed["id"]]})
    if wall_gaps:
        gap, item, wall = min(wall_gaps, key=lambda t: t[0])
        record("wall_furniture_clearance", round(gap, 3),
               position={"object_ids": [item["id"]]})
    if bed is not None:
        bed_wall = min(_obb_gap(bed, w) for w in geo["walls"])
        record("bed_wall_distance", round(bed_wall, 3), position={"object_ids": [bed["id"]]})
    # 拥挤度 = 家具占地 / 房间面积
    furniture_area = sum(b["size"][0] * b["size"][1] for b in f)
    record("crowding", round(min(furniture_area / max(room_area, 1e-6), 1.0), 3))
    # 栅格通路
    grid, origin, cell = build_grid(geo)
    if door is not None and bed is not None:
        door_xy = door_center[:2] if door_center is not None else None
        bed_xy = bed["center"][:2]
        path = _bfs(grid, origin, door_xy, bed_xy, goal_box=bed)
        width, narrow = _corridor_width(grid, origin, door_xy, bed_xy)
        if path is not None:
            record("path_continuity", True)
            plen = 0.0
            for i in range(1, len(path)):
                plen += math.dist(path[i], path[i - 1])
            record("path_length", round(plen, 2),
                   position={"path_id": "door_to_bed"})
            # 路径障碍：路径沿线 0.3m 内有障碍物盒子，或被挤压到 0.3m 以下
            squeeze = _path_obstacle_squeeze(grid, origin, cell, path, geo["obstacles"])
            obstructed = any(
                o["label"] in {"box", "obstacle"} and min(
                    math.dist(p, o["center"][:2]) for p in path) < 0.30
                for o in geo["obstacles"]) or (squeeze is not None and squeeze < 0.30)
            record("path_obstruction", obstructed)
            record("main_activity_area_safety", not obstructed)
            width, narrow = _corridor_width(grid, origin, door_xy, bed_xy)
            if width is not None:
                if squeeze is not None:
                    width = min(width, squeeze)
                record("main_passage_width", round(width, 3), position={"narrowest": narrow})
                record("minimum_passage_width", round(width, 3), position={"narrowest": narrow})
        else:
            record("path_continuity", False)
            record("path_length", None, status="not_evaluable",
                   reason="door_to_bed_path_blocked")
            record("path_obstruction", True)
            record("main_activity_area_safety", False)
            record("main_passage_width", 0.0, reason="door_to_bed_path_blocked")
            record("minimum_passage_width", 0.0, reason="door_to_bed_path_blocked")
    # 入口可用空间：门连接可行走区域面积（与 passage_analysis 口径一致）
    if door is not None and door_center is not None:
        record("entrance_space",
               round(_door_connected_area(grid, origin, cell, door_center[:2]), 3))
    # 活动区：房间中央 60% 区域自由面积
    cx0 = geo["lo"][0] + 0.2 * (geo["hi"][0] - geo["lo"][0])
    cx1 = geo["lo"][0] + 0.8 * (geo["hi"][0] - geo["lo"][0])
    cy0 = geo["lo"][1] + 0.2 * (geo["hi"][1] - geo["lo"][1])
    cy1 = geo["lo"][1] + 0.8 * (geo["hi"][1] - geo["lo"][1])
    free_activity = 0
    for r in range(grid.shape[0]):
        for c in range(grid.shape[1]):
            if grid[r, c]:
                continue
            x, y = origin[0] + c * cell, origin[1] + r * cell
            if cx0 <= x <= cx1 and cy0 <= y <= cy1:
                free_activity += 1
    record("activity_area", round(free_activity * cell * cell, 2))
    return metrics


def payload_from_geometry(geo: dict) -> dict:
    from pipeline.spatial_metrics import build_metric_payload
    payload = build_metric_payload(compute_metrics(geo))
    payload["paths"] = []
    payload["scope"] = {"simulation": True, "raw_media_accessed": False}
    return payload


def move_box(box: dict, dx: float, dy: float) -> None:
    box["center"][0] += dx
    box["center"][1] += dy


def clamp_box_to_room(geo: dict, box: dict) -> None:
    """把盒子夹回房间边界内（不能穿墙）。"""
    hx, hy = box["size"][0] / 2, box["size"][1] / 2
    box["center"][0] = min(max(box["center"][0], geo["lo"][0] + hx), geo["hi"][0] - hx)
    box["center"][1] = min(max(box["center"][1], geo["lo"][1] + hy), geo["hi"][1] - hy)


def nearest_box(geo: dict, xy, *, exclude_kind=None) -> dict | None:
    best, best_dist = None, float("inf")
    for b in [*geo["furniture"], *geo["obstacles"]]:
        if exclude_kind and b["kind"] == exclude_kind:
            continue
        d = math.dist(b["center"][:2], xy)
        if d < best_dist:
            best, best_dist = b, d
    return best


def widen_door(geo: dict, target: float = DOOR_MIN_WIDTH) -> bool:
    door = geo["door"]
    if door is None or float(door["size"][0]) >= target:
        return False
    door = deepcopy(door)
    door["size"] = np.asarray([target, door["size"][1], door["size"][2]], dtype=float)
    geo["door"] = door
    return True


def remove_box(geo: dict, box: dict) -> bool:
    for collection in ("furniture", "obstacles"):
        for i, item in enumerate(geo[collection]):
            if item is box:
                geo[collection].pop(i)
                return True
    return False


def apply_ops(geo: dict, ops: list[dict]) -> dict:
    # op["box"] 引用的是复制前的盒子对象：先记下其所在集合与下标，再在副本中按位置解析
    loc = {}
    for coll in ("furniture", "obstacles"):
        for i, b in enumerate(geo[coll]):
            loc[id(b)] = (coll, i)
    geo = deepcopy(geo)

    def resolve(box):
        coll, i = loc[id(box)]
        return geo[coll][i]

    for op in ops:
        kind = op["op"]
        if kind == "move":
            target = resolve(op["box"])
            move_box(target, op["dx"], op["dy"])
            if op.get("clamp"):
                clamp_box_to_room(geo, target)
        elif kind == "move_away":
            target = resolve(op["box"])
            axis = np.asarray(op["away_from"], dtype=float) - target["center"][:2]
            norm = float(np.linalg.norm(axis)) or 1.0
            move_box(target, -axis[0] / norm * op["distance"], -axis[1] / norm * op["distance"])
            if op.get("clamp"):
                clamp_box_to_room(geo, target)
        elif kind == "add":
            geo["obstacles"].append({
                "id": f"sim_box_{len(geo['obstacles'])}", "label": "box",
                "center": np.asarray(op["center"], dtype=float).copy(),
                "size": np.asarray(op["size"], dtype=float).copy(),
                "rot": op.get("rot", 0.0), "kind": "obstacle",
            })
        elif kind == "remove":
            remove_box(geo, resolve(op["box"]))
        elif kind == "widen_door":
            widen_door(geo, op.get("target", DOOR_MIN_WIDTH))
    return geo
