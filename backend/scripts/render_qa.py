"""可视化 QA 工具：对重建结果输出快速验证图（Open3D 离屏渲染）。

用法（backend venv）:
  python scripts/render_qa.py --pcd data/work/<scan>/postprocess/scene_preview.ply \
      --layout data/work/<scan>/postprocess/layout_boxes.json --outdir qa_out
"""
import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d

CATEGORY_COLORS = {
    "wall": (0.31, 0.61, 0.98),
    "door": (1.0, 0.72, 0.02),
    "window": (0.36, 0.91, 0.54),
    "object": (1.0, 0.56, 0.64),
}


def _box_lines(item, color):
    center = np.asarray(item["center"], dtype=np.float64)
    size = np.asarray(item["size"], dtype=np.float64) / 2.0
    theta = np.radians(float(item.get("rotation_z_deg", 0.0)))
    rot = np.array([
        [np.cos(theta), -np.sin(theta), 0],
        [np.sin(theta), np.cos(theta), 0],
        [0, 0, 1],
    ])
    corners = np.array([
        [-1, -1, -1], [1, -1, -1], [1, 1, -1], [-1, 1, -1],
        [-1, -1, 1], [1, -1, 1], [1, 1, 1], [-1, 1, 1],
    ], dtype=np.float64) * size
    corners = corners @ rot.T + center
    edges = [
        (0, 1), (1, 2), (2, 3), (3, 0),
        (4, 5), (5, 6), (6, 7), (7, 4),
        (0, 4), (1, 5), (2, 6), (3, 7),
    ]
    lines = o3d.geometry.LineSet()
    lines.points = o3d.utility.Vector3dVector(corners)
    lines.lines = o3d.utility.Vector2iVector(edges)
    lines.colors = o3d.utility.Vector3dVector([color] * len(edges))
    return lines


def render(pcd_path: Path, layout_path: Path | None, outdir: Path, view: str | None = None) -> None:
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    pcd = o3d.io.read_point_cloud(str(pcd_path))
    if pcd is None or len(pcd.points) == 0:
        raise RuntimeError(f"无法读取 {pcd_path}")

    geometries = [pcd]
    if layout_path and layout_path.is_file():
        data = json.loads(layout_path.read_text(encoding="utf-8"))
        for key, color in (
            ("walls", CATEGORY_COLORS["wall"]),
            ("doors", CATEGORY_COLORS["door"]),
            ("windows", CATEGORY_COLORS["window"]),
        ):
            for item in data.get(key, []):
                geometries.append(_box_lines(item, color))
        for item in data.get("objects", []):
            geometries.append(_box_lines(item, CATEGORY_COLORS["object"]))

    points = np.asarray(pcd.points)
    center = (points.min(axis=0) + points.max(axis=0)) / 2
    diagonal = float(np.linalg.norm(points.max(axis=0) - points.min(axis=0)))
    radius = max(diagonal * 0.9, 1.0)

    # (eye, up)：视线 = center - eye；up 与视线不共线
    views = {
        "perspective": (center + np.array([radius * 0.7, -radius * 0.9, radius * 0.8]), np.array([0.0, 0.0, 1.0])),
        "top": (center + np.array([radius * 0.01, radius * 0.01, radius * 1.25]), np.array([1.0, -1.0, 0.0])),
        "front": (center + np.array([0.0, -radius * 1.15, radius * 0.25]), np.array([0.0, 0.0, 1.0])),
        "side": (center + np.array([radius * 1.15, 0.0, radius * 0.25]), np.array([0.0, 0.0, 1.0])),
    }
    for name, (eye, up) in views.items():
        if view and name != view:
            continue
        vis = o3d.visualization.Visualizer()
        if not vis.create_window(width=1600, height=900, visible=False):
            raise RuntimeError("offscreen window creation failed")
        for index, geometry in enumerate(geometries):
            # 首个几何体必须 reset_bounding_box=True，否则相机近远平面不覆盖场景
            vis.add_geometry(geometry, reset_bounding_box=(index == 0))
        option = vis.get_render_option()
        option.background_color = np.array([0.02, 0.02, 0.03])
        option.point_size = 1.2
        ctr = vis.get_view_control()
        front = center - eye
        front = front / np.linalg.norm(front)
        up = up - np.dot(up, front) * front
        up = up / np.linalg.norm(up)
        ctr.set_lookat(center)
        ctr.set_front(front)
        ctr.set_up(up)
        ctr.set_zoom(0.72)
        vis.poll_events()
        vis.update_renderer()
        image_path = outdir / f"qa_{name}.png"
        vis.capture_screen_image(str(image_path), do_render=True)
        vis.destroy_window()
        print(f"saved {image_path}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--pcd", type=Path, required=True)
    parser.add_argument("--layout", type=Path, default=None)
    parser.add_argument("--outdir", type=Path, default=Path("qa_out"))
    parser.add_argument("--view", type=str, default=None,
                        choices=["perspective", "top", "front", "side"])
    args = parser.parse_args()
    render(args.pcd, args.layout, args.outdir, args.view)
