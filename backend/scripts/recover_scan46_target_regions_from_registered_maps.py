"""Recover scan 46 review regions from real registered SLAM3R maps.

This is deliberately a targeted recovery pass.  It never changes the accepted
cloud outside the configured review boxes and never fabricates geometry.  A
lower-confidence observation is accepted only when the same voxel is observed
in several frames spread over several temporal blocks.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree


def _inside_union(xyz: np.ndarray, regions: list[tuple[np.ndarray, np.ndarray]]) -> np.ndarray:
    mask = np.zeros(len(xyz), dtype=bool)
    for lo, hi in regions:
        mask |= np.all((xyz >= lo) & (xyz <= hi), axis=1)
    return mask


def _reduce(h: np.ndarray, xyz: np.ndarray, rgb: np.ndarray, weights: np.ndarray,
            frames: np.ndarray, blocks: np.ndarray, max_conf: np.ndarray) -> tuple[np.ndarray, ...]:
    if not len(h):
        return h, xyz, rgb, weights, frames, blocks, max_conf
    order = np.argsort(h, kind="mergesort")
    h = h[order]
    starts = np.r_[0, np.flatnonzero(h[1:] != h[:-1]) + 1]
    return (
        h[starts], np.add.reduceat(xyz[order], starts, axis=0),
        np.add.reduceat(rgb[order], starts, axis=0),
        np.add.reduceat(weights[order], starts),
        np.add.reduceat(frames[order], starts),
        np.add.reduceat(blocks[order], starts),
        np.maximum.reduceat(max_conf[order], starts),
    )


def _empty() -> tuple[np.ndarray, ...]:
    return (
        np.empty(0, np.int64), np.empty((0, 3), np.float64),
        np.empty((0, 3), np.float64), np.empty(0, np.float64),
        np.empty(0, np.int32), np.empty(0, np.int16), np.empty(0, np.float32),
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("work", type=Path)
    p.add_argument("baseline", type=Path)
    p.add_argument("region_report", type=Path)
    p.add_argument("output", type=Path)
    p.add_argument("--voxel", type=float, default=0.0075)
    p.add_argument("--min-conf", type=float, default=2.0)
    p.add_argument("--block-size", type=int, default=15)
    p.add_argument("--min-frames", type=int, default=7)
    p.add_argument("--min-blocks", type=int, default=3)
    p.add_argument("--min-gap", type=float, default=0.009)
    p.add_argument("--max-gap", type=float, default=0.40)
    p.add_argument("--strict-support", action="store_true",
                   help="Require min-frames and min-blocks exactly; disable the high-confidence relaxation.")
    p.add_argument("--immutable-prefix-points", type=int, default=0,
                   help="Keep this leading baseline point prefix immutable; drop only later appended points inside target ROIs.")
    args = p.parse_args()

    report = json.loads(args.region_report.read_text(encoding="utf-8"))
    regions_by_name = {
        name: (np.asarray(spec["lower"], float), np.asarray(spec["upper"], float))
        for name, spec in report["regions"].items()
    }
    regions = list(regions_by_name.values())
    alignment = json.loads((args.work / "postprocess/alignment.json").read_text(encoding="utf-8"))
    rotation = np.asarray(alignment["alignment"]["rotation"], np.float64)
    scale = float(alignment["scale"]["applied"])
    floor_z = float(alignment["alignment"]["floor_z_raw_aligned"])
    preds = args.work / "slam3r/scene/preds"
    point_maps = np.load(preds / "registered_pcds.npy", mmap_mode="r")
    confidences = np.load(preds / "registered_confs.npy", mmap_mode="r")
    images = np.load(preds / "input_imgs.npy", mmap_mode="r")

    all_lo = np.min(np.vstack([lo for lo, _ in regions]), axis=0) - 0.02
    all_hi = np.max(np.vstack([hi for _, hi in regions]), axis=0) + 0.02
    origin = all_lo
    shape = np.ceil((all_hi - all_lo) / args.voxel).astype(np.int64) + 1
    nx, ny = int(shape[0]), int(shape[1])
    global_records = _empty()

    for block_start in range(0, len(point_maps), args.block_size):
        block = _empty()
        for frame_id in range(block_start, min(block_start + args.block_size, len(point_maps))):
            xyz = np.asarray(point_maps[frame_id], np.float64).reshape(-1, 3)
            conf = np.asarray(confidences[frame_id], np.float32).reshape(-1)
            rgb = np.asarray(images[frame_id], np.float32).reshape(-1, 3)
            valid = np.isfinite(xyz).all(axis=1) & np.isfinite(conf) & (conf >= args.min_conf)
            if not np.any(valid):
                continue
            xyz, conf, rgb = xyz[valid], conf[valid], rgb[valid]
            xyz = xyz @ rotation.T
            xyz[:, 2] -= floor_z
            xyz *= scale
            target = _inside_union(xyz, regions)
            if not np.any(target):
                continue
            xyz, conf, rgb = xyz[target], conf[target], rgb[target]
            ijk = np.floor((xyz - origin) / args.voxel).astype(np.int64)
            hashes = ijk[:, 0] + nx * (ijk[:, 1] + ny * ijk[:, 2])
            order = np.lexsort((-conf, hashes))
            sorted_hashes = hashes[order]
            first = np.r_[0, np.flatnonzero(sorted_hashes[1:] != sorted_hashes[:-1]) + 1]
            chosen = order[first]
            hashes, xyz, rgb, conf = hashes[chosen], xyz[chosen], rgb[chosen], conf[chosen]
            weight = np.log1p(np.maximum(conf, 0)).astype(np.float64)
            current = (
                hashes, xyz * weight[:, None], rgb.astype(np.float64) * weight[:, None], weight,
                np.ones(len(hashes), np.int32), np.zeros(len(hashes), np.int16), conf,
            )
            block = _reduce(*[np.concatenate([a, b]) for a, b in zip(block, current)])
        if len(block[0]):
            block = (*block[:5], np.ones(len(block[0]), np.int16), block[6])
            global_records = _reduce(
                *[np.concatenate([a, b]) for a, b in zip(global_records, block)]
            )
        print(f"frames={min(block_start + args.block_size, len(point_maps))}/{len(point_maps)} voxels={len(global_records[0])}", flush=True)

    hashes, xyz_sum, rgb_sum, weights, frames, blocks, max_conf = global_records
    # Cross-time confirmation is mandatory.  High confidence relaxes frame
    # count slightly, but never permits a single-frame observation.
    strict_keep = (frames >= args.min_frames) & (blocks >= args.min_blocks)
    keep = strict_keep if args.strict_support else strict_keep | (
        (max_conf >= 10.0) & (frames >= 3) & (blocks >= 2)
    )
    recovered_xyz = xyz_sum[keep] / weights[keep, None]
    recovered_rgb = np.clip(rgb_sum[keep] / weights[keep, None], 0, 255) / 255.0
    support_frames, support_blocks = frames[keep], blocks[keep]

    baseline = o3d.io.read_point_cloud(str(args.baseline))
    base_xyz = np.asarray(baseline.points, np.float64)
    base_rgb = np.asarray(baseline.colors, np.float64)
    removed_appended_inside = 0
    if args.immutable_prefix_points:
        prefix = int(args.immutable_prefix_points)
        if not 0 < prefix <= len(base_xyz):
            raise ValueError("immutable-prefix-points is outside the baseline point range")
        keep_base = np.ones(len(base_xyz), dtype=bool)
        appended_inside = _inside_union(base_xyz[prefix:], regions)
        keep_base[prefix:] = ~appended_inside
        removed_appended_inside = int(np.count_nonzero(appended_inside))
        base_xyz, base_rgb = base_xyz[keep_base], base_rgb[keep_base]
    tree = cKDTree(base_xyz)
    gap, _ = tree.query(recovered_xyz, workers=-1)
    add = (gap >= args.min_gap) & (gap <= args.max_gap)
    add_xyz, add_rgb = recovered_xyz[add], recovered_rgb[add]

    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(np.vstack([base_xyz, add_xyz]))
    cloud.colors = o3d.utility.Vector3dVector(np.vstack([base_rgb, add_rgb]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(args.output), cloud, write_ascii=False, compressed=False)

    diagnostics = {
        "method": "targeted_registered_maps_low_confidence_cross_time_consensus",
        "synthetic_points": 0,
        "strict_support": bool(args.strict_support),
        "outside_six_regions_unchanged": True,
        "source_frames": int(len(point_maps)),
        "baseline_points": int(len(base_xyz)),
        "immutable_prefix_points": int(args.immutable_prefix_points),
        "removed_later_appended_points_inside_target_rois": removed_appended_inside,
        "candidate_voxels": int(len(hashes)),
        "supported_voxels": int(keep.sum()),
        "real_additions": int(len(add_xyz)),
        "output_points": int(len(base_xyz) + len(add_xyz)),
        "median_support_frames": float(np.median(support_frames)) if len(support_frames) else 0,
        "median_support_blocks": float(np.median(support_blocks)) if len(support_blocks) else 0,
        "point_size_qa_fraction": 0.60,
        "regions": report["regions"],
    }
    args.output.with_suffix(".json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(diagnostics, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
