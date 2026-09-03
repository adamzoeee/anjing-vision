"""SLAM3R 稠密点云后处理：去噪、方向对齐（z-up + 墙面贴轴）、米制缩放与预览导出。

对齐策略（不依赖任何外部标定）：
  1. 估计逐点法向，在单位球上做直方图投票；
  2. RANSAC 提取前 K 个主平面，取「法向直方图支持度最高」的平面法向为竖直轴
     （地板+天花板法向同簇，支持度天然高于任一墙面方向）；
  3. 旋转至 z-up 后，用地板平面把地面平移到 z=0；
  4. 用竖墙法向的水平投影直方图把墙面转到 x/y 轴（Manhattan 对齐）；
  5. 以层高（默认 2.6m，可用 SLAM3R_TARGET_HEIGHT_M 覆盖）恢复米制尺度。
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np

logger = logging.getLogger("anjing.pipeline.postprocess")

try:
    import open3d as o3d
except ImportError as exc:  # 后端 venv 必须安装 open3d（requirements.txt 已含）
    raise RuntimeError("缺少 open3d，无法做点云后处理（pip install open3d==0.19.*）") from exc

TARGET_HEIGHT_M = float(__import__("os").environ.get("SLAM3R_TARGET_HEIGHT_M", "2.6"))
PREVIEW_VOXEL_M = float(__import__("os").environ.get("PREVIEW_VOXEL_M", "0.008"))
PREVIEW_MAX_POINTS = int(__import__("os").environ.get("PREVIEW_MAX_POINTS", "3000000"))
SPATIALLM_VOXEL_M = float(__import__("os").environ.get("SPATIALLM_VOXEL_M", "0.005"))


def load_ply(ply_path: Path) -> o3d.geometry.PointCloud:
    ply_path = Path(ply_path)
    pcd = o3d.io.read_point_cloud(str(ply_path))
    if pcd is None or len(pcd.points) == 0:
        raise RuntimeError(f"无法读取点云：{ply_path}")
    return pcd


def denoise(pcd: o3d.geometry.PointCloud, nb_neighbors: int = 20, std_ratio: float = 1.6) -> tuple:
    """统计离群点剔除（SLAM3R 官方建议），返回 (clean, 剔除点数)。"""
    before = len(pcd.points)
    pcd, trace = pcd.remove_statistical_outlier(nb_neighbors=nb_neighbors, std_ratio=std_ratio)
    removed = before - len(pcd.points)
    logger.info("denoise removed=%d kept=%d", removed, len(pcd.points))
    if len(pcd.points) < 5000:
        raise RuntimeError(f"去噪后点云过少（{len(pcd.points)}），重建质量不足")
    return pcd, removed


def _estimate_up_axis(points: np.ndarray, normals: np.ndarray, top_k: int = 4) -> np.ndarray:
    """用「RANSAC 主平面 × 法向直方图」估计竖直轴（返回单位向量）。

    对每个 RANSAC 平面候选，统计全点云法向中与 ±候选方向夹角 < 22.5° 的点数，
    取支持度最高的候选作为竖直轴。地板与天花板的法向互为反向，
    因此竖直方向的支持度约为任一墙面方向的两倍以上，天然鲁棒。
    """
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    pcd.normals = o3d.utility.Vector3dVector(normals)

    candidates: list[np.ndarray] = []
    remaining = pcd
    for _ in range(top_k):
        try:
            plane, inliers = remaining.segment_plane(
                distance_threshold=0.02 * np.median(np.linalg.norm(points, axis=1) + 1e-6),
                ransac_n=3,
                num_iterations=800,
            )
        except RuntimeError:
            break
        normal = np.asarray(plane[:3], dtype=np.float64)
        normal = normal / (np.linalg.norm(normal) + 1e-12)
        if len(inliers) < 2000:
            break
        candidates.append(normal)
        remaining = remaining.select_by_index(inliers, invert=True)
        if len(remaining.points) < 5000:
            break
    if not candidates:
        # 兜底：取法向直方图最大值
        candidates = [_normal_histogram_peak(normals)]

    def support(direction: np.ndarray) -> int:
        dots = np.abs(normals @ direction)
        return int((dots > 0.9239).sum())  # cos(22.5°)

    best = max(candidates, key=support)
    logger.info("up_axis candidates=%d support=%d", len(candidates), support(best))
    return best


def _normal_histogram_peak(normals: np.ndarray) -> np.ndarray:
    """在单位球离散网格上找法向最密集的方向（无 RANSAC 时的兜底）。"""
    step = 10  # 度
    best_dir, best_count = None, -1
    for theta in range(0, 180, step):
        for phi in range(0, 360, step):
            t, p = np.radians(theta), np.radians(phi)
            direction = np.array([np.sin(t) * np.cos(p), np.sin(t) * np.sin(p), np.cos(t)])
            count = int((np.abs(normals @ direction) > np.cos(np.radians(15))).sum())
            if count > best_count:
                best_count, best_dir = count, direction
    return best_dir if best_dir is not None else np.array([0.0, 0.0, 1.0])


def _rotation_to_z(direction: np.ndarray) -> np.ndarray:
    """返回把 direction 转到 +z 的最小旋转矩阵（Rodrigues）。"""
    direction = direction / (np.linalg.norm(direction) + 1e-12)
    z = np.array([0.0, 0.0, 1.0])
    v = np.cross(direction, z)
    c = float(np.dot(direction, z))
    vx = np.array([[0, -v[2], v[1]], [v[2], 0, -v[0]], [-v[1], v[0], 0]], dtype=np.float64)
    if np.linalg.norm(v) < 1e-9:
        return np.eye(3) if c > 0 else np.diag([1.0, -1.0, -1.0])
    return np.eye(3) + vx + vx @ vx * ((1 - c) / (np.dot(v, v) + 1e-12))


def _align_walls(points: np.ndarray, normals: np.ndarray) -> float:
    """把墙面转到 x/y 轴，返回绕 z 的旋转角（弧度）。

    只用竖直墙面的法向（|n_z| 小）做水平投影直方图，取主导角度。
    """
    vertical_walls = np.abs(normals[:, 2]) < 0.35
    if vertical_walls.sum() < 500:
        return 0.0
    wall_normals = normals[vertical_walls][:, :2]
    lengths = np.linalg.norm(wall_normals, axis=1) + 1e-9
    wall_normals = wall_normals / lengths[:, None]
    angles = np.arctan2(wall_normals[:, 1], wall_normals[:, 0])
    # 周期性角度直方图
    bins = np.linspace(-np.pi / 2, np.pi / 2, 91)  # 法向模 π 即可
    hist, edges = np.histogram(((angles + np.pi / 2) % np.pi) - np.pi / 2, bins=bins)
    mode = (edges[:-1] + edges[1:]) / 2
    theta = float(mode[np.argmax(hist)])
    logger.info("wall_alignment angle_deg=%.1f wall_points=%d", np.degrees(theta), int(vertical_walls.sum()))
    return theta


def align_scene(points: np.ndarray) -> dict:
    """估计并应用「z-up + 地面平移到 0 + 墙面贴轴」的对齐。

    返回 {"points": 对齐后的点, "rotation": (3,3), "theta": 墙面旋转角,
    "up_axis": 竖直轴, "floor_z": 地面高度, "normals": 对齐后的法向}。
    """
    points = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(points)
    radius = float(np.percentile(np.linalg.norm(points - np.median(points, axis=0), axis=1), 90) * 0.15 + 1e-4)
    pcd.estimate_normals(search_param=o3d.geometry.KDTreeSearchParamHybrid(radius=max(radius, 0.02), max_nn=30))
    normals = np.asarray(pcd.normals)

    up_axis = _estimate_up_axis(points, normals)
    rotation = _rotation_to_z(up_axis)
    aligned = points @ rotation.T
    aligned_normals = normals @ rotation.T

    # 确定地板：竖直方向最低的主平面（RANSAC 在 z-up 后对低处点拟合）
    low_mask = aligned[:, 2] < np.percentile(aligned[:, 2], 35)
    if low_mask.sum() < 3000:
        floor_z = float(np.percentile(aligned[:, 2], 1))
    else:
        plane_pcd = o3d.geometry.PointCloud()
        plane_pcd.points = o3d.utility.Vector3dVector(aligned[low_mask])
        plane, inliers = plane_pcd.segment_plane(distance_threshold=0.03, ransac_n=3, num_iterations=600)
        a, b, c, d = (float(v) for v in plane)
        if abs(c) > 0.5:  # 近似水平面
            floor_z = float(-d / c) if abs(c) > 1e-9 else 0.0
        else:
            floor_z = float(np.percentile(aligned[:, 2], 1))
    aligned[:, 2] -= floor_z

    # 墙面贴轴
    theta = _align_walls(aligned, aligned_normals)
    if abs(theta) > 1e-3:
        cos_t, sin_t = np.cos(-theta), np.sin(-theta)
        rz = np.array([[cos_t, -sin_t, 0], [sin_t, cos_t, 0], [0, 0, 1]], dtype=np.float64)
        aligned = aligned @ rz.T
        rotation = rz @ rotation

    return {
        "points": aligned,
        "rotation": rotation,
        "theta": theta,
        "up_axis": up_axis,
        "floor_z": floor_z,
        "normals": aligned_normals @ (np.array(
            [[np.cos(-theta), -np.sin(-theta), 0], [np.sin(-theta), np.cos(-theta), 0], [0, 0, 1]], dtype=np.float64
        ).T if abs(theta) > 1e-3 else np.eye(3)),
    }


def metric_scale(points: np.ndarray, target_height_m: float = TARGET_HEIGHT_M) -> dict:
    """以「层高 = 点云 z 的稳健上界」恢复米制尺度。"""
    z = points[:, 2]
    ceiling = float(np.percentile(z, 98))
    floor = float(np.percentile(z, 1))
    height = max(ceiling - floor, 0.1)
    scale = target_height_m / height
    points_scaled = points * scale
    return {"points": points_scaled, "scale": scale, "measured_height": height, "target_height_m": target_height_m}


def build_outputs(
    raw_ply: Path,
    out_dir: Path,
    *,
    target_height_m: float = TARGET_HEIGHT_M,
    preview_voxel_m: float = PREVIEW_VOXEL_M,
    preview_max_points: int = PREVIEW_MAX_POINTS,
    spatiallm_voxel_m: float = SPATIALLM_VOXEL_M,
) -> dict:
    """完整后处理：读取 SLAM3R PLY → 去噪 → 对齐 → 缩放 → 导出。

    产物：scene_aligned.ply（供 SpatialLM）、scene_preview.ply（供 Web 预览）、
    alignment.json（对齐/缩放元数据，供预览与下游使用）。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    np.random.seed(42)  # RANSAC/采样可复现
    pcd = load_ply(raw_ply)
    pcd, removed = denoise(pcd)

    points = np.asarray(pcd.points, dtype=np.float64)
    colors = np.asarray(pcd.colors, dtype=np.float64).reshape(-1, 3)

    alignment = align_scene(points)
    points = alignment["points"]
    scale_info = metric_scale(points, target_height_m=target_height_m)
    points = scale_info["points"]

    # 供 SpatialLM 的完整点云（轻度体素化，5mm）
    # 用 tensor API 写出 float32 PLY：legacy 写入器会把点强制转成 double
    # （property double x/y/z），部分前端解析器只认 float 会造成步长错位。
    aligned_pcd = o3d.geometry.PointCloud()
    aligned_pcd.points = o3d.utility.Vector3dVector(points)
    aligned_pcd.colors = o3d.utility.Vector3dVector(colors)
    aligned_pcd = aligned_pcd.voxel_down_sample(spatiallm_voxel_m)
    aligned_ply = out_dir / "scene_aligned.ply"
    o3d.t.io.write_point_cloud(str(aligned_ply), o3d.t.geometry.PointCloud.from_legacy(aligned_pcd))

    # 供 Web 预览的高密度点云（8mm 体素 + 上限）
    preview_pcd = aligned_pcd.voxel_down_sample(preview_voxel_m)
    preview_points = np.asarray(preview_pcd.points)
    if len(preview_points) > preview_max_points:
        keep = np.random.default_rng(42).choice(len(preview_points), preview_max_points, replace=False)
        preview_pcd = preview_pcd.select_by_index(keep)
    preview_ply = out_dir / "scene_preview.ply"
    o3d.t.io.write_point_cloud(str(preview_ply), o3d.t.geometry.PointCloud.from_legacy(preview_pcd))

    extents = np.percentile(points, [1, 99], axis=0)
    metadata = {
        "backend": "slam3r+postprocess",
        "denoised_removed_points": removed,
        "alignment": {
            "up_axis": alignment["up_axis"].tolist(),
            "rotation": alignment["rotation"].tolist(),
            "wall_theta_deg": float(np.degrees(alignment["theta"])),
        },
        "scale": {
            "applied": scale_info["scale"],
            "method": "ceiling_height",
            "target_height_m": scale_info["target_height_m"],
            "measured_height": scale_info["measured_height"],
        },
        "coordinate_unit": "meters",
        "z_up": True,
        "extents_m": {
            "x": [float(extents[0, 0]), float(extents[1, 0])],
            "y": [float(extents[0, 1]), float(extents[1, 1])],
            "z": [float(extents[0, 2]), float(extents[1, 2])],
        },
        "points_aligned": int(len(points)),
        "points_preview": int(len(np.asarray(preview_pcd.points))),
        "points_spatiallm": int(len(np.asarray(aligned_pcd.points))),
    }
    (out_dir / "alignment.json").write_text(
        json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    logger.info(
        "postprocess_done aligned=%d preview=%d scale=%.4f",
        len(points), len(np.asarray(preview_pcd.points)), scale_info["scale"],
    )
    return {
        "aligned_ply": aligned_ply,
        "preview_ply": preview_ply,
        "metadata": metadata,
    }


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser("SLAM3R 点云后处理 CLI")
    parser.add_argument("ply", type=Path)
    parser.add_argument("--outdir", type=Path, default=None)
    parser.add_argument("--height", type=float, default=TARGET_HEIGHT_M)
    args = parser.parse_args()
    out = args.outdir or args.ply.parent
    result = build_outputs(args.ply, out, target_height_m=args.height)
    print(json.dumps(result["metadata"], ensure_ascii=False, indent=2))
