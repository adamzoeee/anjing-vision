"""Conservatively complete the two remaining scan-46 display holes.

This is a preview-only completion: it samples only inside the verified bed footprint
and foreground floor ROI, interpolates from nearby registered surface samples, and
never changes or deletes an accepted point.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree


def object_by_label(structure: dict, label: str) -> dict:
    for item in structure.get("objects", []):
        if item.get("label") == label or item.get("instance_id", "").startswith(label):
            return item
    raise RuntimeError(f"Missing verified {label} in structure.json")


def grid_rotated_box(center: np.ndarray, size: np.ndarray, angle_deg: float, step: float) -> np.ndarray:
    u = np.arange(-size[0] / 2 + step / 2, size[0] / 2, step)
    v = np.arange(-size[1] / 2 + step / 2, size[1] / 2, step)
    uu, vv = np.meshgrid(u, v, indexing="xy")
    local = np.column_stack([uu.ravel(), vv.ravel()])
    a = np.deg2rad(angle_deg)
    rot = np.asarray([[np.cos(a), -np.sin(a)], [np.sin(a), np.cos(a)]])
    return local @ rot.T + center[:2]


def interpolate_surface(existing_xyz: np.ndarray, existing_rgb: np.ndarray, query_xy: np.ndarray,
                        source_mask: np.ndarray, min_gap: float, max_gap: float) -> tuple[np.ndarray, np.ndarray]:
    source_xyz = existing_xyz[source_mask]
    source_rgb = existing_rgb[source_mask]
    if len(source_xyz) < 100:
        return np.empty((0, 3)), np.empty((0, 3))
    source_tree = cKDTree(source_xyz[:, :2])
    distance, ids = source_tree.query(query_xy, workers=-1)
    all_tree = cKDTree(existing_xyz)
    xyz = np.column_stack([query_xy, source_xyz[ids, 2]])
    nearest3d, _ = all_tree.query(xyz, workers=-1)
    keep = (distance <= max_gap) & (nearest3d >= min_gap)
    return xyz[keep], source_rgb[ids[keep]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("candidate", type=Path)
    parser.add_argument("structure", type=Path)
    parser.add_argument("candidate_report", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    cloud = o3d.io.read_point_cloud(str(args.candidate))
    xyz, rgb = np.asarray(cloud.points), np.asarray(cloud.colors)
    structure = json.loads(args.structure.read_text(encoding="utf-8"))
    report = json.loads(args.candidate_report.read_text(encoding="utf-8"))
    bed = object_by_label(structure, "bed")

    bed_center = np.asarray(bed["center"], dtype=float)
    bed_size = np.asarray(bed["size"], dtype=float)
    bed_xy = grid_rotated_box(bed_center, bed_size, float(bed.get("rotation_z_deg", 0.0)), 0.014)
    # Use only the registered upper quilt/bed samples, never nearby floor or wall.
    bed_source = (
        (xyz[:, 2] >= max(0.24, bed_center[2] - bed_size[2] * 0.45))
        & (xyz[:, 2] <= bed_center[2] + bed_size[2] * 1.35)
        & (xyz[:, 0] >= bed_center[0] - bed_size[0] * 0.65)
        & (xyz[:, 0] <= bed_center[0] + bed_size[0] * 0.65)
        & (xyz[:, 1] >= bed_center[1] - bed_size[1] * 0.65)
        & (xyz[:, 1] <= bed_center[1] + bed_size[1] * 0.65)
    )
    bed_xyz, bed_rgb = interpolate_surface(xyz, rgb, bed_xy, bed_source, 0.020, 0.20)
    bed_spec = report["regions"]["bed_gap"]
    bed_lo, bed_hi = np.asarray(bed_spec["lower"]), np.asarray(bed_spec["upper"])
    bed_keep = np.all((bed_xyz >= bed_lo) & (bed_xyz <= bed_hi), axis=1)
    bed_xyz, bed_rgb = bed_xyz[bed_keep], bed_rgb[bed_keep]

    floor_spec = report["regions"]["foreground_floor"]
    lo, hi = np.asarray(floor_spec["lower"]), np.asarray(floor_spec["upper"])
    gx = np.arange(lo[0] + 0.007, hi[0], 0.014)
    gy = np.arange(lo[1] + 0.007, hi[1], 0.014)
    xx, yy = np.meshgrid(gx, gy, indexing="xy")
    floor_xy = np.column_stack([xx.ravel(), yy.ravel()])
    floor_source = (
        (xyz[:, 0] >= lo[0] - 0.10) & (xyz[:, 0] <= hi[0] + 0.10)
        & (xyz[:, 1] >= lo[1] - 0.10) & (xyz[:, 1] <= hi[1] + 0.10)
        & (xyz[:, 2] >= -0.06) & (xyz[:, 2] <= 0.10)
    )
    floor_xyz, floor_rgb = interpolate_surface(xyz, rgb, floor_xy, floor_source, 0.020, 0.24)

    additions_xyz = np.vstack([bed_xyz, floor_xyz])
    additions_rgb = np.vstack([bed_rgb, floor_rgb])
    result = o3d.geometry.PointCloud()
    result.points = o3d.utility.Vector3dVector(np.vstack([xyz, additions_xyz]))
    result.colors = o3d.utility.Vector3dVector(np.vstack([rgb, additions_rgb]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(args.output), result, write_ascii=False, compressed=False)
    diagnostics = {
        "status": "candidate_ready",
        "method": "video_evidence_constrained_local_surface_completion",
        "input_points": int(len(xyz)),
        "bed_hole_points": int(len(bed_xyz)),
        "foreground_floor_hole_points": int(len(floor_xyz)),
        "output_points": int(len(xyz) + len(additions_xyz)),
        "existing_points_modified_or_deleted": 0,
        "outside_six_regions_unchanged": True,
        "promotion_requires_visual_qa": True,
        "regions": report["regions"],
    }
    args.output.with_suffix(".json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
