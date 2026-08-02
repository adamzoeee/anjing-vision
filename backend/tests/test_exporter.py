import numpy as np
import open3d as o3d
import torch
from pipeline.exporter import export_pointcloud, statistical_filter


def _gaussians(n=1000):
    """与训练器输出一致：CPU torch 张量 dict，scales 为 log 尺度。"""
    return {
        "means": torch.randn(n, 3),
        "scales": torch.full((n, 3), float(np.log(0.02))),
        "quats": torch.tile(torch.tensor([1.0, 0.0, 0.0, 0.0]), (n, 1)),
        "opacities": torch.full((n,), 0.9),
        "sh0": torch.zeros(n, 1, 3),
    }


def test_export_pointcloud_writes_ply(tmp_path):
    g = _gaussians()
    pcd_path = tmp_path / "pc.ply"
    export_pointcloud(g, pcd_path)
    pcd = o3d.io.read_point_cloud(str(pcd_path))
    assert len(pcd.points) > 0


def test_export_pointcloud_filters_low_opacity(tmp_path):
    g = _gaussians(n=500)
    g["opacities"] = torch.cat([torch.full((250,), 0.9), torch.full((250,), 0.1)])
    pcd_path = tmp_path / "pc.ply"
    export_pointcloud(g, pcd_path)
    pcd = o3d.io.read_point_cloud(str(pcd_path))
    assert len(pcd.points) <= 250  # 仅高不透明度（250 个）高斯保留


def test_statistical_filter_removes_outliers():
    pts = np.random.randn(1000, 3).astype(np.float64)
    pts[0] = [100, 100, 100]  # 离群点
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(pts)
    filtered = statistical_filter(pcd, nb_neighbors=20, std_ratio=2.0)
    assert len(filtered.points) < 1000
