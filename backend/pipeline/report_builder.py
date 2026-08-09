"""报告资源：Open3D 多视角标注渲染图 + 可交互预览资源（ply + manifest.json）。"""
import json
from pathlib import Path

import numpy as np
import open3d as o3d

LEVEL_COLOR = {"red": [1.0, 0.2, 0.2], "yellow": [1.0, 0.85, 0.1], "green": [0.2, 0.8, 0.3]}


def _make_pcd(points: np.ndarray, colors: np.ndarray | None = None) -> o3d.geometry.PointCloud:
    pcd = o3d.geometry.PointCloud()
    pcd.points = o3d.utility.Vector3dVector(np.asarray(points, dtype=np.float64).reshape(-1, 3))
    if colors is not None and len(colors) == len(points):
        pcd.colors = o3d.utility.Vector3dVector(
            np.clip(np.asarray(colors, dtype=np.float64).reshape(-1, 3), 0.0, 1.0)
        )
    if len(pcd.points) > 0:
        pcd.estimate_normals()
    return pcd


def render_annotation_images(
    points: np.ndarray, risks: list[dict], out_dir: Path, n_views: int = 3,
) -> list[Path]:
    """渲染俯视+侧视多角度点云图，风险点以风险色高亮。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    pcd = _make_pcd(points)
    if len(pcd.points) < 10:
        return []  # 点数过少无法渲染
    colors = np.tile([0.55, 0.6, 0.65], (len(pcd.points), 1))
    step = max(1, len(pcd.points) // 200)
    # 每个 risk 用不同起点偏移染色，避免后面的 risk 整体覆盖前面的颜色
    for i, r in enumerate(risks):
        if r["level"] in LEVEL_COLOR:
            colors[i % step::step] = LEVEL_COLOR[r["level"]]
    pcd.colors = o3d.utility.Vector3dVector(colors)
    bbox = pcd.get_axis_aligned_bounding_box()
    center, extent = bbox.get_center(), bbox.get_extent()
    if np.linalg.norm(extent) < 1e-6:
        return []  # 退化点云（全部重合）无法取视角
    paths = []
    for i in range(n_views):
        theta = i * (2 * np.pi / n_views)
        eye = center + extent * np.array([np.cos(theta), np.sin(theta), 0.35])
        vis = o3d.visualization.Visualizer()
        try:
            vis.create_window(width=1280, height=720, visible=False)
            vis.add_geometry(pcd)
            ctr = vis.get_view_control()
            ctr.set_lookat(center)
            ctr.set_front((eye - center) / np.linalg.norm(eye - center))
            ctr.set_up([0, 0, 1])
            vis.poll_events()
            p = out_dir / f"view_{i}.png"
            vis.capture_screen_image(str(p), do_render=True)
            paths.append(p)
        finally:
            vis.destroy_window()
    return paths


def build_preview_assets(
    points: np.ndarray,
    out_dir: Path,
    title: str,
    colors: np.ndarray | None = None,
    gaussian_filename: str | None = None,
    scale_status: str = "relative",
    quality: dict | None = None,
    cameras: list[dict] | None = None,
    image_shapes: list[tuple[int, int]] | None = None,
    camera_scale: float = 1.0,
) -> dict:
    """打包交互预览：scene.ply + manifest.json（供 App WebGL 渲染器加载）。"""
    out_dir.mkdir(parents=True, exist_ok=True)
    pcd = _make_pcd(points, colors)
    ply_path = out_dir / "scene.ply"
    o3d.io.write_point_cloud(str(ply_path), pcd)
    manifest = {
        "title": title,
        "point_count": len(pcd.points),
        "ply": "scene.ply",
        "unit": "m" if scale_status.startswith("metric") else "relative",
        "scale_status": scale_status,
        "quality": quality or {},
    }
    if gaussian_filename:
        manifest["gaussian_ply"] = gaussian_filename
    if cameras and image_shapes and len(cameras) == len(image_shapes):
        viewer_cameras = []
        for index, (camera, shape) in enumerate(zip(cameras, image_shapes)):
            height, width = shape
            K = np.asarray(camera["K"], dtype=np.float64)
            viewer_cameras.append({
                "id": index,
                "img_name": camera.get("name", str(index)),
                "width": int(width),
                "height": int(height),
                # antimatter15/splat 使用 COLMAP world→camera 的 R、t。
                "position": (np.asarray(camera["t"], dtype=np.float64) * camera_scale).tolist(),
                "rotation": np.asarray(camera["R"], dtype=np.float64).tolist(),
                "fx": float(K[0, 0]),
                "fy": float(K[1, 1]),
            })
        camera_filename = "cameras.json"
        (out_dir / camera_filename).write_text(
            json.dumps(viewer_cameras, ensure_ascii=False), encoding="utf-8"
        )
        manifest["cameras"] = camera_filename
    (out_dir / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2))
    return manifest
