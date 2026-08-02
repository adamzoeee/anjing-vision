"""高斯场 → 稠密点云：按尺度采样高斯中心点（统计滤波见 statistical_filter）。"""
from pathlib import Path

import numpy as np
import open3d as o3d


def export_pointcloud(gaussians: dict, out_path: Path, downsample_voxel: float = 0.01):
    """从训练结果导出点云。scales 为 log 尺度（训练器约定），opacities 为 (N,)。

    输入为 torch CPU 张量 dict（train_gaussians 的输出）；低不透明度（<=0.5）
    高斯剔除；大尺度高斯按正态扰动扩展为多点。
    """
    means = gaussians["means"].numpy()
    scales = gaussians["scales"].exp().numpy()
    opac = gaussians["opacities"].numpy().reshape(-1)
    keep = opac > 0.5
    means, scales = means[keep], scales[keep]
    rng = np.random.default_rng(0)
    pts = [means]
    for s in (2.0, 3.0):
        mask = scales.max(axis=1) > 0.05 * s
        if mask.any():
            pts.append(means[mask] + rng.normal(0, 0.02, (mask.sum(), 3)))
    cloud = np.concatenate(pts, axis=0).astype(np.float64)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(cloud)
    if downsample_voxel > 0:
        pcd = pcd.voxel_down_sample(downsample_voxel)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(out_path), pcd)


def statistical_filter(pcd: o3d.geometry.PointCloud, nb_neighbors: int = 20, std_ratio: float = 2.0):
    cl, _ = pcd.remove_statistical_outlier(nb_neighbors=nb_neighbors, std_ratio=std_ratio)
    return cl
