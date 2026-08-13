import numpy as np

from pipeline.spatial_measurement import RoomFrame, clean_object_points, estimate_room_frame, evaluate_dimension_accuracy, measure_object, measure_room


def _frame() -> RoomFrame:
    return RoomFrame(
        origin=np.zeros(3),
        axes=np.eye(3),
        ground_inlier_ratio=0.3,
        confidence="high",
        horizontal_method="manhattan_walls",
    )


def test_bed_dimensions_use_horizontal_axes_and_vertical_height():
    rng = np.random.default_rng(3)
    bed = rng.uniform([-1.0, -0.75, 0.35], [1.0, 0.75, 0.85], size=(1200, 3))
    result = measure_object(bed, "床", _frame())

    assert result["status"] == "measured"
    assert result["dimensions"]["length"] > result["dimensions"]["width"]
    assert np.isclose(result["dimensions"]["length"], 1.88, atol=0.12)
    assert np.isclose(result["dimensions"]["width"], 1.41, atol=0.12)
    assert np.isclose(result["dimensions"]["height"], 0.47, atol=0.08)


def test_door_height_is_vertical_not_largest_pca_axis():
    rng = np.random.default_rng(4)
    door = rng.uniform([-0.45, -0.03, 0.0], [0.45, 0.03, 2.1], size=(1000, 3))
    result = measure_object(door, "门", _frame())

    assert result["status"] == "measured"
    assert result["dimensions"]["height"] > result["dimensions"]["width"]
    # 输入先做 2..98% 清理，再以 3..97% 稳健边界测量，避免边缘飞点放大尺寸。
    assert np.isclose(result["dimensions"]["height"], 1.84, atol=0.12)
    assert np.isclose(result["dimensions"]["width"], 0.85, atol=0.1)


def test_object_cleanup_keeps_dominant_cluster_and_drops_background():
    rng = np.random.default_rng(8)
    object_points = rng.normal([0.0, 0.0, 0.7], 0.04, size=(300, 3))
    background = rng.normal([3.0, 3.0, 1.5], 0.06, size=(80, 3))
    cleaned, quality = clean_object_points(np.vstack([object_points, background]), _frame())

    assert len(cleaned) >= 250
    assert np.linalg.norm(cleaned.mean(axis=0) - np.array([0.0, 0.0, 0.7])) < 0.15
    assert quality["cluster_count"] >= 2


def test_room_frame_and_dimensions_follow_ground_not_global_xyz():
    rng = np.random.default_rng(11)
    floor = np.column_stack([
        rng.uniform(-2.5, 2.5, 1800),
        rng.uniform(-2.0, 2.0, 1800),
        rng.normal(0.0, 0.003, 1800),
    ])
    furniture = rng.uniform([-1.5, -1.2, 0.1], [1.5, 1.2, 1.8], size=(700, 3))
    wall_x = np.column_stack([np.full(700, 2.5), rng.uniform(-2, 2, 700), rng.uniform(0, 2.4, 700)])
    wall_y = np.column_stack([rng.uniform(-2.5, 2.5, 700), np.full(700, 2.0), rng.uniform(0, 2.4, 700)])
    angle = np.deg2rad(27)
    rotation = np.array([[1, 0, 0], [0, np.cos(angle), -np.sin(angle)], [0, np.sin(angle), np.cos(angle)]])
    points = np.vstack([floor, furniture, wall_x, wall_y]) @ rotation.T

    world_up = np.array([0.0, 0.0, 1.0]) @ rotation.T
    cameras = []
    for index in range(12):
        # world→camera 的第二行取图像向下，故 -R.T@y 恢复 world_up。
        down = -world_up
        forward = np.array([1.0, 0.0, 0.0]) @ rotation.T
        right = np.cross(down, forward)
        right /= np.linalg.norm(right)
        forward = np.cross(right, down)
        cameras.append({"R": np.stack([right, down, forward], axis=0), "center": np.array([index, 0, 1])})
    frame = estimate_room_frame(points, cameras)
    room = measure_room(points, frame)

    assert frame is not None
    assert frame.horizontal_method == "manhattan_walls"
    assert abs(float(frame.axes[:, 2] @ (np.array([0.0, 0.0, 1.0]) @ rotation.T))) > 0.95
    assert room["status"] == "measured"
    assert room["dimensions"]["length"] > room["dimensions"]["width"] > room["dimensions"]["height"]


def test_dimension_accuracy_excludes_values_used_for_calibration():
    predictions = {
        "门": {"dimensions": {"height": 2.05, "width": 0.86}},
        "床": {"dimensions": {"length": 2.0, "width": 1.47}},
    }
    result = evaluate_dimension_accuracy(
        predictions,
        [
            {"object_type": "door", "dimension": "height", "meters": 2.05},
            {"object_type": "door", "dimension": "width", "meters": 0.9},
            {"object_type": "bed", "dimension": "width", "meters": 1.5},
        ],
        [{"object_type": "door", "dimension": "height", "meters": 2.05}],
    )
    assert result["compared_count"] == 2
    assert len(result["comparisons"]) == 2
    assert result["mean_relative_error"] > 0
    assert all(item["dimension"] != "height" for item in result["comparisons"])
