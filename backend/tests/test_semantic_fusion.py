"""第二阶段合成场景测试：多视角语义融合、实例分离、RoomFrame 与稳健尺寸。

全部使用合成数据，不运行真实视频或大规模 3DGS 训练。
"""
import numpy as np
import pytest

from pipeline.semantic import (
    SemanticFusion,
    fuse_multiview_semantics,
    project_points_to_view,
)
from pipeline.spatial_measurement import (
    RoomFrame,
    build_semantic_objects,
    build_semantic_space,
    cluster_semantic_instances,
    estimate_room_frame,
    measure_room,
    merge_fragmented_clusters,
    rescale_semantic_space,
)


# ---------------------------------------------------------------------------
# 合成场景工具
# ---------------------------------------------------------------------------

def _camera(center, look_at=(0.0, 0.0, 1.0), up=(0.0, 0.0, 1.0), f=600.0,
            width=800, height=600, roll=0.0):
    """构造与 COLMAP 同约定的相机字典（cam = R @ world + t）。"""
    center = np.asarray(center, dtype=np.float64)
    forward = np.asarray(look_at, dtype=np.float64) - center
    forward /= np.linalg.norm(forward)
    up_vector = np.asarray(up, dtype=np.float64)
    right = np.cross(forward, up_vector)
    if np.linalg.norm(right) < 1e-9:  # 光轴与 up 平行时换参考向量
        up_vector = np.array([0.0, 1.0, 0.0])
        right = np.cross(forward, up_vector)
    right /= np.linalg.norm(right)
    down = np.cross(forward, right)  # 相机系 y = 图像向下，右手系
    down /= np.linalg.norm(down)
    if roll:
        angle = np.deg2rad(roll)
        cos, sin = np.cos(angle), np.sin(angle)
        right = cos * right - sin * down
        down = sin * right + cos * down
    rotation = np.stack([right, down, forward], axis=0)
    translation = -rotation @ center
    return {
        "R": rotation,
        "t": translation,
        "K": np.array([[f, 0, width / 2], [0, f, height / 2], [0, 0, 1.0]]),
        "center": center,
        "camera_model": "PINHOLE",
        "radial_distortion": np.zeros(0),
        "image_size": [width, height],
    }


def _circle_cameras(count=8, radius=4.5, height=1.6, f=600.0, width=800, height_img=600):
    cameras = []
    for index in range(count):
        theta = 2 * np.pi * index / count
        center = [radius * np.cos(theta), radius * np.sin(theta), height]
        cameras.append(_camera(center, look_at=(0.0, 0.0, 1.0), f=f, width=width, height=height_img))
    return cameras


def _box(low, high, count, rng):
    return rng.uniform(low, high, size=(count, 3))


def _bbox_mask(points, ids, camera, shape, pad=2):
    """把 ids 的点投影到相机，用其像素包围盒生成 mask。"""
    uv, depth, valid = project_points_to_view(
        points[np.asarray(ids, dtype=int)], camera, image_shape=shape
    )
    u, v = uv[valid, 0], uv[valid, 1]
    if len(u) == 0:
        return None
    x1 = max(0, int(np.floor(u.min())) - pad)
    x2 = min(shape[1], int(np.ceil(u.max())) + pad + 1)
    y1 = max(0, int(np.floor(v.min())) - pad)
    y2 = min(shape[0], int(np.ceil(v.max())) + pad + 1)
    mask = np.zeros(shape, dtype=bool)
    mask[y1:y2, x1:x2] = True
    return mask


def _frame():
    return RoomFrame(
        origin=np.zeros(3),
        axes=np.eye(3),
        ground_inlier_ratio=0.3,
        confidence="high",
        horizontal_method="manhattan_walls",
        floor_plane=np.array([0.0, 0.0, 1.0, 0.0]),
        wall_normals=(np.array([1.0, 0.0, 0.0]), np.array([0.0, 1.0, 0.0])),
        ground_support=0.4,
    )


def _manual_fusion(point_labels, *, count, supporting_views=4, score=0.9, consistency=1.0):
    """构造手动融合结果，跳过真实投影投票。"""
    return SemanticFusion(
        visible_views=np.full(count, supporting_views, dtype=int),
        votes={pid: {label: score * supporting_views} for pid, label in point_labels.items()},
        supporting_views={pid: {label: supporting_views} for pid, label in point_labels.items()},
        point_labels=dict(point_labels),
        semantic_score={pid: score for pid in point_labels},
        consistency={pid: consistency for pid in point_labels},
        diagnostics={},
    )


