"""Build two strictly local scan-46 preview repairs on an accepted baseline.

The baseline is immutable.  Stage 1 may add video-supported samples only in
the reviewed entrance-floor hole.  Stage 2 may additionally add samples only
on the reviewed +X window wall.  Existing points are never removed or moved.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree

from complete_scan46_structural_surfaces_from_video import sample_video_colours


FLOOR_X = (-0.5144042583, 0.3155957417)
FLOOR_Y = (0.7989766623, 1.3889766623)
WINDOW_Y = (-0.5309722655, 0.8190277345)
WINDOW_Z = (0.20, 1.55)


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def grid(a: tuple[float, float], b: tuple[float, float], step: float) -> tuple[np.ndarray, np.ndarray]:
    av = np.arange(a[0], a[1] + step * 0.5, step)
    bv = np.arange(b[0], b[1] + step * 0.5, step)
    return np.meshgrid(av, bv, indexing="xy")


def supported_missing(
    candidates: np.ndarray,
    normals: np.ndarray,
    baseline_xyz: np.ndarray,
    work: Path,
    alignment: dict,
    min_views: int,
    min_gap: float,
) -> tuple[np.ndarray, np.ndarray, dict]:
    gap, _ = cKDTree(baseline_xyz).query(candidates, workers=-1)
    candidates = candidates[gap >= min_gap]
    normals = normals[gap >= min_gap]
    if not len(candidates):
        return np.empty((0, 3)), np.empty((0, 3)), {"missing_candidates": 0}
    rgb, views, score = sample_video_colours(candidates, normals, work, alignment)
    luminance = rgb @ np.asarray([0.2126, 0.7152, 0.0722])
    # Requiring several independent views prevents a single bad depth/color
    # projection from producing a visible slab or ghost surface.
    keep = (views >= min_views) & np.isfinite(score) & (luminance >= 0.10) & (luminance <= 0.94)
    return candidates[keep], rgb[keep], {
        "missing_candidates": int(len(candidates)),
        "video_supported": int(np.count_nonzero(keep)),
        "median_support_views": float(np.median(views[keep])) if np.any(keep) else 0.0,
    }


def write_cloud(path: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(xyz)
    cloud.colors = o3d.utility.Vector3dVector(rgb)
    path.parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_point_cloud(str(path), cloud, write_ascii=False, compressed=False):
        raise RuntimeError(f"Failed to write {path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path, help="Work directory containing the original 900 registered views")
    parser.add_argument("baseline", type=Path)
    parser.add_argument("floor_output", type=Path)
    parser.add_argument("final_output", type=Path)
    parser.add_argument("--step", type=float, default=0.008)
    parser.add_argument("--min-gap", type=float, default=0.013)
    parser.add_argument("--min-views", type=int, default=4)
    args = parser.parse_args()

    alignment = json.loads((args.work / "postprocess/alignment.json").read_text(encoding="utf-8"))
    baseline = o3d.io.read_point_cloud(str(args.baseline))
    base_xyz = np.asarray(baseline.points, np.float64)
    base_rgb = np.asarray(baseline.colors, np.float64)

    # Match the already accepted single floor layer instead of fitting a new
    # plane from the problematic ROI.  This prevents a raised second floor.
    floor_reference = base_xyz[
        (base_xyz[:, 2] >= -0.025) & (base_xyz[:, 2] <= 0.025)
        & ~(
            (base_xyz[:, 0] >= FLOOR_X[0]) & (base_xyz[:, 0] <= FLOOR_X[1])
            & (base_xyz[:, 1] >= FLOOR_Y[0]) & (base_xyz[:, 1] <= FLOOR_Y[1])
        )
    ]
    if len(floor_reference) < 100:
        raise RuntimeError("Accepted baseline has insufficient floor reference points")
    floor_z = float(np.median(floor_reference[:, 2]))
    fx, fy = grid(FLOOR_X, FLOOR_Y, args.step)
    floor_candidates = np.column_stack([fx.ravel(), fy.ravel(), np.full(fx.size, floor_z)])
    floor_normals = np.repeat(np.asarray([[0.0, 0.0, 1.0]]), len(floor_candidates), axis=0)
    floor_xyz, floor_rgb, floor_stats = supported_missing(
        floor_candidates, floor_normals, base_xyz, args.work, alignment,
        args.min_views, args.min_gap,
    )
    stage1_xyz = np.vstack([base_xyz, floor_xyz])
    stage1_rgb = np.vstack([base_rgb, floor_rgb])
    write_cloud(args.floor_output, stage1_xyz, stage1_rgb)

    # The reviewed window is on the +X side wall (not the +Y rear wall used by
    # the rejected candidate).  Use the accepted room extent and add only the
    # exact reviewed wall rectangle; no ceiling or neighbouring wall is touched.
    wall_x = float(alignment["extents_m"]["x"][1]) - 0.010
    wy, wz = grid(WINDOW_Y, WINDOW_Z, args.step)
    window_candidates = np.column_stack([np.full(wy.size, wall_x), wy.ravel(), wz.ravel()])
    window_normals = np.repeat(np.asarray([[-1.0, 0.0, 0.0]]), len(window_candidates), axis=0)
    window_xyz, window_rgb, window_stats = supported_missing(
        window_candidates, window_normals, stage1_xyz, args.work, alignment,
        args.min_views, args.min_gap,
    )
    final_xyz = np.vstack([stage1_xyz, window_xyz])
    final_rgb = np.vstack([stage1_rgb, window_rgb])
    write_cloud(args.final_output, final_xyz, final_rgb)

    report = {
        "status": "candidate_ready_for_visual_qa",
        "method": "immutable_accepted_baseline_plus_two_video_supported_single_surface_rois",
        "baseline": args.baseline.name,
        "baseline_sha256": sha256(args.baseline),
        "baseline_points_preserved": int(len(base_xyz)),
        "existing_points_removed": 0,
        "existing_points_moved": 0,
        "outside_two_rois_unchanged": True,
        "synthetic_constant_colour_points": 0,
        "floor": {
            "bounds_xy": [list(FLOOR_X), list(FLOOR_Y)],
            "z": floor_z,
            **floor_stats,
            "added_points": int(len(floor_xyz)),
            "stage_output": args.floor_output.name,
        },
        "window_wall": {
            "axis": "+X",
            "x": wall_x,
            "bounds_yz": [list(WINDOW_Y), list(WINDOW_Z)],
            **window_stats,
            "added_points": int(len(window_xyz)),
        },
        "output_points": int(len(final_xyz)),
        "point_size_qa_fraction": 0.60,
        "structure_measurements_passage_untouched": True,
    }
    args.final_output.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
