"""Rebuild scan 46 six marked regions from registered real observations only.

The accepted clear cloud is immutable outside the six review regions.  The
starting cloud already contains anchored supplement consensus and the local
bookshelf replacement.  This pass mines the 900-frame registered SLAM3R maps
that were fused with multi-view consensus, adding only observations that fall
into a marked region and are genuinely absent from the starting cloud.

No plane, grid, interpolation, colour fill, or synthetic point is generated.
"""
from __future__ import annotations

import argparse
import json
import struct
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree


PLY_TYPES = {
    "char": "i1", "uchar": "u1", "short": "<i2", "ushort": "<u2",
    "int": "<i4", "uint": "<u4", "float": "<f4", "double": "<f8",
}


def read_binary_ply(path: Path) -> tuple[np.ndarray, np.ndarray]:
    with path.open("rb") as handle:
        header: list[str] = []
        while True:
            line = handle.readline()
            if not line:
                raise RuntimeError(f"truncated PLY header: {path}")
            text = line.decode("ascii").strip()
            header.append(text)
            if text == "end_header":
                break
        if "format binary_little_endian 1.0" not in header:
            raise RuntimeError(f"only binary little-endian PLY is supported: {path}")
        count = int(next(line.split()[2] for line in header if line.startswith("element vertex ")))
        props: list[tuple[str, str]] = []
        in_vertex = False
        for line in header:
            if line.startswith("element "):
                in_vertex = line.startswith("element vertex ")
            elif in_vertex and line.startswith("property "):
                _, kind, name = line.split()
                props.append((name, PLY_TYPES[kind]))
        records = np.fromfile(handle, dtype=np.dtype(props), count=count)
    xyz = np.column_stack([records[name] for name in ("x", "y", "z")]).astype(np.float64)
    rgb = np.column_stack([records[name] for name in ("red", "green", "blue")]).astype(np.float64)
    if rgb.max(initial=0.0) > 1.5:
        rgb /= 255.0
    return xyz, np.clip(rgb, 0.0, 1.0)


def inside(xyz: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    return np.all((xyz >= lo) & (xyz <= hi), axis=1)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("stage1", type=Path)
    parser.add_argument("real_consensus", type=Path)
    parser.add_argument("region_report", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--min-gap", type=float, default=0.010)
    parser.add_argument("--max-nearby-gap", type=float, default=0.30)
    parser.add_argument("--voxel", type=float, default=0.0075)
    args = parser.parse_args()

    report = json.loads(args.region_report.read_text(encoding="utf-8"))
    regions = {
        name: (np.asarray(spec["lower"], dtype=float), np.asarray(spec["upper"], dtype=float))
        for name, spec in report["regions"].items()
    }
    stage_cloud = o3d.io.read_point_cloud(str(args.stage1))
    stage_xyz = np.asarray(stage_cloud.points, dtype=np.float64)
    stage_rgb = np.asarray(stage_cloud.colors, dtype=np.float64)
    real_xyz, real_rgb = read_binary_ply(args.real_consensus)

    stage_tree = cKDTree(stage_xyz)
    distance, _ = stage_tree.query(real_xyz, workers=-1)
    union = np.zeros(len(real_xyz), dtype=bool)
    per_region_source: dict[str, int] = {}
    for name, (lo, hi) in regions.items():
        mask = inside(real_xyz, lo, hi)
        union |= mask
        per_region_source[name] = int(mask.sum())
    selected = union & (distance >= args.min_gap) & (distance <= args.max_nearby_gap)
    add_xyz, add_rgb = real_xyz[selected], real_rgb[selected]

    # Downsample additions only.  Starting points stay byte-for-byte equivalent
    # in coordinates and colour, including the accepted clear areas.
    additions = o3d.geometry.PointCloud()
    additions.points = o3d.utility.Vector3dVector(add_xyz)
    additions.colors = o3d.utility.Vector3dVector(add_rgb)
    additions = additions.voxel_down_sample(args.voxel)
    add_xyz = np.asarray(additions.points, dtype=np.float64)
    add_rgb = np.asarray(additions.colors, dtype=np.float64)

    # A second exact-neighbour gate prevents voxel centroids from landing on an
    # already represented surface and thickening it visually.
    distance, _ = stage_tree.query(add_xyz, workers=-1)
    keep = distance >= args.min_gap
    add_xyz, add_rgb = add_xyz[keep], add_rgb[keep]

    output_cloud = o3d.geometry.PointCloud()
    output_cloud.points = o3d.utility.Vector3dVector(np.vstack([stage_xyz, add_xyz]))
    output_cloud.colors = o3d.utility.Vector3dVector(np.vstack([stage_rgb, add_rgb]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(args.output), output_cloud, write_ascii=False, compressed=False)

    add_region_counts = {
        name: int(inside(add_xyz, lo, hi).sum()) for name, (lo, hi) in regions.items()
    }
    diagnostics = {
        "status": "candidate_ready",
        "method": "registered_real_multiview_consensus_six_region_only",
        "synthetic_points": 0,
        "starting_points": int(len(stage_xyz)),
        "real_consensus_source_points": int(len(real_xyz)),
        "real_additions": int(len(add_xyz)),
        "output_points": int(len(stage_xyz) + len(add_xyz)),
        "outside_six_regions_unchanged": True,
        "point_size_qa_fraction": 0.60,
        "regions": report["regions"],
        "source_points_by_region": per_region_source,
        "additions_by_region": add_region_counts,
        "promotion_requires_visual_qa": True,
    }
    args.output.with_suffix(".json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