# ---------------------------------------------------------------------------
# 1. 投影测试
# ---------------------------------------------------------------------------

def test_project_points_to_view_pinhole_front_and_bounds():
    camera = {
        "R": np.eye(3), "t": np.array([0.0, 0.0, -2.0]),
        "K": np.array([[500.0, 0, 320], [0, 500, 240], [0, 0, 1.0]]),
        "camera_model": "PINHOLE", "radial_distortion": np.zeros(0),
        "image_size": [640, 480],
    }
    points = np.array([[0.0, 0.0, 4.0], [1.0, 1.0, 4.0], [0.0, 0.0, -1.0]])
    uv, depth, valid = project_points_to_view(points, camera)

    assert valid[0] and np.allclose(uv[0], [320.0, 240.0]) and depth[0] == pytest.approx(2.0)
    # (1,1,4) 投影到 (570,490)，v=490 超出 480 高度 → 图像边界外拒投
    assert not valid[1]
    # 相机后方点（cam z = -1）必须拒绝
    assert not valid[2]


def test_project_points_to_view_radial_distortion():
    camera = {
        "R": np.eye(3), "t": np.zeros(3),
        "K": np.array([[600.0, 0, 400], [0, 600, 300], [0, 0, 1.0]]),
        "camera_model": "SIMPLE_RADIAL", "radial_distortion": np.array([0.1]),
        "image_size": [800, 600],
    }
    # 相机坐标 (1.0, 0.5, 5.0)：x_n=0.2, y_n=0.1, r2=0.05
    points = np.array([[1.0, 0.5, 5.0]])
    uv, _depth, valid = project_points_to_view(points, camera)
    expected = 1.0 + 0.1 * 0.05
    assert valid[0]
    assert uv[0, 0] == pytest.approx(600 * expected * 0.2 + 400)
    assert uv[0, 1] == pytest.approx(600 * expected * 0.1 + 300)


def test_project_points_unknown_camera_model_rejected():
    camera = {
        "R": np.eye(3), "t": np.zeros(3),
        "K": np.eye(3), "camera_model": "OPENCV",
        "radial_distortion": np.zeros(0), "image_size": [100, 100],
    }
    uv, _depth, valid = project_points_to_view(np.array([[0.0, 0.0, 2.0]]), camera)
    assert not valid[0]


# ---------------------------------------------------------------------------
# 2. 遮挡与多视角投票
# ---------------------------------------------------------------------------

def test_fusion_occluded_background_never_voted():
    """同一像素上位于前景后方的点不能被 mask 投票。"""
    points = np.array([[0.0, 0.0, 2.0], [0.0, 0.0, 5.0]])
    camera = _camera((0.0, 0.0, 0.0), look_at=(0.0, 0.0, 10.0), f=100.0, width=100, height=100)
    mask = np.zeros((100, 100), dtype=bool)
    mask[50, 50] = True
    records = [
        {
            "camera": camera,
            "image_shape": (100, 100),
            "detections": [{"label": "床", "score": 0.9, "mask": mask, "mask_score": 0.9}],
        }
        for _ in range(3)
    ]
    fusion = fuse_multiview_semantics(points, records, min_supporting_views=1)
    assert 0 in fusion.point_labels and fusion.point_labels[0] == "床"
    assert 1 not in fusion.point_labels


def test_fusion_multiview_majority_and_consistency():
    """6 个可见视图中 5 个投票为床 → 高置信床；只被单帧投过的点不给标签。"""
    points = _box([-1.0, -0.75, 0.4], [1.0, 0.75, 0.9], 600, np.random.default_rng(1))
    stray = np.array([[4.0, 4.0, 1.0]])
    points = np.vstack([points, stray])
    camera = _camera((0.0, 0.0, -4.0), look_at=(0.0, 0.0, 10.0))
    shape = (600, 800)
    mask = _bbox_mask(points, list(range(600)), camera, shape)
    assert mask is not None
    records = []
    for index in range(6):
        detections = [] if index == 5 else [
            {"label": "床", "score": 0.9, "mask": mask, "mask_score": 0.95}
        ]
        records.append({"camera": camera, "image_shape": shape, "detections": detections})
    fusion = fuse_multiview_semantics(points, records)

    labeled = [pid for pid in fusion.point_labels.values()]
    assert labeled and set(labeled) == {"床"}
    assert 600 not in fusion.point_labels  # 只被 0 帧投过的游离点
    bed_support = max(
        fusion.supporting_views[pid].get("床", 0) for pid in fusion.point_labels
    )
    assert bed_support >= 5


