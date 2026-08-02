import numpy as np
import open3d as o3d
from pipeline.geometry import fit_ground_plane, measure_door_width, measure_step_height, measure_floor_slope


def _pcd(points: np.ndarray) -> o3d.geometry.PointCloud:
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    return pcd


def _synthetic_room():
    """6x4m 房间点云：地面 z=0，两侧墙，一道 0.8m 门洞。"""
    rng = np.random.default_rng(1)
    pts = []
    for x in np.arange(-3, 3, 0.02):
        for y in np.arange(-2, 2, 0.02):
            if rng.random() < 0.3:
                pts.append([x, y, 0])            # 地面
    for y in np.arange(-2, 2, 0.02):
        for z in np.arange(0, 2.5, 0.02):
            if rng.random() < 0.3:
                pts.append([-3, y, z])           # 左墙
                if not (-0.4 <= y <= 0.4 and z <= 2.0):
                    pts.append([3, y, z])        # 右墙（门洞区跳过，否则门洞被填满）
    # 门洞在 x=3 墙上，y ∈ [-0.4, 0.4]，z ∈ [0, 2]
    for y in np.arange(-2, 2, 0.02):
        for z in np.arange(0, 2.5, 0.02):
            if not (-0.4 <= y <= 0.4 and z <= 2.0):
                if rng.random() < 0.3:
                    pts.append([3, y, z])
    return np.array(pts)


def test_fit_ground_plane():
    pts = _synthetic_room()
    plane, inliers = fit_ground_plane(pts)
    a, b, c, d = plane
    assert abs(c) > 0.9 and abs(d) < 0.15  # 法向接近 z 轴，z≈0


def test_measure_door_width_finds_0_8m():
    pts = _synthetic_room()
    w = measure_door_width(pts, wall_x=3.0, z_min=0.0, z_max=2.0, y_min=-2, y_max=2)
    assert abs(w - 0.8) < 0.15


def test_measure_step_height():
    pts = _synthetic_room()
    pts = np.vstack([pts, [0, 0, 0.05] + np.random.default_rng(2).normal(0, 0.002, (300, 3))])
    h = measure_step_height(pts)
    assert abs(h - 0.05) < 0.02


def test_measure_floor_slope():
    pts = _synthetic_room()
    pts[:, 2] += 0.05 * pts[:, 0]  # 5% 坡度
    slope = measure_floor_slope(pts)
    assert abs(slope - 0.05) < 0.02
