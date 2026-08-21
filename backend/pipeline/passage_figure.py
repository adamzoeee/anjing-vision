"""2.5D 通行图（passage plan）：房间俯视图 + 可行走区域 + 门→床最短路径 + 通道净宽标注。

与 passage_builder 共用 passage_metrics 的占用栅格与路径算法，绘制：
房间边界、墙、门窗、家具 footprint、可行走区域、门→床路径（红线）、
最窄点标记，并标注通道净宽/路径长度/可行走面积。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon  # noqa: E402
import numpy as np  # noqa: E402
import open3d as o3d  # noqa: E402

from .passage_metrics import analyze_passage, floor_occupancy

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

LABEL_CN = {
    "bed": "床", "wardrobe": "衣柜", "sofa": "沙发", "desk": "书桌", "table": "桌子",
    "cabinet": "柜子", "bookshelf": "书架", "chair": "椅子", "stool": "凳子",
    "small_table": "小桌", "box": "箱子", "storage_rack": "置物架",
    "nightstand": "床头柜", "unknown_obstacle": "箱子",
}


def _box_points(points: np.ndarray, item: dict, margin: float = 0.12) -> np.ndarray:
    center = np.asarray(item["center"], dtype=float)
    half = np.asarray(item["size"], dtype=float) / 2 + margin
    theta = math.radians(float(item.get("rotation_z_deg", 0.0)))
    delta = points - center
    lx = delta[:, 0] * math.cos(theta) + delta[:, 1] * math.sin(theta)
    ly = -delta[:, 0] * math.sin(theta) + delta[:, 1] * math.cos(theta)
    inside = (np.abs(lx) <= half[0]) & (np.abs(ly) <= half[1]) & (np.abs(delta[:, 2]) <= half[2])
    return points[inside]


def render_passage_plan(aligned_ply: Path, structure_json: Path,
                        measurements_json: Path, output_png: Path) -> Path:
    structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
    measurements = json.loads(Path(measurements_json).read_text(encoding="utf-8"))
    cloud = o3d.io.read_point_cloud(str(aligned_ply))
    points = np.asarray(cloud.points, dtype=float)
    scale = float((measurements.get("scale") or {}).get("scale") or 1.0)
    points = points * scale

    door = next(iter(structure.get("doors", [])), None)
    bed = next((o for o in structure.get("objects", []) if o.get("label") == "bed"), None)

    fig, ax = plt.subplots(figsize=(9.5, 7.5), dpi=110)
    ax.set_facecolor("#f6f4ee")

    # 房间边界
    room = structure.get("room", {})
    bounds = room.get("bounds_xy", {})
    lo = np.asarray(bounds.get("min", [0, 0]), dtype=float)
    hi = np.asarray(bounds.get("max", [1, 1]), dtype=float)
    floor_poly = room.get("floor_polygon") or [
        [lo[0], lo[1], 0.0], [hi[0], lo[1], 0.0], [hi[0], hi[1], 0.0], [lo[0], hi[1], 0.0]]
    ax.add_patch(Polygon([[p[0], p[1]] for p in floor_poly], closed=True,
                         facecolor="#eef3f0", edgecolor="#4a5568", linewidth=2.5))

    # 可行走区域（占用栅格自由格，绿色）
    grid, origin, cell = None, None, 0.05
    if door is not None and bed is not None and len(points) > 1000:
        dcenter = np.asarray(door["center"], dtype=float)
        dhalf = np.asarray(door["size"], dtype=float) / 2 + 0.15
        dtheta = math.radians(float(door.get("rotation_z_deg", 0.0)))
        dd = points - dcenter
        dlx = dd[:, 0] * math.cos(dtheta) + dd[:, 1] * math.sin(dtheta)
        dly = -dd[:, 0] * math.sin(dtheta) + dd[:, 1] * math.cos(dtheta)
        door_ids = np.where((np.abs(dlx) <= dhalf[0]) & (np.abs(dly) <= dhalf[1])
                            & (np.abs(dd[:, 2]) <= dhalf[2]))[0]
        grid, origin, cell = floor_occupancy(points, cell_size=0.05, exclude_ids=door_ids)
    if grid is not None and grid.size > 1:
        free = ~grid
        rows, cols = np.where(free)
        xs = origin[0] + cols * cell
        ys = origin[1] + rows * cell
        ax.scatter(xs, ys, s=2.2, c="#b7e4c7", marker="s", linewidths=0, alpha=0.55, zorder=1)

    # 家具 footprint（浅色框 + 中文名）
    for item in structure.get("objects", []):
        c = np.asarray(item["center"], dtype=float)[:2]
        size = np.asarray(item["size"], dtype=float)[:2]
        theta = math.radians(float(item.get("rotation_z_deg", 0.0)))
        corners = [[c[0] + (sx * size[0] / 2) * math.cos(theta) - (sy * size[1] / 2) * math.sin(theta),
                    c[1] + (sx * size[0] / 2) * math.sin(theta) + (sy * size[1] / 2) * math.cos(theta)]
                   for sx in (-1, 1) for sy in (-1, 1)]
        ordered = [corners[0], corners[1], corners[3], corners[2]]
        ax.add_patch(Polygon(ordered, closed=True, facecolor="#f6c453", edgecolor="#b7791f",
                             linewidth=1.4, alpha=0.75, zorder=2))
        label = item.get("label", "object")
        ax.text(c[0], c[1], LABEL_CN.get(label, label), ha="center", va="center",
                fontsize=8.5, color="#3d2e0a", zorder=4)

    # 门窗标记
    for opening in structure.get("doors", []):
        c = np.asarray(opening["center"])[:2]
        w = float(opening["size"][0])
        ax.add_patch(plt.Rectangle((c[0] - w / 2, c[1] - 0.09), w, 0.18,
                                   facecolor="#2b6cb0", edgecolor="white", zorder=5))
        ax.text(c[0], c[1] + 0.16, "门", ha="center", fontsize=9, color="#2b6cb0", zorder=5)
    for opening in structure.get("windows", []):
        c = np.asarray(opening["center"])[:2]
        w = float(opening["size"][0])
        ax.add_patch(plt.Rectangle((c[0] - w / 2, c[1] - 0.09), w, 0.18,
                                   facecolor="#2f855a", edgecolor="white", zorder=5))
        ax.text(c[0], c[1] + 0.16, "窗", ha="center", fontsize=9, color="#2f855a", zorder=5)

    # 门→床路径 + 最窄点
    passage = measurements.get("passage") or {}
    if grid is not None and grid.size > 1 and door is not None and bed is not None:
        door_pts = _box_points(points, door, margin=0.15)
        bed_pts = _box_points(points, bed, margin=0.10)
        try:
            report = analyze_passage(points, door_pts, bed_pts)
            if report.status == "ok" and report.path_3d:
                path = np.asarray(report.path_3d)
                ax.plot(path[:, 0], path[:, 1], color="#e53e3e", linewidth=2.6, zorder=3,
                        label="门→床路径")
                if report.narrowest_point is not None:
                    n = report.narrowest_point
                    ax.scatter([n[0]], [n[1]], marker="X", s=120, color="#c53030",
                               edgecolor="white", linewidths=1.2, zorder=6,
                               label=f"最窄处 {report.passage_width_m}m")
                ax.legend(loc="lower right", fontsize=8.5)
        except Exception:  # noqa: BLE001
            pass

    ax.set_aspect("equal")
    ax.axis("off")
    walkable = measurements.get("walkable_area_m2")
    title = "通行图 · 门 → 床"
    if passage.get("passage_width_m") is not None:
        title += f" | 通道净宽 {passage['passage_width_m']}m"
    if passage.get("path_length_m") is not None:
        title += f" | 路径 {passage['path_length_m']}m"
    if walkable is not None:
        title += f" | 可行走 {walkable}m²"
    ax.set_title(title, fontsize=11.5, color="#2d3748", pad=10)
    fig.tight_layout()
    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, facecolor="#f6f4ee")
    plt.close(fig)
    return output_png


if __name__ == "__main__":
    import sys

    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"D:\部署文件\anjing-vision-3d-fix\backend\data\work\46")
    post = work / "postprocess"
    out = render_passage_plan(
        post / "scene_aligned.ply",
        post / "structure_calibrated.json" if (post / "structure_calibrated.json").is_file() else post / "structure.json",
        post / "measurements.json",
        post / "passage_plan.png",
    )
    print("PASSAGE_PLAN_SAVED", out)