def test_fusion_single_view_detection_cannot_define_semantics():
    points = _box([-0.5, -0.5, 0.3], [0.5, 0.5, 0.8], 200, np.random.default_rng(2))
    camera = _camera((0.0, 0.0, -3.0), look_at=(0.0, 0.0, 10.0), f=100.0, width=100, height=100)
    shape = (600, 800)
    mask = _bbox_mask(points, list(range(len(points))), camera, shape)
    records = [{
        "camera": camera,
        "image_shape": shape,
        "detections": [{"label": "床", "score": 0.9, "mask": mask, "mask_score": 0.95}],
    }]
    fusion = fuse_multiview_semantics(points, records)  # 默认 min_supporting_views=2
    assert fusion.point_labels == {}


# ---------------------------------------------------------------------------
# 3. 3D 实例分离
# ---------------------------------------------------------------------------

def _two_cabinet_scene():
    rng = np.random.default_rng(3)
    cabinet_a = _box([1.15, -0.275, 0.0], [2.15, 0.275, 2.0], 1200, rng)
    cabinet_b = _box([-2.15, -0.275, 0.0], [-1.15, 0.275, 2.0], 1200, rng)
    points = np.vstack([cabinet_a, cabinet_b])
    cameras = _circle_cameras(8, radius=4.5, height=1.6)
    shape = (600, 800)
    records = []
    for camera in cameras:
        mask_a = _bbox_mask(points, list(range(0, 1200)), camera, shape)
        mask_b = _bbox_mask(points, list(range(1200, 2400)), camera, shape)
        # 真实实例 mask 互不重叠：裁剪掉彼此包围盒，避免远处投影互相污染。
        if mask_a is not None and mask_b is not None:
            mask_a, mask_b = mask_a & ~mask_b, mask_b & ~mask_a
        detections = []
        for mask in (mask_a, mask_b):
            if mask is not None and mask.any():
                detections.append({"label": "柜子", "score": 0.9, "mask": mask, "mask_score": 0.95})
        records.append({"camera": camera, "image_shape": shape, "detections": detections})
    return points, cameras, records


def test_spatially_separated_cabinets_become_two_instances():
    points, _cameras, records = _two_cabinet_scene()
    fusion = fuse_multiview_semantics(points, records)
    ids = fusion.label_point_ids("柜子")
    assert len(ids) >= 50

    clusters = cluster_semantic_instances(points, ids, min_points=20)
    assert len(clusters) >= 2
    merged = merge_fragmented_clusters(points, clusters, records, "柜子")
    assert len(merged) == 2

    objects, stats = build_semantic_objects(
        points, fusion, records, _frame(), unit="meters", metric_scale_status="metric_apriltag"
    )
    cabinets = [obj for obj in objects if obj.label == "柜子"]
    assert len(cabinets) == 2
    assert {obj.instance_id for obj in cabinets} == {"cabinet_01", "cabinet_02"}
    centers = sorted(float(np.linalg.norm(obj.center_3d - [0, 0, 1.0])) for obj in cabinets)
    assert centers[0] == pytest.approx(1.65, abs=0.2)
    assert centers[1] == pytest.approx(1.65, abs=0.2)


def test_mask_association_merges_occlusion_split_into_one_instance():
    rng = np.random.default_rng(4)
    left = _box([-0.7, -0.3, 0.0], [-0.2, 0.3, 2.0], 900, rng)
    right = _box([0.2, -0.3, 0.0], [0.7, 0.3, 2.0], 900, rng)
    points = np.vstack([left, right])  # 中间 0.4 空隙：遮挡造成的断裂
    cameras = _circle_cameras(8, radius=4.0, height=1.6)
    shape = (600, 800)
    records = []
    for camera in cameras:
        mask = _bbox_mask(points, list(range(len(points))), camera, shape)
        if mask is not None:
            records.append({
                "camera": camera,
                "image_shape": shape,
                "detections": [{"label": "柜子", "score": 0.9, "mask": mask, "mask_score": 0.95}],
            })
    fusion = fuse_multiview_semantics(points, records)
    ids = fusion.label_point_ids("柜子")
    clusters = cluster_semantic_instances(points, ids, min_points=20)
    merged = merge_fragmented_clusters(points, clusters, records, "柜子")
    assert len(merged) == 1

    objects, _stats = build_semantic_objects(
        points, fusion, records, _frame(), unit="meters", metric_scale_status="metric_apriltag"
    )
    cabinets = [obj for obj in objects if obj.label == "柜子"]
    assert len(cabinets) == 1
    assert cabinets[0].instance_id == "cabinet_01"


