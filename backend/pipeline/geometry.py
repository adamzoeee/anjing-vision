"""点云几何分析：RANSAC 平面提取、门宽/门槛/台阶/坡度测量。"""
import numpy as np
import open3d as o3d


def _to_pcd(points: np.ndarray) -> o3d.geometry.PointCloud:
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.asarray(points, dtype=np.float64).reshape(-1, 3))
    return pcd


def fit_ground_plane(points: np.ndarray, distance_threshold: float = 0.03) -> tuple[np.ndarray, np.ndarray]:
    """RANSAC 拟合地面（法向最接近 z 轴的平面）。返回 (ax+by+cz+d=0, 内点索引)。"""
    pcd = _to_pcd(points)
    plane, inliers = pcd.segment_plane(distance_threshold=distance_threshold, ransac_n=3, num_iterations=500)
    a, b, c, d = plane
    if abs(c) < abs(a) or abs(c) < abs(b):
        # 法向更接近水平 → 重新在候选平面里选 z 分量最大的
        best, best_in = plane, inliers
        for _ in range(20):
            p2, in2 = _to_pcd(points).segment_plane(distance_threshold, 3, 200)
            if abs(p2[2]) > abs(best[2]):
                best, best_in = p2, in2
        plane, inliers = best, best_in
    return plane, np.asarray(inliers)


def measure_door_width(
    points: np.ndarray, wall_x: float, z_min: float = 0.0, z_max: float = 2.2,
    y_min: float = -3.0, y_max: float = 3.0,
) -> float:
    """沿墙(x≈wall_x)在高度带 [z_min,z_max] 内统计 y 分布，最大连续空缺宽度≈门宽。

    先剔除地面点：地面(z≈0)会进入墙厚带 |x-wall_x|<0.1 并填入门洞空缺，
    导致最大空缺远小于真实门宽（合成房间地面 x_max≈2.98、z=0 即属此类）。
    """
    keep = np.ones(len(points), dtype=bool)
    try:
        plane, inliers = fit_ground_plane(points)
        a, b, c, d = plane
        if abs(c) > 0.8 and len(inliers) > 0.1 * len(points):
            keep[inliers] = False  # 地面平面可靠（法向接近 z 轴、内点占比合理）时才剔除
    except Exception:
        pass  # 拟合失败时退化为不过滤
    sel = points[
        (np.abs(points[:, 0] - wall_x) < 0.1) & (points[:, 2] >= z_min) & (points[:, 2] <= z_max) & keep
    ]
    if len(sel) < 10:
        return 0.0
    y = np.sort(sel[:, 1])
    y = y[(y >= y_min) & (y <= y_max)]
    if len(y) < 10:
        return 0.0
    gaps = np.diff(y)
    return float(gaps.max()) if gaps.size else 0.0


def measure_step_height(points: np.ndarray, max_height: float = 0.5) -> float:
    """门槛/台阶高度：地面之上 max_height 内按 0.01m 分层统计点数，最高密度层中心≈台阶面高度。

    规格的"内点 z 直方图双峰"法在合成数据上失效：台阶面 z≈0.05 距地面平面
    0.05 > RANSAC distance_threshold=0.03，根本不在 inliers 里；且墙脚 z≈0.02 的
    离散网格层（~108 点/层）会形成伪峰，双峰间距≈0.02 而非 0.05。
    故改为：地面高度 z0 取内点 z 中位数（地面峰天然是第一峰），统计
    z∈(z0, z0+max_height) 内点的 0.01m 分层直方图，取计数最大层中心为台阶高度。
    合成数据中台阶层（~300 点集中在 ±0.006 内）计数远高于墙脚单层（~54 点/0.01m），可稳健区分。
    """
    plane, inliers = fit_ground_plane(points)
    a, b, c, d = plane
    if abs(c) < 0.5:
        return 0.0  # 地面平面不可靠（法向不接近 z 轴）
    z0 = float(np.median(points[inliers, 2]))
    rel = points[:, 2] - z0
    cand = points[(rel > 0.005) & (rel < max_height)]
    if len(cand) < 10:
        return 0.0
    edges = np.arange(0.0, max_height + 0.01, 0.01)
    hist, edges = np.histogram(cand[:, 2] - z0, bins=edges)
    best = int(np.argmax(hist))
    return float(edges[best] + 0.005)


def measure_floor_slope(points: np.ndarray) -> float:
    """地面坡度 = 平面法向与 z 轴夹角的正切。"""
    plane, _ = fit_ground_plane(points)
    a, b, c, d = plane
    norm = np.linalg.norm([a, b, c])
    return float(np.hypot(a, b) / abs(c)) if abs(c) > 1e-6 else 0.0
