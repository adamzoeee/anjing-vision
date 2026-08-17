"""passage_metrics 测试：合成房间验证占用图/路径/Clearance/门槛/坡度。"""
import numpy as np
import pytest

from pipeline.passage_metrics import (
    PassageReport,
    analyze_passage,
    clearance_along_path,
    corridor_clearance,
    floor_occupancy,
    measure_slope,
    measure_threshold,
    passage_to,
)


def _grid(rng, x_range, y_range, z_range, step=0.05, density=0.5):
    xs = np.arange(x_range[0], x_range[1], step)
    ys = np.arange(y_range[0], y_range[1], step)
    zs = np.arange(z_range[0], z_range[1], step)
    points = np.array([[x, y, z] for x in xs for y in ys for z in zs], dtype=np.float64)
    keep = rng.random(len(points)) < density
    return points[keep]


def _synthetic_room():
    """6x4m 房间：右墙门洞 0.9m，左侧柜子，走廊一个纸箱，深处一张床（目标）。"""
    rng = np.random.default_rng(11)
    parts = []
    door_ids: list[int] = []
    obstacle_ids: list[int] = []

    def add(points):
        start = sum(len(p) for p in parts)
        parts.append(points)
        return list(range(start, start + len(points)))

    add(_grid(rng, (-3, 3), (-2, 2), (0.0, 0.01), density=0.5))  # 地面
    for x_wall in (-3.0, 3.0):
        pts = []
        for y in np.arange(-2, 2, 0.05):
            for z in np.arange(0.05, 2.2, 0.05):
                # 门洞范围放宽 1cm，规避 np.arange 浮点边界（0.4500000000000002 > 0.45）
                if x_wall == 3.0 and -0.46 <= y <= 0.46 and z <= 2.0:
                    continue  # 门洞
                if rng.random() < 0.5:
                    pts.append([x_wall, y, z])
        add(np.asarray(pts, dtype=np.float64))
    for y_wall in (-2.0, 2.0):
        pts = [[x, y_wall, z] for x in np.arange(-3, 3, 0.05)
               for z in np.arange(0.05, 2.2, 0.05) if rng.random() < 0.5]
        add(np.asarray(pts, dtype=np.float64))
    door_pts = [[3.0, y, z] for y in (-0.45, 0.45) for z in np.arange(0.05, 2.0, 0.05)]
    door_ids.extend(add(np.asarray(door_pts, dtype=np.float64)))
    add(_grid(rng, (-2.6, -2.2), (-0.5, 0.5), (0.05, 1.8), density=0.6))  # 柜子
    obstacle_ids.extend(add(_grid(rng, (0.6, 0.9), (-0.2, 0.2), (0.05, 0.6), density=0.6)))  # 走廊纸箱
    target = _grid(rng, (-2.4, -1.8), (0.6, 1.2), (0.05, 0.5), density=0.6)  # 床（目标）
    points = np.vstack(parts + [target])
    return points, door_ids, obstacle_ids, target


def test_floor_occupancy_doorway_free_wall_occupied():
    points, door_ids, obstacle_ids, _ = _synthetic_room()
    exclude = np.unique(np.concatenate([door_ids, obstacle_ids]))
    grid, origin, cell = floor_occupancy(points, exclude_ids=exclude)
    door_col = int(round((3.0 - origin[0]) / cell))
    door_row = int(round((0.0 - origin[1]) / cell))
    assert not grid[door_row, door_col]  # 门洞自由
    wall_row = int(round((-0.8 - origin[1]) / cell))
    assert grid[wall_row, door_col]  # 门洞外墙体占用


def test_passage_to_finds_path_door_to_bed():
    points, door_ids, obstacle_ids, target = _synthetic_room()
    exclude = np.unique(np.concatenate([door_ids, obstacle_ids]))
    grid, origin, cell = floor_occupancy(points, exclude_ids=exclude)
    door_xy = points[door_ids][:, :2]
    path = passage_to(grid, origin, cell, door_xy, target[:, :2])
    assert path is not None and len(path) > 10


def test_clearance_narrowest_is_doorway():
    points, door_ids, obstacle_ids, target = _synthetic_room()
    exclude = np.unique(np.concatenate([door_ids, obstacle_ids]))
    # 目标点（床）自身不是结构，需排除，否则通道被目的地堵死
    target_ids = np.arange(len(points) - len(target), len(points))
    exclude = np.unique(np.concatenate([exclude, target_ids]))
    grid, origin, cell = floor_occupancy(points, exclude_ids=exclude)
    width, point = corridor_clearance(grid, origin, cell, points[door_ids][:, :2], target[:, :2])
    assert width is not None
    assert 0.75 <= width <= 1.15  # 门洞 0.9m 是最窄处
    assert point is not None and len(point) == 3


def test_analyze_passage_full_report():
    points, door_ids, obstacle_ids, target = _synthetic_room()
    exclude = np.unique(np.concatenate([door_ids, obstacle_ids]))
    target_ids = np.arange(len(points) - len(target), len(points))
    report = analyze_passage(
        points, points[door_ids], target, exclude_ids=exclude,
        target_ids=target_ids, cell_size=0.05,
    )
    assert isinstance(report, PassageReport)
    assert report.status == "ok"
    assert report.passage_width_m is not None and 0.75 <= report.passage_width_m <= 1.15
    assert report.path_3d and report.path_length_m
    assert report.threshold_m == 0.0  # 无门槛 → 确认 0
    assert report.stairs_exist is False
    assert report.slope is not None and report.slope < 0.05


def test_measure_threshold_detects_step():
    rng = np.random.default_rng(2)
    ground = np.c_[rng.uniform(1.9, 2.0, 300), rng.uniform(-0.4, 0.4, 300), np.zeros(300)]
    frame = np.c_[rng.uniform(1.9, 2.0, 300), rng.uniform(-0.4, 0.4, 300),
                  rng.uniform(0.02, 2.0, 300)]
    step = np.c_[rng.uniform(1.9, 2.0, 300), rng.uniform(-0.3, 0.3, 300),
                 0.05 + rng.normal(0, 0.002, 300)]
    height = measure_threshold(np.vstack([ground, frame, step]))
    assert abs(height - 0.05) < 0.02


def test_measure_slope_flat_and_tilted():
    rng = np.random.default_rng(4)
    flat = np.c_[rng.uniform(-2, 2, 500), rng.uniform(-1.5, 1.5, 500), np.zeros(500)]
    assert measure_slope(flat) < 1e-3
    tilted = flat.copy()
    tilted[:, 2] = 0.05 * tilted[:, 0]
    slope = measure_slope(tilted)
    assert abs(slope - 0.05) < 0.01


def test_analyze_passage_pending_without_door():
    points, _, _, target = _synthetic_room()
    report = analyze_passage(points, np.zeros((0, 3)), target)
    assert report.status == "pending"
    assert report.reason