# ---------------------------------------------------------------------------
# 4. RoomFrame
# ---------------------------------------------------------------------------

def test_room_frame_with_roll_walls_and_outliers():
    rng = np.random.default_rng(5)
    rotation = np.array([
        [1, 0, 0],
        [0, np.cos(np.deg2rad(20)), -np.sin(np.deg2rad(20))],
        [0, np.sin(np.deg2rad(20)), np.cos(np.deg2rad(20))],
    ]) @ np.array([
        [np.cos(np.deg2rad(15)), -np.sin(np.deg2rad(15)), 0],
        [np.sin(np.deg2rad(15)), np.cos(np.deg2rad(15)), 0],
        [0, 0, 1],
    ])
    floor = np.column_stack([
        rng.uniform(-2.4, 2.4, 2000),
        rng.uniform(-1.8, 1.8, 2000),
        rng.normal(0.0, 0.004, 2000),
    ])
    wall_x1 = np.column_stack([np.full(600, 2.4), rng.uniform(-1.8, 1.8, 600), rng.uniform(0, 2.7, 600)])
    wall_x2 = np.column_stack([np.full(600, -2.4), rng.uniform(-1.8, 1.8, 600), rng.uniform(0, 2.7, 600)])
    wall_y1 = np.column_stack([rng.uniform(-2.4, 2.4, 600), np.full(600, 1.8), rng.uniform(0, 2.7, 600)])
    wall_y2 = np.column_stack([rng.uniform(-2.4, 2.4, 600), np.full(600, -1.8), rng.uniform(0, 2.7, 600)])
    ceiling = np.column_stack([
        rng.uniform(-2.4, 2.4, 400), rng.uniform(-1.8, 1.8, 400), np.full(400, 2.7),
    ])
    furniture = _box([1.0, 0.4, 0.1], [1.7, 1.2, 1.5], 300, rng)
    outliers = _box([-4.0, -4.0, -1.0], [4.0, 4.0, 4.0], 40, rng)
    points = np.vstack([floor, wall_x1, wall_x2, wall_y1, wall_y2, ceiling, furniture, outliers])
    points = points @ rotation.T

    world_up = np.array([0.0, 0.0, 1.0]) @ rotation.T
    cameras = []
    for index in range(12):
        theta = 2 * np.pi * index / 12
        center = np.array([3.5 * np.cos(theta), 3.5 * np.sin(theta), 1.5])
        forward = -center.copy()
        forward[2] = 0.0
        forward /= np.linalg.norm(forward)
        down = -world_up
        right = np.cross(down, forward)
        right /= np.linalg.norm(right)
        forward = np.cross(right, down)
        roll = rng.uniform(-12, 12)
        angle = np.deg2rad(roll)
        cos, sin = np.cos(angle), np.sin(angle)
        right = cos * right - sin * down
        down = sin * right + cos * down
        cameras.append({"R": np.stack([right, down, forward], axis=0), "center": center})

    frame = estimate_room_frame(points, cameras)
    assert frame is not None
    assert abs(float(frame.axes[:, 2] @ world_up)) > 0.95
    assert frame.horizontal_method == "manhattan_walls"
    assert len(frame.wall_normals) == 2
    assert frame.floor_plane is not None
    assert frame.ground_support > 0.2

    room = measure_room(points, frame)
    assert room["status"] == "measured"
    assert room["dimensions"]["length"] > room["dimensions"]["width"] > room["dimensions"]["height"]


def test_room_frame_fails_without_ground_support():
    """只有家具、没有地面的点云不能产出可靠 RoomFrame。"""
    rng = np.random.default_rng(6)
    furniture = _box([-1.5, -1.2, 0.3], [1.5, 1.2, 2.2], 800, rng)
    frame = estimate_room_frame(furniture, [])
    assert frame is None or frame.confidence == "low"


# ---------------------------------------------------------------------------
# 5. 尺寸测量（米制）
# ---------------------------------------------------------------------------

