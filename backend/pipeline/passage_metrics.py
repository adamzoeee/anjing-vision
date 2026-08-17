"""通路测量（第三阶段 A 类几何风险）：占用图 + 通行路径 + Clearance + 门槛/台阶/坡度。

输入为房间坐标系点云（z 向上、地面 z≈0，米制单位优先，相对单位亦可——
测量结果单位跟随输入）。与 spatial_measurement 的 RoomFrame 约定一致。

模块职责：
1. ``floor_occupancy``：地面以上结构（墙/家具）2D 栅格化 → 占用图；
2. ``passage_to``：从门出发到目标（床/活动区/出口）的最短可行路径（BFS）；
3. ``clearance_along_path``：沿路径逐点测自由空间宽度，最窄处 = 通道净宽；
4. ``measure_threshold``：门区地面分层直方图 → 门槛高度/台阶；
5. ``measure_slope``：地面内点平面法向 → 坡度；
6. ``analyze_passage``：组合入口，一次输出全部通路指标。

纯 numpy 实现（无新依赖），不 import 业务模块，与运行中管道隔离。
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

OBSTACLE_LABELS = frozenset({"纸箱", "杂物", "水桶", "收纳箱", "行李箱", "宠物", "椅子"})


@dataclass
class PassageReport:
    """通路测量结果。"""
    passage_width_m: float | None = None        # 通道净宽（最窄处自由宽度）
    narrowest_point: list[float] | None = None  # 最窄处 3D 坐标
    path_length_m: float | None = None          # 门→目标路径长度
    path_3d: list[list[float]] = field(default_factory=list)  # 路径折线（3D）
    threshold_m: float | None = None            # 门槛高度；None=未检测到/不可测
    stairs_exist: bool | None = None            # 是否存在台阶（>=0.3m 视为台阶）
    slope: float | None = None                  # 地面坡度（法向与 z 夹角正切）
    status: str = "pending"                     # ok | pending（数据不足）
    reason: str = ""


def _to_xy(points_room: np.ndarray) -> np.ndarray:
    return np.asarray(points_room, dtype=np.float64).reshape(-1, 3)[:, :2]


def floor_occupancy(
    points_room: np.ndarray,
    *,
    cell_size: float = 0.05,
    z_band: tuple[float, float] = (0.10, 1.80),
    exclude_ids: np.ndarray | None = None,
) -> tuple[np.ndarray, np.ndarray, float]:
    """地面以上 z_band 内的点 → 2D 占用栅格（True=被墙/家具占据）。

    exclude_ids：需排除的点索引（门点、临时障碍物点——它们不是静态结构）。
    返回 (grid, origin(x0,y0), cell_size)。
    """
    points_room = np.asarray(points_room, dtype=np.float64).reshape(-1, 3)
    if len(points_room) < 10 or cell_size <= 0:
        raise ValueError("点数过少或 cell_size 非法")
    keep = np.ones(len(points_room), dtype=bool)
    if exclude_ids is not None and len(exclude_ids):
        keep[np.asarray(exclude_ids, dtype=int)] = False
    z_lo, z_hi = z_band
    sel = points_room[keep & (points_room[:, 2] >= z_lo) & (points_room[:, 2] <= z_hi)]
    if len(sel) == 0:
        return np.zeros((1, 1), dtype=bool), np.zeros(2), float(cell_size)
    x0, y0 = np.floor(sel[:, :2].min(axis=0) / cell_size) * cell_size
    cols = np.floor((sel[:, 0] - x0) / cell_size).astype(np.int64)
    rows = np.floor((sel[:, 1] - y0) / cell_size).astype(np.int64)
    grid = np.zeros((int(rows.max()) + 1, int(cols.max()) + 1), dtype=bool)
    grid[rows, cols] = True
    return grid, np.array([x0, y0], dtype=np.float64), float(cell_size)


def _cells_of(points_xy: np.ndarray, origin, cell_size, shape) -> list[tuple[int, int]]:
    cols = np.floor((points_xy[:, 0] - origin[0]) / cell_size).astype(np.int64)
    rows = np.floor((points_xy[:, 1] - origin[1]) / cell_size).astype(np.int64)
    height, width = shape
    inside = (cols >= 0) & (cols < width) & (rows >= 0) & (rows < height)
    return list(set(zip(rows[inside].tolist(), cols[inside].tolist())))


def passage_to(
    grid: np.ndarray,
    origin: np.ndarray,
    cell_size: float,
    door_xy: np.ndarray,
    target_xy: np.ndarray,
) -> list[tuple[int, int]] | None:
    """门 → 目标的 8 邻域 BFS 最短可行路径（栅格坐标折线）。

    door_xy/target_xy：各自点集 (M,2)/(K,2)，取各自中心格为起终点。
    返回路径单元列表 [(row, col), ...]；不可达返回 None。
    """
    grid = np.asarray(grid, dtype=bool)
    if grid.size <= 1:
        return None
    starts = _cells_of(np.asarray(door_xy).reshape(-1, 2), origin, cell_size, grid.shape)
    goals = _cells_of(np.asarray(target_xy).reshape(-1, 2), origin, cell_size, grid.shape)
    if not starts or not goals:
        return None
    # 起终点落在被占格上时，就近取自由邻格
    free = ~grid
    starts = [c for c in starts if free[c]] or _free_neighbors(starts, free)
    goals = [c for c in goals if free[c]] or _free_neighbors(goals, free)
    if not starts or not goals:
        return None
    start = starts[0]
    goal = min(goals, key=lambda c: (c[0] - start[0]) ** 2 + (c[1] - start[1]) ** 2)
    height, width = grid.shape
    parent = {start: None}
    queue = deque([start])
    while queue:
        current = queue.popleft()
        if current == goal:
            break
        row, col = current
        for d_row in (-1, 0, 1):
            for d_col in (-1, 0, 1):
                if d_row == 0 and d_col == 0:
                    continue
                rr, cc = row + d_row, col + d_col
                if not (0 <= rr < height and 0 <= cc < width):
                    continue
                if (rr, cc) in parent:
                    continue
                if grid[rr, cc]:
                    continue
                parent[(rr, cc)] = current
                queue.append((rr, cc))
    if goal not in parent:
        return None
    path = []
    node = goal
    while node is not None:
        path.append(node)
        node = parent[node]
    return path[::-1]


def _free_neighbors(cells, free_mask):
    neighbors = set()
    height, width = free_mask.shape
    for row, col in cells:
        for d_row in (-1, 0, 1):
            for d_col in (-1, 0, 1):
                rr, cc = row + d_row, col + d_col
                if 0 <= rr < height and 0 <= cc < width and free_mask[rr, cc]:
                    neighbors.add((rr, cc))
    return list(neighbors)


def clearance_along_path(
    grid: np.ndarray, path: list[tuple[int, int]], cell_size: float
) -> tuple[float | None, int | None]:
    """沿路径逐点测"垂直于路径方向"的自由连续宽度，返回 (最窄宽度, 最窄点索引)。

    宽度 = 路径点两侧沿法向延伸的自由格数 × cell_size；最窄处即通道净宽。
    """
    grid = np.asarray(grid, dtype=bool)
    free = ~grid
    if len(path) < 2:
        return None, None
    narrowest: float | None = None
    narrowest_index: int | None = None
    # 平滑窗口：8 邻域 BFS 的对角锯齿会让单步方向噪声很大，取前后邻点平均方向
    smooth = 3
    for index in range(len(path)):
        head = min(index + smooth, len(path) - 1)
        tail = max(index - smooth, 0)
        if head == tail:
            continue
        direction = np.asarray(path[head], dtype=float) - np.asarray(path[tail], dtype=float)
        norm = float(np.linalg.norm(direction))
        if norm < 1e-9:
            continue
        perp = np.array([-direction[1], direction[0]]) / norm
        width_cells = 0
        for sign in (1.0, -1.0):
            step = 1
            while step < 200:
                rr = int(round(path[index][0] + sign * perp[0] * step))
                cc = int(round(path[index][1] + sign * perp[1] * step))
                if 0 <= rr < grid.shape[0] and 0 <= cc < grid.shape[1] and free[rr, cc]:
                    width_cells += 1
                    step += 1
                else:
                    break
        width = float(width_cells) * cell_size
        if narrowest is None or width < narrowest:
            narrowest, narrowest_index = width, index
    return narrowest, narrowest_index


def measure_threshold(
    door_points_room: np.ndarray, *, max_height: float = 0.5, bin_m: float = 0.01
) -> float:
    """门区门槛/台阶高度：地面之上分层直方图最高密度层中心。

    door_points_room：门区点（房间坐标系）。无显著抬高层时返回 0.0。
    """
    points = np.asarray(door_points_room, dtype=np.float64).reshape(-1, 3)
    if len(points) < 20:
        return 0.0
    # 门区点通常不含地面，z0 取最低 5 分位（门柱底端≈地面高度）
    z0 = float(np.percentile(points[:, 2], 5))
    rel = points[:, 2] - z0
    cand = points[(rel > 0.005) & (rel < max_height)]
    if len(cand) < 10:
        return 0.0
    edges = np.arange(0.0, max_height + bin_m, bin_m)
    hist, _ = np.histogram(cand[:, 2] - z0, bins=edges)
    best = int(np.argmax(hist))
    height = float(edges[best] + bin_m / 2.0)
    return 0.0 if height < 0.01 else height  # <1cm 视为无门槛（直方图噪声）


def measure_slope(ground_points: np.ndarray) -> float | None:
    """地面坡度：最小二乘平面法向与 z 轴夹角的正切。"""
    points = np.asarray(ground_points, dtype=np.float64).reshape(-1, 3)
    if len(points) < 10:
        return None
    centroid = points.mean(axis=0)
    centered = points - centroid
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    normal = vt[2]
    normal = normal / max(np.linalg.norm(normal), 1e-12)
    if abs(normal[2]) < 1e-6:
        return None
    return float(np.hypot(normal[0], normal[1]) / abs(normal[2]))


def corridor_clearance(
    grid: np.ndarray,
    origin: np.ndarray,
    cell_size: float,
    door_xy: np.ndarray,
    target_xy: np.ndarray,
) -> tuple[float | None, np.ndarray | None]:
    """门 → 目标主轴走廊的最窄自由宽度（通道净宽）。

    沿门中心到目标中心的连线逐点取扫描线（垂直于主轴），两侧自由延伸之和即
    该处宽度；最窄处 = 通道净宽。不依赖 BFS 路径形状，避免对角 zigzag 失真。
    """
    grid = np.asarray(grid, dtype=bool)
    free = ~grid
    door_cells = _cells_of(np.asarray(door_xy).reshape(-1, 2), origin, cell_size, grid.shape)
    target_cells = _cells_of(np.asarray(target_xy).reshape(-1, 2), origin, cell_size, grid.shape)
    if not door_cells or not target_cells:
        return None, None
    start = np.asarray(door_cells).mean(axis=0)
    goal = np.asarray(target_cells).mean(axis=0)
    axis = goal - start
    length = float(np.linalg.norm(axis))
    if length < 1e-9:
        return None, None
    axis_u = axis / length
    # perp 量化为与主轴正交的坐标轴：门/墙通常与坐标轴对齐，
    # 斜向 perp 会在门洞处斜穿出界，导致宽度测量失真。
    perp = np.array([1.0, 0.0]) if abs(axis_u[1]) > abs(axis_u[0]) else np.array([0.0, 1.0])
    best_width: float | None = None
    best_point: np.ndarray | None = None
    for t in range(int(length) + 1):
        base = np.array([
            int(round(start[0] + axis_u[0] * t)),
            int(round(start[1] + axis_u[1] * t)),
        ])
        # 沿 perp 方向采样 ±400 格，切分自由连通段
        segments: list[tuple[int, int]] = []
        run: list[int] = []
        for s in range(-400, 401):
            rr = int(round(base[0] + perp[0] * s))
            cc = int(round(base[1] + perp[1] * s))
            is_free = (
                0 <= rr < grid.shape[0]
                and 0 <= cc < grid.shape[1]
                and free[rr, cc]
            )
            if is_free:
                run.append(s)
            else:
                if run:
                    segments.append((run[0], run[-1]))
                run = []
        if run:
            segments.append((run[0], run[-1]))
        if not segments:
            continue
        # base 自由 → 取包含 base 的段；被占（如落在目标物体内）→ 取最近段
        if any(lo <= 0 <= hi for lo, hi in segments):
            seg = next((lo, hi) for lo, hi in segments if lo <= 0 <= hi)
        else:
            seg = min(segments, key=lambda pair: min(abs(pair[0]), abs(pair[1])))
        width = float(seg[1] - seg[0] + 1) * cell_size
        if best_width is None or width < best_width:
            best_width = width
            best_point = np.array([
                origin[0] + base[1] * cell_size,
                origin[1] + base[0] * cell_size,
                0.0,
            ])
    return best_width, best_point


def analyze_passage(
    points_room: np.ndarray,
    door_points_room: np.ndarray,
    target_points_room: np.ndarray,
    *,
    ground_inliers: np.ndarray | None = None,
    exclude_ids: np.ndarray | None = None,
    target_ids: np.ndarray | None = None,
    cell_size: float = 0.05,
) -> PassageReport:
    """通路测量组合入口。

    points_room：全部房间坐标点（米）；door_points_room：门区点；
    target_points_room：通行目标点（床/活动区质心/出口）；
    target_ids：目标点的全局索引（目标本身不是结构，需从占用图排除，
    否则通道被"目的地"自身堵死）；
    ground_inliers：地面内点索引（坡度测量用，缺省取 z 最低 30% 点）。
    """
    report = PassageReport()
    points = np.asarray(points_room, dtype=np.float64).reshape(-1, 3)
    door_pts = np.asarray(door_points_room, dtype=np.float64).reshape(-1, 3)
    target_pts = np.asarray(target_points_room, dtype=np.float64).reshape(-1, 3)
    if len(points) < 50 or len(door_pts) < 3 or len(target_pts) < 3:
        report.status = "pending"
        report.reason = "门/目标/场景点数不足"
        return report

    try:
        exclude = np.asarray(exclude_ids, dtype=int) if exclude_ids is not None else np.zeros(0, dtype=int)
        if target_ids is not None:
            exclude = np.unique(np.concatenate([exclude, np.asarray(target_ids, dtype=int)]))
        grid, origin, cell = floor_occupancy(points, cell_size=cell_size, exclude_ids=exclude)
        path = passage_to(grid, origin, cell, door_pts[:, :2], target_pts[:, :2])
        if path is None:
            report.status = "pending"
            report.reason = "门与目标之间无可达路径"
            return report
        width, narrow_point = corridor_clearance(grid, origin, cell, door_pts[:, :2], target_pts[:, :2])
        report.passage_width_m = round(width, 3) if width is not None else None
        report.narrowest_point = (
            [float(v) for v in narrow_point] if narrow_point is not None else None
        )
        # 路径折线转 3D（z 取地面高度 0）
        report.path_3d = [
            [origin[0] + col * cell, origin[1] + row * cell, 0.0] for row, col in path
        ]
        report.path_length_m = round(len(path) * cell, 3)
        report.status = "ok"
    except (ValueError, IndexError) as exc:
        report.status = "pending"
        report.reason = f"通路分析失败: {exc}"
        return report

    # 门槛/台阶：门区点分层
    step = measure_threshold(door_pts)
    if step <= 0.0:
        report.threshold_m = 0.0
        report.stairs_exist = False
    elif step < 0.3:
        report.threshold_m = round(float(step), 3)
        report.stairs_exist = False
    else:
        report.threshold_m = None
        report.stairs_exist = True

    # 坡度：地面内点
    if ground_inliers is not None and len(ground_inliers) >= 10:
        report.slope = measure_slope(points[np.asarray(ground_inliers, dtype=int)])
    else:
        order = np.argsort(points[:, 2])
        lowest = points[order[: max(10, len(points) // 10)]]
        report.slope = measure_slope(lowest)
    if report.slope is not None:
        report.slope = round(report.slope, 4)
    return report
