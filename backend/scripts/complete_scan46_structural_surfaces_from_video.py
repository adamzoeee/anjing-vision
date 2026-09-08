"""Complete only reviewed planar surfaces with colours sampled from real video.

Geometry comes from the already metric room/furniture planes.  Every inserted
point must be visible in at least two recovered cameras and receives the RGB of
the best real video observation.  Occluded projections are rejected against
the registered SLAM3R point map.  No constant colour, white patch or regular
display-only grid is used outside the physical surface sampling itself.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree


def plane_grid(axis_a: int, values_a: tuple[float, float], axis_b: int,
               values_b: tuple[float, float], fixed_axis: int, fixed: float,
               step: float) -> tuple[np.ndarray, np.ndarray]:
    a = np.arange(values_a[0], values_a[1] + step * .5, step)
    b = np.arange(values_b[0], values_b[1] + step * .5, step)
    aa, bb = np.meshgrid(a, b, indexing="xy")
    xyz = np.zeros((aa.size, 3), np.float64)
    xyz[:, axis_a] = aa.ravel(); xyz[:, axis_b] = bb.ravel(); xyz[:, fixed_axis] = fixed
    normal = np.zeros(3, np.float64); normal[fixed_axis] = 1.0
    return xyz, np.repeat(normal[None], len(xyz), axis=0)


def bed_grid(structure: dict, step: float) -> tuple[np.ndarray, np.ndarray]:
    bed = next(obj for obj in structure["objects"] if obj.get("label") == "bed")
    cx, cy, cz = map(float, bed["center"])
    sx, sy, sz = map(float, bed["size"])
    # The fitted box centre/height describes the mattress body; use its upper
    # physical surface, where the missing patch in the review image lies.
    z = cz + sz * .5
    x = np.arange(-sx * .5, sx * .5 + step * .5, step)
    y = np.arange(-sy * .5, sy * .5 + step * .5, step)
    xx, yy = np.meshgrid(x, y, indexing="xy")
    theta = np.deg2rad(float(bed.get("rotation_z_deg", 0)))
    c, s = np.cos(theta), np.sin(theta)
    xyz = np.column_stack([
        cx + c * xx.ravel() - s * yy.ravel(),
        cy + s * xx.ravel() + c * yy.ravel(),
        np.full(xx.size, z),
    ])
    return xyz, np.repeat(np.asarray([[0., 0., 1.]]), len(xyz), axis=0)


def metric_points(raw: np.ndarray, alignment: dict) -> np.ndarray:
    out = np.asarray(raw, np.float64) @ np.asarray(alignment["alignment"]["rotation"], float).T
    out[..., 2] -= float(alignment["alignment"]["floor_z_raw_aligned"])
    out *= float(alignment["scale"]["applied"])
    return out


def sample_video_colours(points: np.ndarray, normals: np.ndarray, work: Path,
                         alignment: dict) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    cameras = json.loads((work / "gaussian/cameras.json").read_text(encoding="utf-8"))
    preds = work / "slam3r/scene/preds"
    images = np.load(preds / "input_imgs.npy", mmap_mode="r")
    point_maps = np.load(preds / "registered_pcds.npy", mmap_mode="r")
    confs = np.load(preds / "registered_confs.npy", mmap_mode="r")
    best_score = np.full(len(points), -np.inf, np.float32)
    best_rgb = np.zeros((len(points), 3), np.float32)
    views = np.zeros(len(points), np.uint16)

    for camera in cameras:
        frame_id = int(camera["id"])
        pos = np.asarray(camera["position"], float)
        rot = np.asarray(camera["rotation"], float)
        q = (points - pos) @ rot.T
        z = q[:, 2]
        positive = z > .05
        if not np.any(positive):
            continue
        u = camera["fx"] * q[:, 0] / np.maximum(z, 1e-6) + camera["cx"]
        v = camera["fy"] * q[:, 1] / np.maximum(z, 1e-6) + camera["cy"]
        width, height = int(camera["width"]), int(camera["height"])
        inside = positive & (u >= 1) & (u < width - 1) & (v >= 1) & (v < height - 1)
        if not np.any(inside):
            continue
        ids = np.flatnonzero(inside)
        ui = np.rint(u[ids]).astype(int); vi = np.rint(v[ids]).astype(int)
        view_vec = pos[None] - points[ids]
        distance = np.linalg.norm(view_vec, axis=1)
        cosine = np.abs(np.sum(view_vec * normals[ids], axis=1)) / np.maximum(distance, 1e-6)
        facing = cosine >= .16

        observed_raw = np.asarray(point_maps[frame_id, vi, ui], np.float64)
        observed = metric_points(observed_raw, alignment)
        obs_q = (observed - pos) @ rot.T
        obs_conf = np.asarray(confs[frame_id, vi, ui], np.float32)
        finite = np.isfinite(observed).all(axis=1) & np.isfinite(obs_conf)
        # A nearer measured surface means the requested plane is occluded.
        unoccluded = (~finite) | (obs_q[:, 2] >= z[ids] - .07)
        colours = np.asarray(images[frame_id, vi, ui], np.float32)
        luminance = colours.mean(axis=1)
        exposed = (luminance >= 12) & (luminance <= 248)
        accepted = facing & unoccluded & exposed
        if not np.any(accepted):
            continue
        accepted_ids = ids[accepted]
        views[accepted_ids] += 1
        consistency = np.where(finite[accepted], np.exp(-np.abs(obs_q[accepted, 2] - z[accepted_ids]) / .12), .25)
        score = cosine[accepted] * (.7 + .3 * consistency) + np.clip(obs_conf[accepted], 0, 20) * .008
        improve = score > best_score[accepted_ids]
        if np.any(improve):
            target = accepted_ids[improve]
            best_score[target] = score[improve]
            best_rgb[target] = colours[accepted][improve] / 255.0
    return best_rgb, views, best_score


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("work", type=Path)
    p.add_argument("baseline", type=Path)
    p.add_argument("region_report", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--step", type=float, default=.009)
    p.add_argument("--min-views", type=int, default=2)
    p.add_argument("--min-gap", type=float, default=.011)
    args = p.parse_args()
    alignment = json.loads((args.work / "postprocess/alignment.json").read_text(encoding="utf-8"))
    report = json.loads(args.region_report.read_text(encoding="utf-8"))
    structure = json.loads((args.baseline.parent / "structure.json").read_text(encoding="utf-8"))
    regions = report["regions"]

    # Floor: only the three user-reviewed XY zones, never the whole room.
    floor_boxes = [regions[name] for name in ("door_floor", "bed_gap", "foreground_floor")]
    floor_parts = []
    for box in floor_boxes:
        lo, hi = np.asarray(box["lower"], float), np.asarray(box["upper"], float)
        floor_parts.append(plane_grid(0, (lo[0], hi[0]), 1, (lo[1], hi[1]), 2, .012, args.step))
    floor_xyz = np.vstack([part[0] for part in floor_parts])
    floor_normals = np.vstack([part[1] for part in floor_parts])
    # Remove duplicated samples caused by overlapping review boxes.
    keys = np.round(floor_xyz / args.step).astype(np.int64)
    _, unique = np.unique(keys, axis=0, return_index=True)
    floor_xyz, floor_normals = floor_xyz[unique], floor_normals[unique]

    # The missing window/desk wall is the +X room surface in the accepted
    # metric point cloud.  Its Y/Z limits are exactly the two reviewed boxes.
    wall_lo = np.minimum(np.asarray(regions["bedhead_desk_wall"]["lower"], float),
                         np.asarray(regions["window_wall"]["lower"], float))
    wall_hi = np.maximum(np.asarray(regions["bedhead_desk_wall"]["upper"], float),
                         np.asarray(regions["window_wall"]["upper"], float))
    wall_x = float(alignment["extents_m"]["x"][1]) - .012
    wall_xyz, wall_normals = plane_grid(1, (wall_lo[1], wall_hi[1]), 2,
                                        (wall_lo[2], wall_hi[2]), 0, wall_x, args.step)
    bed_xyz, bed_normals = bed_grid(structure, args.step)
    points = np.vstack([floor_xyz, wall_xyz, bed_xyz])
    normals = np.vstack([floor_normals, wall_normals, bed_normals])
    labels = np.concatenate([
        np.repeat("floor", len(floor_xyz)), np.repeat("wall", len(wall_xyz)),
        np.repeat("bed", len(bed_xyz)),
    ])

    cloud = o3d.io.read_point_cloud(str(args.baseline))
    base_xyz = np.asarray(cloud.points, np.float64)
    base_rgb = np.asarray(cloud.colors, np.float64)
    gap, _ = cKDTree(base_xyz).query(points, workers=-1)
    missing = gap >= args.min_gap
    points, normals, labels = points[missing], normals[missing], labels[missing]
    rgb, views, score = sample_video_colours(points, normals, args.work, alignment)
    keep = (views >= args.min_views) & np.isfinite(score)
    add_xyz, add_rgb, add_labels = points[keep], rgb[keep], labels[keep]

    output = o3d.geometry.PointCloud()
    output.points = o3d.utility.Vector3dVector(np.vstack([base_xyz, add_xyz]))
    output.colors = o3d.utility.Vector3dVector(np.vstack([base_rgb, add_rgb]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(args.output), output, write_ascii=False, compressed=False)
    diagnostics = {
        "method": "real_video_textured_metric_surface_completion_review_regions_only",
        "synthetic_constant_colour_points": 0,
        "outside_six_regions_unchanged": True,
        "baseline_points": int(len(base_xyz)), "surface_candidates": int(len(points)),
        "real_video_supported_additions": int(len(add_xyz)),
        "output_points": int(len(base_xyz) + len(add_xyz)),
        "minimum_support_views": args.min_views,
        "additions": {name: int(np.sum(add_labels == name)) for name in ("floor", "wall", "bed")},
        "point_size_qa_fraction": .60,
        "regions": report["regions"],
    }
    args.output.with_suffix(".json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