def test_bed_dimensions_with_noise_and_outliers():
    rng = np.random.default_rng(7)
    xs, ys = np.meshgrid(np.linspace(-1.0, 1.0, 41), np.linspace(-0.75, 0.75, 31))
    top = np.column_stack([
        xs.ravel() + rng.normal(0, 0.004, xs.size),
        ys.ravel() + rng.normal(0, 0.004, ys.size),
        np.full(xs.size, 0.48) + rng.normal(0, 0.004, xs.size),
    ])
    skirt = np.column_stack([
        rng.uniform(-1.0, 1.0, 300),
        rng.uniform(-0.75, 0.75, 300),
        rng.uniform(0.25, 0.48, 300),
    ])
    outliers = _box([-3.0, -3.0, 0.0], [3.0, 3.0, 2.0], 30, rng)
    points = np.vstack([top, skirt, outliers])

    fusion = _manual_fusion(
        {pid: "床" for pid in range(len(points) - 30)},
        count=len(points),
    )
    objects, _stats = build_semantic_objects(
        points, fusion, [], _frame(), unit="meters", metric_scale_status="metric_apriltag"
    )
    assert len(objects) == 1
    obj = objects[0]
    assert obj.status == "measured"
    dims = obj.dimensions
    assert dims["length_m"] == pytest.approx(2.0, abs=0.12)
    assert dims["width_m"] == pytest.approx(1.5, abs=0.12)
    assert dims["height_m"] == pytest.approx(0.48, abs=0.08)
    assert obj.footprint is not None and obj.footprint["area"] == pytest.approx(3.0, rel=0.15)
    assert len(obj.obb_corners) == 8
    assert obj.measurement_confidence in {"high", "medium"}


def test_cabinet_dimensions_use_vertical_extent():
    rng = np.random.default_rng(8)
    cabinet = _box([-0.595, -0.295, 0.0], [0.595, 0.295, 2.01], 1500, rng)
    outliers = _box([-4.0, -4.0, 0.0], [4.0, 4.0, 3.0], 25, rng)
    points = np.vstack([cabinet, outliers])
    fusion = _manual_fusion({pid: "柜子" for pid in range(1500)}, count=len(points))
    objects, _stats = build_semantic_objects(
        points, fusion, [], _frame(), unit="meters", metric_scale_status="metric_apriltag"
    )
    assert len(objects) == 1
    obj = objects[0]
    assert obj.status == "measured"
    dims = obj.dimensions
    assert dims["length_m"] == pytest.approx(1.19, abs=0.07)   # 宽（沿墙）
    assert dims["width_m"] == pytest.approx(0.59, abs=0.06)    # 深
    assert dims["height_m"] == pytest.approx(2.01, abs=0.07)


def test_table_dimensions_use_tabletop_height():
    """1.2 × 0.6 × 0.75 m 桌面：高度取顶面距地（p90），长宽取水平 OBB。"""
    rng = np.random.default_rng(16)
    xs, ys = np.meshgrid(np.linspace(-0.6, 0.6, 25), np.linspace(-0.3, 0.3, 13))
    tabletop = np.column_stack([
        xs.ravel() + rng.normal(0, 0.003, xs.size),
        ys.ravel() + rng.normal(0, 0.003, ys.size),
        np.full(xs.size, 0.75) + rng.normal(0, 0.003, xs.size),
    ])
    legs = np.column_stack([
        rng.uniform(-0.55, 0.55, 120),
        rng.uniform(-0.25, 0.25, 120),
        rng.uniform(0.05, 0.72, 120),
    ])
    outliers = _box([-2.5, -2.5, 0.0], [2.5, 2.5, 1.8], 20, rng)
    points = np.vstack([tabletop, legs, outliers])
    fusion = _manual_fusion(
        {pid: "桌子" for pid in range(len(points) - 20)}, count=len(points)
    )
    objects, _stats = build_semantic_objects(
        points, fusion, [], _frame(), unit="meters", metric_scale_status="metric_apriltag"
    )
    assert len(objects) == 1
    obj = objects[0]
    assert obj.status == "measured"
    assert obj.dimensions["length_m"] == pytest.approx(1.2, abs=0.1)
    assert obj.dimensions["width_m"] == pytest.approx(0.6, abs=0.08)
    assert obj.dimensions["height_m"] == pytest.approx(0.75, abs=0.06)


