"""报告资源：Open3D 多视角标注渲染图 + 可交互预览资源（ply + manifest.json）。"""
import json
from pathlib import Path

import numpy as np
import open3d as o3d

LEVEL_COLOR = {"red": [1.0, 0.2, 0.2], "yellow": [1.0, 0.85, 0.1], "green": [0.2, 0.8, 0.3]}


def _make_pcd(points: np.ndarray) -> o3d.geometry.PointCloud:
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.asarray(points, dtype=np.float64).reshape(-1, 3))
    pcd.estimate_normals()
    return pcd


def render_annotation_images(
    points: np.ndarray, risks: list[dict], out_dir: Path, n_views: int = 3,
) -> list[Path]:
    """渲染俯视+侧视多角度点云图，风险点以风险色高亮。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    pcd = _make_pcd(points)
    colors = np.tile([0.55, 0.6, 0.65], (len(pcd.points), 1))
    for r in risks:
        if r["level"] in LEVEL_COLOR:
            colors[:: max(1, len(pcd.points) // 200)] = LEVEL_COLOR[r["level"]]
    pcd.colors = o3d.utility.Vector3dVector(colors)
    bbox = pcd.get_axis_aligned_bounding_box()
    center, extent = bbox.get_center(), bbox.get_extent()
    paths = []
    for i in range(n_views):
        theta = i * (2 * np.pi / n_views)
        eye = center + extent * np.array([np.cos(theta), np.sin(theta), 0.35])
        vis = o3d.visualization.Visualizer()
        vis.create_window(width=1280, height=720, visible=False)
        vis.add_geometry(pcd)
        ctr = vis.get_view_control()
        ctr.set_lookat(center)
        ctr.set_front((eye - center) / np.linalg.norm(eye - center))
        ctr.set_up([0, 0, 1])
        vis.poll_events()
        p = out_dir / f"view_{i}.png"
        vis.capture_screen_image(str(p), do_render=True)
        vis.destroy_window()
        paths.append(p)
    return paths


def build_preview_assets(points: np.ndarray, out_dir: Path, title: str) -> dict:
    """打包交互预览：scene.ply + manifest.json（供 App WebGL 渲染器加载）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    pcd = _make_pcd(points)
    ply_path = out_dir / "scene.ply"
    o3d.io.write_point_cloud(str(ply_path), pcd)
    manifest = {
        "title": title,
        "point_count": len(pcd.points),
        "ply": "scene.ply",
        "unit": "m",
    }
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest
