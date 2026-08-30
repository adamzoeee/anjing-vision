"""Render a 2.5D formal-risk overlay from existing structured JSON only."""
from __future__ import annotations

import json
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
from matplotlib.patches import Polygon  # noqa: E402


plt.rcParams["font.sans-serif"] = ["Microsoft YaHei", "SimHei", "DejaVu Sans"]
plt.rcParams["axes.unicode_minus"] = False


LEVEL_COLOR = {"high": "#d73027", "medium": "#f39c12", "low": "#2e9e5b"}


def _object_centers(structure: dict) -> dict[str, list[float]]:
    items = []
    items.extend(structure.get("semantic_instances") or [])
    for key in ("objects", "doors", "windows"):
        items.extend((structure.get("boxes") or {}).get(key) or [])
    centers = {}
    for item in items:
        object_id = item.get("instance_id") or item.get("id")
        center = item.get("center") or item.get("position_xyz")
        if object_id and isinstance(center, list) and len(center) >= 2:
            centers[str(object_id)] = [float(center[0]), float(center[1])]
    return centers


def collect_risk_markers(assessment: dict, structure: dict) -> list[dict]:
    """Resolve evaluated formal risks to known XY evidence without inference."""
    centers = _object_centers(structure)
    markers = []
    for risk in assessment.get("risks") or []:
        if risk.get("assessment_status") != "evaluated":
            continue
        position = risk.get("position") or {}
        xy = position.get("point_xy") or position.get("position_xy")
        center = position.get("center_xyz")
        if xy is None and isinstance(center, list) and len(center) >= 2:
            xy = center[:2]
        if xy is None and position.get("object_id"):
            xy = centers.get(str(position["object_id"]))
        if not isinstance(xy, list) or len(xy) < 2:
            continue
        markers.append({
            "risk_code": risk.get("risk_code"),
            "name": risk.get("risk_name") or risk.get("metric_code"),
            "level": risk.get("risk_level"),
            "xy": [float(xy[0]), float(xy[1])],
            "value": risk.get("measured_value"),
            "unit": risk.get("unit") or "",
        })
    return markers


def render_formal_risk_figure(
    assessment_json: str | Path,
    structure_json: str | Path,
    output_png: str | Path,
) -> Path:
    """Render official risk markers over the accepted structured floor polygon."""
    assessment = json.loads(Path(assessment_json).read_text(encoding="utf-8"))
    structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
    floor = (structure.get("room") or {}).get("floor_polygon") or []
    if len(floor) < 3:
        raise ValueError("structured floor polygon unavailable")
    markers = collect_risk_markers(assessment, structure)

    fig, ax = plt.subplots(figsize=(9, 7), dpi=120)
    ax.set_facecolor("#f7f8fa")
    polygon = Polygon(
        [[float(point[0]), float(point[1])] for point in floor],
        closed=True, facecolor="#eef3f0", edgecolor="#4a5568", linewidth=2.5,
    )
    ax.add_patch(polygon)
    for index, marker in enumerate(markers, start=1):
        color = LEVEL_COLOR.get(marker["level"], "#7a869a")
        x, y = marker["xy"]
        ax.scatter([x], [y], s=170, color=color, edgecolors="white", linewidths=1.5, zorder=3)
        ax.text(x, y, str(index), color="white", ha="center", va="center", fontsize=8, zorder=4)
    legend = [
        f"{index}. {item['name']}：{item['value']}{item['unit']}（{item['level']}）"
        for index, item in enumerate(markers, start=1)
    ]
    if not legend:
        legend = ["当前正式风险项没有可定位的结构化坐标"]
    ax.set_title("安龄智境 · 正式风险位置图", fontsize=15, color="#1f2a44")
    ax.text(0.01, -0.08, "\n".join(legend), transform=ax.transAxes, va="top", fontsize=9)
    xs = [float(point[0]) for point in floor]
    ys = [float(point[1]) for point in floor]
    margin = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.12 or 0.5
    ax.set_xlim(min(xs) - margin, max(xs) + margin)
    ax.set_ylim(min(ys) - margin, max(ys) + margin)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.subplots_adjust(bottom=min(0.35, 0.13 + len(legend) * 0.025))
    output_png = Path(output_png)
    output_png.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(output_png, facecolor="#f7f8fa", bbox_inches="tight")
    plt.close(fig)
    return output_png