def test_sofa_dimensions_include_backrest_height():
    """2.0 × 0.9 坐面、靠背顶 0.85 m：高度取含靠背的垂直范围。"""
    rng = np.random.default_rng(17)
    xs, ys = np.meshgrid(np.linspace(-1.0, 1.0, 41), np.linspace(-0.45, 0.45, 19))
    seat = np.column_stack([
        xs.ravel() + rng.normal(0, 0.003, xs.size),
        ys.ravel() + rng.normal(0, 0.003, ys.size),
        np.full(xs.size, 0.45) + rng.normal(0, 0.003, xs.size),
    ])
    backrest = np.column_stack([
        np.full(250, -1.0) + rng.normal(0, 0.003, 250),
        rng.uniform(-0.45, 0.45, 250),
        rng.uniform(0.45, 0.85, 250),
    ])
    base = np.column_stack([
        rng.uniform(-1.0, 1.0, 200),
        rng.uniform(-0.45, 0.45, 200),
        rng.uniform(0.1, 0.45, 200),
    ])
    points = np.vstack([seat, backrest, base])
    fusion = _manual_fusion({pid: "沙发" for pid in range(len(points))}, count=len(points))
    objects, _stats = build_semantic_objects(
        points, fusion, [], _frame(), unit="meters", metric_scale_status="metric_apriltag"
    )
    assert len(objects) == 1
    obj = objects[0]
    assert obj.status == "measured"
    assert obj.dimensions["length_m"] == pytest.approx(2.0, abs=0.15)
    assert obj.dimensions["width_m"] == pytest.approx(0.9, abs=0.12)
    assert obj.dimensions["height_m"] == pytest.approx(0.85, abs=0.1)


def test_room_fallback_uses_scene_extent_with_lowered_confidence():
    """地面点不足时退回全点云稳健范围，并降级置信度、注明方法。"""
    rng = np.random.default_rng(18)
    floor_few = np.column_stack([  # 只有少量地面点（<100），不满足地面优先路径
        rng.uniform(-2.4, 2.4, 60),
        rng.uniform(-1.8, 1.8, 60),
        rng.normal(0.0, 0.004, 60),
    ])
    wall_x1 = np.column_stack([np.full(400, 2.4), rng.uniform(-1.8, 1.8, 400), rng.uniform(0, 2.7, 400)])
    wall_x2 = np.column_stack([np.full(400, -2.4), rng.uniform(-1.8, 1.8, 400), rng.uniform(0, 2.7, 400)])
    wall_y1 = np.column_stack([rng.uniform(-2.4, 2.4, 400), np.full(400, 1.8), rng.uniform(0, 2.7, 400)])
    wall_y2 = np.column_stack([rng.uniform(-2.4, 2.4, 400), np.full(400, -1.8), rng.uniform(0, 2.7, 400)])
    ceiling = np.column_stack([
        rng.uniform(-2.4, 2.4, 300), rng.uniform(-1.8, 1.8, 300), np.full(300, 2.7),
    ])
    points = np.vstack([floor_few, wall_x1, wall_x2, wall_y1, wall_y2, ceiling])
    frame = _frame()
    room = measure_room(points, frame)

    assert room["status"] == "measured"
    assert room["method"] == "scene_extent_fallback"
    assert room["confidence"] in {"low", "medium"}
    assert room["dimensions"]["length"] > room["dimensions"]["width"] > 0


def test_object_relations_report_nearest_neighbors_with_footprint_gap():
    """物体间位置关系：最近邻居 + 中心距离 + 地面 footprint 近似间隙。"""
    points, _cameras, records = _two_cabinet_scene()
    fusion = fuse_multiview_semantics(points, records)
    space = build_semantic_space(
        points, fusion, records, _frame(), unit="meters", metric_scale_status="metric_apriltag"
    )
    relations = space["object_relations"]
    assert set(relations) == {"cabinet_01", "cabinet_02"}
    first = relations["cabinet_01"][0]
    assert first["instance_id"] == "cabinet_02"
    assert first["label"] == "柜子"
    assert first["center_distance"] == pytest.approx(3.3, abs=0.25)
    # 两柜 footprint 边缘间隙 ≈ 中心距 3.3 - 柜宽 1.0
    assert first["footprint_gap"] == pytest.approx(2.3, abs=0.3)


