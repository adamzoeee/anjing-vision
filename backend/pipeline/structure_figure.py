"""2.5D 结构图：按测量结果绘制俯视平面图（中文标注 + 家具尺寸）。"""
from __future__ import annotations

import json
import math
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon, Rectangle  # noqa: E402

plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei"]
plt.rcParams["axes.unicode_minus"] = False

LABEL_CN = {
    "bed": "床", "wardrobe": "衣柜", "sofa": "沙发", "desk": "书桌", "table": "桌子",
    "cabinet": "柜子", "bookshelf": "书架", "chair": "椅子", "stool": "凳子",
    "small_table": "小桌", "box": "箱子", "unknown_obstacle": "箱子",
}
CN_NUM = "一二三四五六七八"


def _name(item: dict, index: int) -> str:
    label = item.get("label", "object")
    cn = LABEL_CN.get(label, label)
    return f"{cn}{CN_NUM[index] if index < len(CN_NUM) else index + 1}"


def render_structure_plan(measurements_json: Path, structure_json: Path, output_png: Path) -> Path:
    measurements = json.loads(Path(measurements_json).read_text(encoding="utf-8"))
    structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
    room = measurements.get("room", {})
    objects = measurements.get("objects", [])
    openings = measurements.get("openings", [])

    fig, ax = plt.subplots(figsize=(9, 7), dpi=110)
    ax.set_facecolor("#f6f4ee")

    length = float(room.get("length_m") or 5)
    width = float(room.get("width_m") or 4)
    cx = (structure.get("room", {}).get("bounds_xy", {}).get("min", [0, 0])[0]
          + structure.get("room", {}).get("bounds_xy", {}).get("max", [length, width])[0]) / 2
    cy = (structure.get("room", {}).get("bounds_xy", {}).get("min", [0, 0])[1]
          + structure.get("room", {}).get("bounds_xy", {}).get("max", [length, width])[1]) / 2
    floor = structure.get("room", {}).get("floor_polygon") or [
        [cx - length / 2, cy - width / 2], [cx + length / 2, cy - width / 2],
        [cx + length / 2, cy + width / 2], [cx - length / 2, cy + width / 2]]
    polygon = Polygon([[p[0], p[1]] for p in floor], closed=True,
                      facecolor="#eef3f0", edgecolor="#4a5568", linewidth=2.5)
    ax.add_patch(polygon)

    for index, opening in enumerate(openings):
        c = opening.get("center") or [cx, cy, 0]
        kind = opening.get("type", "door")
        color = "#2b6cb0" if kind == "door" else "#2f855a"
        width_m = float(opening.get("width_m") or 0.8)
        marker = Rectangle((c[0] - width_m / 2, c[1] - 0.09), width_m, 0.18,
                           facecolor=color, edgecolor="white", linewidth=1.5, alpha=0.9)
        ax.add_patch(marker)
        ax.text(c[0], c[1] + 0.22, f"{'门' if kind == 'door' else '窗'} {width_m:.2f}m",
                ha="center", fontsize=8.5, color=color)

    counted: dict[str, int] = {}
    for item in objects:
        label = item.get("type") or item.get("label") or "object"
        counted[label] = counted.get(label, 0)
        name = _name({"label": label}, counted[label])
        counted[label] += 1
        c = item.get("center") or [cx, cy, 0]
        l_m = float(item.get("length_m") or 0.2)
        w_m = float(item.get("width_m") or 0.2)
        theta = math.radians(float(item.get("rotation_z_deg") or 0.0))
        corners = [
            [c[0] + (sx * l_m / 2) * math.cos(theta) - (sy * w_m / 2) * math.sin(theta),
             c[1] + (sx * l_m / 2) * math.sin(theta) + (sy * w_m / 2) * math.cos(theta)]
            for sx in (-1, 1) for sy in (-1, 1)
        ]
        ordered = [corners[0], corners[1], corners[3], corners[2]]
        poly = Polygon(ordered, closed=True, facecolor="#f6c453", edgecolor="#b7791f",
                       linewidth=1.6, alpha=0.85)
        ax.add_patch(poly)
        ax.text(c[0], c[1], f"{name}\n{l_m:.2f}×{w_m:.2f}m",
                ha="center", va="center", fontsize=8.2, color="#3d2e0a")

    # 无类别的小方块统一按“箱子”画出来（不叫 unknown）：只画落地、有体积的
    # 几何聚类；挂墙/悬空物在结构提取阶段已过滤，这里不再出现。
    box_count = 0
    for item in structure.get("geometric_obstacles", []):
        c = item.get("center")
        size = item.get("size")
        if not c or not size or len(size) < 3:
            continue
        box_count += 1
        l_m = max(float(size[0]), float(size[1]))
        w_m = min(float(size[0]), float(size[1]))
        if l_m < 0.08 or w_m < 0.08:
            continue
        theta = math.radians(float(item.get("rotation_z_deg") or 0.0))
        corners = [
            [c[0] + (sx * l_m / 2) * math.cos(theta) - (sy * w_m / 2) * math.sin(theta),
             c[1] + (sx * l_m / 2) * math.sin(theta) + (sy * w_m / 2) * math.cos(theta)]
            for sx in (-1, 1) for sy in (-1, 1)
        ]
        ordered = [corners[0], corners[1], corners[3], corners[2]]
        poly = Polygon(ordered, closed=True, facecolor="#cbd5e0", edgecolor="#4a5568",
                       linewidth=1.2, alpha=0.8)
        ax.add_patch(poly)
        ax.text(c[0], c[1], f"箱子\n{l_m:.2f}×{w_m:.2f}m",
                ha="center", va="center", fontsize=7.4, color="#1a202c")

    lo = min(p[0] for p in floor)
    hi = max(p[0] for p in floor)
    lo_y = min(p[1] for p in floor)
    hi_y = max(p[1] for p in floor)
    ax.text((lo + hi) / 2, lo_y - 0.28,
            f"房间 {length:.2f}m × {width:.2f}m × {room.get('height_m') or 0:.2f}m（高）",
            ha="center", fontsize=10.5, color="#2d3748")
    ax.set_xlim(lo - 0.6, hi + 0.6)
    ax.set_ylim(lo_y - 0.7, hi_y + 0.5)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.tight_layout()
    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, facecolor="#f6f4ee")
    plt.close(fig)
    return output_png


if __name__ == "__main__":
    import sys

    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"D:\部署文件\anjing-vision-3d-fix\backend\data\work\40")
    post = work / "postprocess"
    out = render_structure_plan(
        post / "measurements.json",
        post / "structure_calibrated.json" if (post / "structure_calibrated.json").is_file() else post / "structure.json",
        post / "structure_plan.png",
    )
    print("PLAN_SAVED", out)
