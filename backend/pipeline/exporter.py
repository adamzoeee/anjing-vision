"""高斯场 → 稠密点云：按尺度采样高斯中心点（统计滤波见 statistical_filter）。"""
from pathlib import Path

import numpy as np
import open3d as o3d

SH_C0 = 0.28209479177387814


def _opacity_values(gaussians: dict) -> np.ndarray:
    values = gaussians["opacities"].numpy().reshape(-1)
    if gaussians.get("opacity_logits", False):
        values = 1.0 / (1.0 + np.exp(-values))
    return values


def gaussian_rgb(gaussians: dict) -> np.ndarray:
    """将零阶球谐系数转换为预览 RGB。"""
    sh0 = gaussians["sh0"].numpy().reshape(-1, 3)
    return np.clip(sh0 * SH_C0 + 0.5, 0.0, 1.0)


def export_pointcloud(gaussians: dict, out_path: Path, downsample_voxel: float = 0.01):
    """从训练结果导出点云。scales 为 log 尺度（训练器约定），opacities 为 (N,)。

    输入为 torch CPU 张量 dict（train_gaussians 的输出）；低不透明度（<=0.5）
    高斯剔除；大尺度高斯按正态扰动扩展为多点。
    """
    means = gaussians["means"].numpy()
    scales = gaussians["scales"].exp().numpy()
    opac = _opacity_values(gaussians)
    colors = gaussian_rgb(gaussians)
    keep = opac > (0.01 if gaussians.get("opacity_logits", False) else 0.5)
    means, scales, colors = means[keep], scales[keep], colors[keep]
    if not len(means):
        raise ValueError("没有达到可见不透明度的高斯点")
    rng = np.random.default_rng(0)
    pts = [means]
    cols = [colors]
    for s in (2.0, 3.0):
        mask = scales.max(axis=1) > 0.05 * s
        if mask.any():
            pts.append(means[mask] + rng.normal(0, 0.02, (mask.sum(), 3)))
            cols.append(colors[mask])
    cloud = np.concatenate(pts, axis=0).astype(np.float64)
    cloud_colors = np.concatenate(cols, axis=0).astype(np.float64)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(cloud)
    pcd.colors = o3d.utility.Vector3dVector(cloud_colors)
    if downsample_voxel > 0:
        pcd = pcd.voxel_down_sample(downsample_voxel)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(out_path), pcd)


def export_gaussian_ply(gaussians: dict, out_path: Path) -> None:
    """导出标准 3D Gaussian Splat PLY，供兼容查看器保留真实高斯属性。"""
    means = gaussians["means"].numpy().astype("<f4")
    scales = gaussians["scales"].numpy().astype("<f4")
    quats = gaussians["quats"].numpy().astype("<f4")
    opacities = gaussians["opacities"].numpy().reshape(-1, 1).astype("<f4")
    if not gaussians.get("opacity_logits", False):
        values = np.clip(opacities, 1e-6, 1 - 1e-6)
        opacities = np.log(values / (1 - values)).astype("<f4")
    sh0 = gaussians["sh0"].numpy().reshape(-1, 3).astype("<f4")
    sh_rest = gaussians.get("sh_rest")
    rest = (
        sh_rest.numpy().transpose(0, 2, 1).reshape(len(means), -1).astype("<f4")
        if sh_rest is not None else np.zeros((len(means), 0), dtype="<f4")
    )
    names = ["x", "y", "z", "nx", "ny", "nz"]
    names += [f"f_dc_{i}" for i in range(3)]
    names += [f"f_rest_{i}" for i in range(rest.shape[1])]
    names += ["opacity"] + [f"scale_{i}" for i in range(3)] + [f"rot_{i}" for i in range(4)]
    dtype = np.dtype([(name, "<f4") for name in names])
    vertices = np.empty(len(means), dtype=dtype)
    values = np.concatenate([
        means, np.zeros_like(means), sh0, rest, opacities, scales, quats,
    ], axis=1)
    for index, name in enumerate(names):
        vertices[name] = values[:, index]
    header = ["ply", "format binary_little_endian 1.0", "comment 3D Gaussian Splat"]
    header.append(f"element vertex {len(vertices)}")
    header.extend(f"property float {name}" for name in names)
    header.append("end_header")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    with out_path.open("wb") as handle:
        handle.write(("\n".join(header) + "\n").encode("ascii"))
        vertices.tofile(handle)


def statistical_filter(pcd: o3d.geometry.PointCloud, nb_neighbors: int = 20, std_ratio: float = 2.0):
    cl, _ = pcd.remove_statistical_outlier(nb_neighbors=nb_neighbors, std_ratio=std_ratio)
    return cl