def test_door_opening_width_and_height_from_jamb_columns():
    rng = np.random.default_rng(9)
    jamb_left = _box([-0.47, -0.03, 0.0], [-0.39, 0.03, 2.04], 500, rng)
    jamb_right = _box([0.39, -0.03, 0.0], [0.47, 0.03, 2.04], 500, rng)
    lintel = _box([-0.47, -0.03, 2.0], [0.47, 0.03, 2.04], 120, rng)
    leaf = _box([-0.43, -0.02, 0.1], [0.05, 0.02, 1.9], 800, rng)
    points = np.vstack([jamb_left, jamb_right, lintel, leaf])
    fusion = _manual_fusion({pid: "门" for pid in range(len(points))}, count=len(points))
    objects, _stats = build_semantic_objects(
        points, fusion, [], _frame(), unit="meters", metric_scale_status="metric_apriltag"
    )
    assert len(objects) == 1
    obj = objects[0]
    assert obj.status == "measured"
    door_meta = obj.metadata["door_measurement"]
    assert door_meta["method"] == "door_jamb_columns"
    assert door_meta["fallback_used"] is False
    assert door_meta["estimated_opening_width_m"] == pytest.approx(0.86, abs=0.08)
    assert door_meta["estimated_opening_height_m"] == pytest.approx(2.04, abs=0.1)
    assert obj.dimensions["width_m"] == pytest.approx(0.86, abs=0.08)


def test_room_dimensions_from_floor_walls_and_ceiling():
    rng = np.random.default_rng(10)
    floor = np.column_stack([
        rng.uniform(-2.41, 2.41, 2500),
        rng.uniform(-1.805, 1.805, 2500),
        rng.normal(0.0, 0.003, 2500),
    ])
    wall_x1 = np.column_stack([np.full(500, 2.41), rng.uniform(-1.805, 1.805, 500), rng.uniform(0, 2.74, 500)])
    wall_x2 = np.column_stack([np.full(500, -2.41), rng.uniform(-1.805, 1.805, 500), rng.uniform(0, 2.74, 500)])
    wall_y1 = np.column_stack([rng.uniform(-2.41, 2.41, 500), np.full(500, 1.805), rng.uniform(0, 2.74, 500)])
    wall_y2 = np.column_stack([rng.uniform(-2.41, 2.41, 500), np.full(500, -1.805), rng.uniform(0, 2.74, 500)])
    ceiling = np.column_stack([
        rng.uniform(-2.41, 2.41, 400), rng.uniform(-1.805, 1.805, 400), np.full(400, 2.74),
    ])
    points = np.vstack([floor, wall_x1, wall_x2, wall_y1, wall_y2, ceiling])

    frame = estimate_room_frame(points, _circle_cameras(10, radius=4.5, height=1.6))
    assert frame is not None and frame.confidence in {"high", "medium"}

    space = build_semantic_space(
        points, SemanticFusion(
            visible_views=np.zeros(len(points), dtype=int),
            votes={}, supporting_views={}, point_labels={},
            semantic_score={}, consistency={},
        ), [], frame, unit="meters", metric_scale_status="metric_apriltag",
    )
    room = space["room_dimensions"]
    assert room["status"] == "measured"
    dims = room["dimensions"]
    assert dims["length_m"] == pytest.approx(4.82, abs=0.3)
    assert dims["width_m"] == pytest.approx(3.61, abs=0.3)
    assert dims["height_m"] == pytest.approx(2.74, abs=0.2)
    assert space["room_frame"]["floor_plane"] is not None


# ---------------------------------------------------------------------------
# 6. 失败用例 → unknown
# ---------------------------------------------------------------------------

def test_too_few_points_returns_unknown():
    rng = np.random.default_rng(11)
    points = _box([-0.2, -0.2, 0.3], [0.2, 0.2, 0.7], 12, rng)
    fusion = _manual_fusion({pid: "床" for pid in range(len(points))}, count=len(points))
    objects, _stats = build_semantic_objects(
        points, fusion, [], _frame(), unit="meters", metric_scale_status="metric_apriltag"
    )
    assert len(objects) == 1
    assert objects[0].status == "unknown"
    assert objects[0].reason == "too_few_points"
    assert all(value is None for value in objects[0].dimensions.values())


