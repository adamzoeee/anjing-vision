"""Build a conservative display candidate for scan 46.

The accepted 08:55 cloud is immutable.  This script only inserts samples in
gaps on the physical floor, four walls and the measured bed top.  Colours are
interpolated from nearby accepted points on the same surface; openings are
kept open and no ceiling is created.  The output is a candidate until the
60%-point-size review passes.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree


def grid(a0: float, a1: float, b0: float, b1: float, step: float) -> tuple[np.ndarray, np.ndarray]:
    a = np.arange(a0, a1 + step * .5, step)
    b = np.arange(b0, b1 + step * .5, step)
    aa, bb = np.meshgrid(a, b, indexing="xy")
    return aa.ravel(), bb.ravel()


def interpolate_surface(
    candidates: np.ndarray,
    reference_xyz: np.ndarray,
    reference_rgb: np.ndarray,
    gap_tree: cKDTree,
    min_gap: float,
    max_colour_distance: float,
) -> tuple[np.ndarray, np.ndarray]:
    gap, _ = gap_tree.query(candidates, workers=-1)
    missing = gap >= min_gap
    candidates = candidates[missing]
    if not len(candidates) or not len(reference_xyz):
        return np.empty((0, 3)), np.empty((0, 3))
    tree = cKDTree(reference_xyz)
    distance, ids = tree.query(candidates, k=min(12, len(reference_xyz)), workers=-1)
    if distance.ndim == 1:
        distance, ids = distance[:, None], ids[:, None]
    keep = distance[:, 0] <= max_colour_distance
    candidates, distance, ids = candidates[keep], distance[keep], ids[keep]
    weight = 1.0 / np.maximum(distance, .008) ** 2
    colours = np.sum(reference_rgb[ids] * weight[..., None], axis=1) / np.sum(weight, axis=1)[:, None]
    # Reject colourful interpolation on structural surfaces.  This prevents a
    # nearby object from being smeared across a wall/floor hole.
    spread = np.max(reference_rgb[ids], axis=2) - np.min(reference_rgb[ids], axis=2)
    stable = np.median(spread, axis=1) < .42
    return candidates[stable], np.clip(colours[stable], 0, 1)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("baseline", type=Path)
    p.add_argument("alignment", type=Path)
    p.add_argument("structure", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--step", type=float, default=.010)
    p.add_argument("--min-gap", type=float, default=.025)
    args = p.parse_args()

    cloud = o3d.io.read_point_cloud(str(args.baseline))
    xyz = np.asarray(cloud.points, np.float64)
    rgb = np.asarray(cloud.colors, np.float64)
    alignment = json.loads(args.alignment.read_text(encoding="utf-8"))
    structure = json.loads(args.structure.read_text(encoding="utf-8"))
    ext = alignment["extents_m"]
    xmin, xmax = map(float, ext["x"]); ymin, ymax = map(float, ext["y"])
    zmin, zmax = 0.0, float(ext["z"][1])
    all_tree = cKDTree(xyz)
    additions_xyz: list[np.ndarray] = []
    additions_rgb: list[np.ndarray] = []
    counts: dict[str, int] = {}

    # Floor.  Reference points must be close to the measured floor, so beds or
    # cabinets cannot supply colours.  The slight negative offset avoids
    # z-fighting with accepted points while remaining metrically negligible.
    floor_z = float(np.median(xyz[(xyz[:, 2] > -.08) & (xyz[:, 2] < .08), 2]))
    gx, gy = grid(xmin, xmax, ymin, ymax, args.step)
    floor = np.column_stack([gx, gy, np.full(len(gx), floor_z - .003)])
    ref = np.abs(xyz[:, 2] - floor_z) <= .045
    add, col = interpolate_surface(floor, xyz[ref], rgb[ref], all_tree, args.min_gap, .34)
    additions_xyz.append(add); additions_rgb.append(col); counts["floor"] = len(add)

    # Openings in the current metric point-cloud frame.  structure.json has a
    # manually expanded Y extent, but its opening X/Z measurements are valid.
    door = (structure.get("doors") or [{}])[0]
    window = (structure.get("windows") or [{}])[0]
    door_cx = float((door.get("center") or [xmax, ymin, 1.05])[0])
    door_w = float((door.get("size") or [.85, .1, 2.1])[0])
    door_h = float((door.get("size") or [.85, .1, 2.1])[2])
    win_cx = float((window.get("center") or [.65, ymax, 1.575])[0])
    win_w = float((window.get("size") or [1.60, .1, 1.25])[0])
    win_cz = float((window.get("center") or [.65, ymax, 1.575])[2])
    win_h = float((window.get("size") or [1.60, .1, 1.25])[2])

    surfaces: list[tuple[str, np.ndarray, np.ndarray]] = []
    # x walls: coordinates are (y,z)
    gy, gz = grid(ymin, ymax, .08, zmax - .08, args.step)
    for name, xfixed in (("wall_xmin", xmin + .004), ("wall_xmax", xmax - .004)):
        pts = np.column_stack([np.full(len(gy), xfixed), gy, gz])
        surfaces.append((name, pts, np.abs(xyz[:, 0] - xfixed) <= .055))
    # y-min door wall and y-max window wall: coordinates are (x,z).
    gx, gz = grid(xmin, xmax, .08, zmax - .08, args.step)
    door_wall = np.column_stack([gx, np.full(len(gx), ymin + .004), gz])
    door_open = (np.abs(gx - door_cx) <= door_w * .52) & (gz <= door_h + .03)
    surfaces.append(("door_wall", door_wall[~door_open], np.abs(xyz[:, 1] - (ymin + .004)) <= .055))
    window_wall = np.column_stack([gx, np.full(len(gx), ymax - .004), gz])
    window_open = (np.abs(gx - win_cx) <= win_w * .52) & (np.abs(gz - win_cz) <= win_h * .52)
    surfaces.append(("window_wall", window_wall[~window_open], np.abs(xyz[:, 1] - (ymax - .004)) <= .055))

    for name, points, ref in surfaces:
        add, col = interpolate_surface(points, xyz[ref], rgb[ref], all_tree, args.min_gap, .30)
        additions_xyz.append(add); additions_rgb.append(col); counts[name] = len(add)

    # Repair only holes on the measured mattress top.  The accepted structure
    # box controls the footprint; nearby accepted bed points supply colour.
    bed = next((o for o in structure.get("objects", []) if o.get("label") == "bed"), None)
    if bed:
        center = np.asarray(bed["center"], float); size = np.asarray(bed["size"], float)
        theta = np.deg2rad(float(bed.get("rotation_z_deg", 0)))
        a, b = grid(-size[0] / 2, size[0] / 2, -size[1] / 2, size[1] / 2, args.step)
        c, s = np.cos(theta), np.sin(theta)
        bed_top = np.column_stack([
            center[0] + c * a - s * b,
            center[1] + s * a + c * b,
            np.full(len(a), center[2] + size[2] / 2),
        ])
        local = xyz - center
        ref = (np.abs(local[:, 2] - size[2] / 2) <= .10) & (np.linalg.norm(local[:, :2], axis=1) <= max(size[:2]) * .75)
        add, col = interpolate_surface(bed_top, xyz[ref], rgb[ref], all_tree, args.min_gap, .25)
        additions_xyz.append(add); additions_rgb.append(col); counts["bed_top"] = len(add)

    add_xyz = np.vstack(additions_xyz) if additions_xyz else np.empty((0, 3))
    add_rgb = np.vstack(additions_rgb) if additions_rgb else np.empty((0, 3))
    out = o3d.geometry.PointCloud()
    out.points = o3d.utility.Vector3dVector(np.vstack([xyz, add_xyz]))
    out.colors = o3d.utility.Vector3dVector(np.vstack([rgb, add_rgb]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(args.output), out, write_ascii=False, compressed=False)
    report = {
        "status": "candidate_only", "method": "accepted_baseline_local_surface_gap_repair",
        "baseline_points_preserved": int(len(xyz)), "added_points": int(len(add_xyz)),
        "output_points": int(len(xyz) + len(add_xyz)), "additions": counts,
        "ceiling_added": 0, "openings_preserved": ["door", "window"],
        "outside_physical_surface_gaps_unchanged": True, "point_size_qa_fraction": .60,
        "official_preview_changed": False,
    }
    args.output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
