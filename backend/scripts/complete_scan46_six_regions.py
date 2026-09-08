"""Complete the two regions omitted by the legacy four-region scan-46 repair."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree


def inside(points: np.ndarray, lower: np.ndarray, upper: np.ndarray) -> np.ndarray:
    return np.all((points >= lower) & (points <= upper), axis=1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("stage1", type=Path)
    parser.add_argument("trusted_source", type=Path)
    parser.add_argument("alignment", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()

    alignment = json.loads(args.alignment.read_text(encoding="utf-8"))
    ext = alignment["extents_m"]
    x0, x1 = map(float, ext["x"])
    y0, y1 = map(float, ext["y"])
    z1 = float(ext["z"][1])
    regions = {
        "door_floor": ([-0.5144042583, 0.7989766623, -0.03], [0.3155957417, 1.3889766623, 0.16]),
        "bedhead_desk_wall": ([1.78213545, -0.5309722655, 0.12], [1.99213545, 1.3789766623, 2.526745507]),
        "window_wall": ([1.66213545, -0.5309722655, 0.20], [1.99213545, 0.8190277345, 1.55]),
        "bookshelf": ([0.1355957417, 0.1790277345, 0.28], [1.68213545, 1.3789766623, 1.98]),
        # The legacy policy omitted these two user-marked areas entirely.
        "bed_gap": ([x0 + 0.02, y0 - 0.20, 0.12], [x1 - 0.25, y0 + 1.22, 0.72]),
        "foreground_floor": ([x0 - 0.03, y0 - 0.20, -0.075], [x1 + 0.03, y0 + 0.72, 0.18]),
    }
    regions = {name: (np.asarray(lo), np.asarray(hi)) for name, (lo, hi) in regions.items()}

    base_cloud = o3d.io.read_point_cloud(str(args.baseline))
    stage_cloud = o3d.io.read_point_cloud(str(args.stage1))
    source_cloud = o3d.io.read_point_cloud(str(args.trusted_source))
    base_xyz = np.asarray(base_cloud.points)
    stage_xyz = np.asarray(stage_cloud.points)
    stage_rgb = np.asarray(stage_cloud.colors)
    source_xyz = np.asarray(source_cloud.points)
    source_rgb = np.asarray(source_cloud.colors)

    target = inside(source_xyz, *regions["bed_gap"]) | inside(source_xyz, *regions["foreground_floor"])
    brightness = source_rgb.mean(axis=1)
    target &= np.isfinite(source_xyz).all(axis=1) & (brightness >= 0.035) & (brightness <= 0.985)
    candidate_xyz, candidate_rgb = source_xyz[target], source_rgb[target]
    tree = cKDTree(stage_xyz)
    distance, _ = tree.query(candidate_xyz, workers=-1)
    # The source is the same registered reconstruction as the accepted baseline.
    # Only restore genuinely absent samples; do not thicken already-clear surfaces.
    keep = (distance >= 0.012) & (distance <= 0.24)
    candidate_xyz, candidate_rgb = candidate_xyz[keep], candidate_rgb[keep]
    candidate = o3d.geometry.PointCloud()
    candidate.points = o3d.utility.Vector3dVector(candidate_xyz)
    candidate.colors = o3d.utility.Vector3dVector(candidate_rgb)
    candidate = candidate.voxel_down_sample(0.008)
    if len(candidate.points):
        _, ids = candidate.remove_radius_outlier(nb_points=3, radius=0.028)
        candidate = candidate.select_by_index(ids)
    additions_xyz = np.asarray(candidate.points)
    additions_rgb = np.asarray(candidate.colors)

    result = o3d.geometry.PointCloud()
    result.points = o3d.utility.Vector3dVector(np.vstack([stage_xyz, additions_xyz]))
    result.colors = o3d.utility.Vector3dVector(np.vstack([stage_rgb, additions_rgb]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(args.output), result, write_ascii=False, compressed=False)

    # Stage 1 already proves byte-for-byte preservation outside its four boxes.
    # This stage appends points only; prove every appended point is in one of the
    # two newly whitelisted boxes, so it cannot alter any other accepted area.
    unchanged = bool(np.all(
        inside(additions_xyz, *regions["bed_gap"])
        | inside(additions_xyz, *regions["foreground_floor"])
    ))
    diagnostics = {
        "status": "candidate_ready" if unchanged else "rejected",
        "method": "six_region_registered_video_consensus_repair",
        "baseline": str(args.baseline),
        "stage1": str(args.stage1),
        "trusted_source": str(args.trusted_source),
        "baseline_points": int(len(base_xyz)),
        "stage1_points": int(len(stage_xyz)),
        "restored_points": int(len(additions_xyz)),
        "output_points": int(len(stage_xyz) + len(additions_xyz)),
        "outside_six_regions_unchanged": bool(unchanged),
        "regions": {name: {"lower": lo.tolist(), "upper": hi.tolist()} for name, (lo, hi) in regions.items()},
        "promotion_requires_visual_qa": True,
    }
    args.output.with_suffix(".json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    if not unchanged:
        args.output.unlink(missing_ok=True)
        raise RuntimeError("Points outside the six whitelisted regions changed")
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