def test_heavy_outliers_return_unknown_instead_of_fake_size():
    """弥漫离群点形成大簇时按实例分离，绝不把离群簇硬算成真实尺寸。"""
    rng = np.random.default_rng(12)
    bed = _box([-1.0, -0.75, 0.3], [1.0, 0.75, 0.8], 120, rng)
    # 大面积弥漫离群点（稀疏平面带），空间上与床分离，且占点数主导。
    outliers = np.column_stack([
        rng.uniform(-8.0, 8.0, 600),
        rng.uniform(-8.0, 8.0, 600),
        rng.uniform(2.5, 3.5, 600),
    ])
    points = np.vstack([bed, outliers])
    fusion = _manual_fusion({pid: "床" for pid in range(len(points))}, count=len(points))
    objects, _stats = build_semantic_objects(
        points, fusion, [], _frame(), unit="meters", metric_scale_status="metric_apriltag"
    )
    assert len(objects) == 2
    measured = [obj for obj in objects if obj.status == "measured"]
    unknown = [obj for obj in objects if obj.status == "unknown"]
    assert len(measured) == 1
    assert measured[0].dimensions["length_m"] == pytest.approx(2.0, abs=0.3)
    assert len(unknown) == 1
    assert unknown[0].reason == "diffuse_cluster"


def test_room_frame_failure_returns_unknown_dimensions():
    rng = np.random.default_rng(13)
    points = _box([-1.0, -0.75, 0.3], [1.0, 0.75, 0.8], 800, rng)
    fusion = _manual_fusion({pid: "床" for pid in range(len(points))}, count=len(points))
    objects, _stats = build_semantic_objects(
        points, fusion, [], None, unit="meters", metric_scale_status="metric_apriltag"
    )
    assert len(objects) == 1
    assert objects[0].status == "unknown"
    assert objects[0].reason == "room_frame_unavailable"


def test_relative_scale_keeps_semantics_but_never_fakes_meters():
    rng = np.random.default_rng(14)
    points = _box([-1.0, -0.75, 0.3], [1.0, 0.75, 0.8], 800, rng)
    fusion = _manual_fusion({pid: "床" for pid in range(len(points))}, count=len(points))
    objects, _stats = build_semantic_objects(
        points, fusion, [], _frame(), unit="model_units", metric_scale_status="relative"
    )
    assert len(objects) == 1
    obj = objects[0]
    assert obj.label == "床"          # 语义理解照常
    assert obj.status == "unknown"
    assert obj.reason == "metric_scale_unavailable"
    assert obj.measurement_confidence == "low"

    space = build_semantic_space(
        points, fusion, [], _frame(), unit="model_units", metric_scale_status="relative"
    )
    assert space["room_dimensions"]["status"] == "unknown"
    assert space["metric_available"] is False
    assert space["unit"] == "model_units"


@pytest.mark.parametrize("status", ["relative", "calibration_failed"])
def test_non_metric_status_cannot_fake_meters_by_unit_argument(status):
    rng = np.random.default_rng(140)
    points = _box([-1.0, -0.75, 0.3], [1.0, 0.75, 0.8], 500, rng)
    fusion = _manual_fusion({pid: "床" for pid in range(len(points))}, count=len(points))
    space = build_semantic_space(
        points, fusion, [], _frame(), unit="meters", metric_scale_status=status
    )
    assert space["metric_available"] is False
    assert space["unit"] == "model_units"
    assert space["room_dimensions"]["status"] == "unknown"
    assert space["objects"][0]["status"] == "unknown"
    assert space["objects"][0]["reason"] == "metric_scale_unavailable"


# ---------------------------------------------------------------------------
# 7. 旧兼容标定分支的缩放回写
# ---------------------------------------------------------------------------

def test_rescale_semantic_space_applies_final_scale_once():
    rng = np.random.default_rng(15)
    points = _box([-1.0, -0.75, 0.3], [1.0, 0.75, 0.8], 600, rng)
    fusion = _manual_fusion({pid: "床" for pid in range(len(points))}, count=len(points))
    space = build_semantic_space(
        points, fusion, [], _frame(), unit="model_units", metric_scale_status="metric_references"
    )
    assert space["room_dimensions"]["status"] == "unknown"

    scaled = rescale_semantic_space(space, 2.0, unit="meters")
    assert scaled["unit"] == "meters" and scaled["metric_available"] is True
    obj = scaled["objects"][0]
    assert obj["status"] == "measured"
    assert obj["dimensions"]["length_m"] == pytest.approx(2.0 * 1.92, abs=0.3)
    assert obj["center_3d"][2] == pytest.approx(2.0 * 0.55, abs=0.2)
    with pytest.raises(ValueError, match="禁止重复"):
        rescale_semantic_space(scaled, 2.0, unit="meters")
