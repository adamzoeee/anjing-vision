"""通路与净距测量：基于 structure（米制）+ 对齐点云，补全 measurements.json。

① 家具间净距离：footprint（绕 z 的 2D 矩形）边缘最短距离（SAT）；
② 可行走区域：地面占用栅格自由格面积；
③ 门→床最短路径长度与沿路最窄通道净宽（复用 passage_metrics）。
"""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np
import open3d as o3d

from .passage_metrics import analyze_passage, floor_occupancy

_CEILING_LABELS = {"chandelier", "curtain", "窗帘", "吊灯", "灯", "lamp"}


def _obb2d_distance(center_a, size_a, rot_a, center_b, size_b, rot_b) -> float:
    """两个绕 z 旋转的 2D 矩形边缘最短距离（分离轴定理）。"""
    def corners(center, size, rot):
        c, s = math.cos(math.radians(rot)), math.sin(math.radians(rot))
        r = np.array([[c, s], [-s, c]], dtype=np.float64)
        cx, cy = center[0], center[1]
        return np.array([
            [cx + sx * size[0] / 2 * c - sy * size[1] / 2 * s,
             cy + sx * size[0] / 2 * s + sy * size[1] / 2 * c]
            for sx in (-1, 1) for sy in (-1, 1)
        ])
    rects = [corners(center_a, size_a, rot_a), corners(center_b, size_b, rot_b)]
    separated = False
    min_gap = float("inf")
    for rect in rects:
        for i in range(4):
            edge = rect[(i + 1) % 4] - rect[i]
            normal = np.array([-edge[1], edge[0]], dtype=np.float64)
            normal /= max(np.linalg.norm(normal), 1e-12)
            projections = [corners @ normal for corners in rects]
            lo = max(p.min() for p in projections)
            hi = min(p.max() for p in projections)
            if lo > hi + 1e-9:
                separated = True
                min_gap = min(min_gap, float(lo - hi))
    if separated:
        return min_gap
    return 0.0


def _box_points(points: np.ndarray, item: dict, margin: float = 0.12) -> np.ndarray:
    center = np.asarray(item["center"], dtype=float)
    half = np.asarray(item["size"], dtype=float) / 2 + margin
    theta = math.radians(float(item.get("rotation_z_deg", 0.0)))
    delta = points - center
    lx = delta[:, 0] * math.cos(theta) + delta[:, 1] * math.sin(theta)
    ly = -delta[:, 0] * math.sin(theta) + delta[:, 1] * math.cos(theta)
    inside = (np.abs(lx) <= half[0]) & (np.abs(ly) <= half[1]) & (np.abs(delta[:, 2]) <= half[2])
    return points[inside]


def build_passage_metrics(
    aligned_ply: Path,
    structure_json: Path,
    measurements_json: Path,
) -> dict:
    """把净距/可行走面积/通道指标合并进 measurements.json 并返回。"""
    structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
    measurements = json.loads(Path(measurements_json).read_text(encoding="utf-8"))
    scale = float((measurements.get("scale") or {}).get("scale") or 1.0)

    cloud = o3d.io.read_point_cloud(str(aligned_ply))
    points = np.asarray(cloud.points, dtype=float) * scale  # → 米

    objects = [o for o in structure.get("objects", []) if o.get("label") not in _CEILING_LABELS]
    objects += [o for o in structure.get("geometric_obstacles", [])]

    # ① 家具间净距离（米制 footprint 边缘距）
    distances = []
    for i in range(len(objects)):
        for j in range(i + 1, len(objects)):
            a, b = objects[i], objects[j]
            gap = _obb2d_distance(
                np.asarray(a["center"]), np.asarray(a["size"]), float(a.get("rotation_z_deg", 0.0)),
                np.asarray(b["center"]), np.asarray(b["size"]), float(b.get("rotation_z_deg", 0.0)),
            )
            distances.append({
                "between": [a.get("label", a.get("instance_id")), b.get("label", b.get("instance_id"))],
                "clearance_m": round(gap, 3),
            })
    distances.sort(key=lambda item: item["clearance_m"])

    # ②/③ 可行走区域 + 门→床通道
    passage = {"status": "pending", "reason": "数据不足"}
    walkable = None
    door = next(iter(structure.get("doors", [])), None)
    bed = next((o for o in structure.get("objects", []) if o.get("label") == "bed"), None)
    if door is not None and bed is not None and len(points) > 1000:
        try:
            door_pts = _box_points(points, door, margin=0.15)
            bed_pts = _box_points(points, bed, margin=0.10)
            # 门洞区域必须从占用栅格中排除，否则墙面把出入口堵死（向量化 O(N)）
            dcenter = np.asarray(door["center"], dtype=float)
            dhalf = np.asarray(door["size"], dtype=float) / 2 + 0.15
            dtheta = math.radians(float(door.get("rotation_z_deg", 0.0)))
            dd = points - dcenter
            dlx = dd[:, 0] * math.cos(dtheta) + dd[:, 1] * math.sin(dtheta)
            dly = -dd[:, 0] * math.sin(dtheta) + dd[:, 1] * math.cos(dtheta)
            door_ids = np.where(
                (np.abs(dlx) <= dhalf[0]) & (np.abs(dly) <= dhalf[1]) & (np.abs(dd[:, 2]) <= dhalf[2])
            )[0]
            grid, origin, cell = floor_occupancy(points, cell_size=0.05, exclude_ids=door_ids)
            walkable = round(float((~grid).sum()) * cell * cell, 2)
            report = analyze_passage(points, door_pts, bed_pts)
            passage = {
                "status": report.status,
                "reason": report.reason or None,
                "passage_width_m": report.passage_width_m,
                "narrowest_point": report.narrowest_point,
                "path_length_m": report.path_length_m,
                "threshold_m": report.threshold_m,
                "stairs_exist": report.stairs_exist,
                "slope": report.slope,
            }
        except Exception as exc:  # noqa: BLE001
            passage = {"status": "pending", "reason": str(exc)[:200]}

    measurements["distances"] = distances[:24]
    measurements["passage"] = passage
    measurements["walkable_area_m2"] = walkable
    Path(measurements_json).write_text(
        json.dumps(measurements, ensure_ascii=False, indent=2), encoding="utf-8")
    return measurements


if __name__ == "__main__":
    import sys

    work = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(
        r"D:\部署文件\anjing-vision-3d-fix\backend\data\work\40")
    post = work / "postprocess"
    result = build_passage_metrics(
        post / "scene_aligned.ply",
        post / "structure_calibrated.json" if (post / "structure_calibrated.json").is_file() else post / "structure.json",
        post / "measurements.json",
    )
    print(json.dumps({"passage": result["passage"], "walkable": result["walkable_area_m2"],
                      "distances": result["distances"][:6]}, ensure_ascii=False))
