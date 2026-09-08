"""Build a scan46 floor-only candidate from real supplement observations.

The accepted Figure-4 cloud is immutable.  The supplement reconstruction is
registered to the baseline SLAM coordinate system with the *identical anchor
frames*, then converted through the normal alignment/scale transform.  Only
multi-frame observations close to the accepted z=0 floor can be appended.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree

from fuse_scan46_buchong2_four_regions import metric, robust_similarity


ROOT = Path(__file__).resolve().parents[1]
WORK = ROOT / "data/work/46"
BASE_WORK = ROOT / "data/work/45"
SOURCE = WORK / "supplement_buchong2_clip3"
BASELINE = WORK / "postprocess/scene_preview_local_surface_repair_candidate.ply"
OUTPUT = WORK / "postprocess/scene_preview_figure4_plus_strict_real_floor.ply"
REPORT = OUTPUT.with_suffix(".json")


def main() -> None:
    mapping = json.loads((SOURCE / "supplement_mapping.json").read_text(encoding="utf-8"))["mapping"]
    alignment = json.loads((WORK / "postprocess/alignment.json").read_text(encoding="utf-8"))
    base_dir = BASE_WORK / "slam3r/scene/preds"
    new_dir = SOURCE / "slam3r/scene/preds"
    base_points = np.load(base_dir / "registered_pcds.npy", mmap_mode="r")
    base_conf = np.load(base_dir / "registered_confs.npy", mmap_mode="r")
    new_points = np.load(new_dir / "registered_pcds.npy", mmap_mode="r")
    new_conf = np.load(new_dir / "registered_confs.npy", mmap_mode="r")
    new_images = np.load(new_dir / "input_imgs.npy", mmap_mode="r")

    source_parts, target_parts = [], []
    rng = np.random.default_rng(4603)
    for item in mapping:
        if item["kind"] != "anchor":
            continue
        new_id, base_id = int(item["output_index"]), int(item["baseline_index"])
        src = np.asarray(new_points[new_id], np.float64).reshape(-1, 3)
        dst = np.asarray(base_points[base_id], np.float64).reshape(-1, 3)
        conf = np.minimum(np.asarray(new_conf[new_id]).reshape(-1), np.asarray(base_conf[base_id]).reshape(-1))
        valid = np.isfinite(src).all(1) & np.isfinite(dst).all(1) & (conf >= 12.0)
        ids = np.flatnonzero(valid)
        if len(ids) > 2500:
            ids = rng.choice(ids, 2500, replace=False)
        source_parts.append(src[ids])
        target_parts.append(dst[ids])
    source_xyz, target_xyz = np.concatenate(source_parts), np.concatenate(target_parts)
    scale, rotation, translation, residual_raw = robust_similarity(source_xyz, target_xyz, iterations=7)
    residual_metric = residual_raw * float(alignment["scale"]["applied"])

    # Fuse one representative real observation per 8 mm voxel.  A voxel must
    # occur in at least four frames and in at least two temporal thirds.
    voxel = 0.008
    records: dict[tuple[int, int, int], list] = {}
    supplement_ids = [int(x["output_index"]) for x in mapping if x["kind"] == "supplement"]
    first, last = min(supplement_ids), max(supplement_ids)
    span = max(1, last - first + 1)
    for frame_id in supplement_ids:
        raw = np.asarray(new_points[frame_id], np.float64).reshape(-1, 3)
        conf = np.asarray(new_conf[frame_id], np.float32).reshape(-1)
        rgb = np.asarray(new_images[frame_id], np.float32).reshape(-1, 3)
        valid = np.isfinite(raw).all(1) & np.isfinite(conf) & (conf >= 8.0)
        raw, conf, rgb = raw[valid], conf[valid], rgb[valid]
        baseline_raw = scale * (raw @ rotation.T) + translation
        xyz = metric(baseline_raw, alignment)
        x, y, z = xyz.T
        keep = (
            (x >= -0.52) & (x <= 0.55) &
            (y >= 0.55) & (y <= 1.40) &
            (z >= -0.035) & (z <= 0.045)
        )
        xyz, rgb, conf = xyz[keep], rgb[keep], conf[keep]
        block = min(2, int(3 * (frame_id - first) / span))
        for p, c, q in zip(xyz, rgb, conf):
            key = tuple(np.floor(p / voxel).astype(np.int64))
            row = records.setdefault(key, [np.zeros(3), np.zeros(3), 0.0, set(), set(), 0.0])
            w = float(np.log1p(q))
            row[0] += p * w; row[1] += c * w; row[2] += w
            row[3].add(frame_id); row[4].add(block); row[5] = max(row[5], float(q))

    fused_xyz, fused_rgb, supports = [], [], []
    for xyz_sum, rgb_sum, weight, frames, blocks, max_conf in records.values():
        if len(frames) < 4 or len(blocks) < 2 or max_conf < 10.0:
            continue
        fused_xyz.append(xyz_sum / weight)
        fused_rgb.append(np.clip(rgb_sum / weight, 0, 255) / 255.0)
        supports.append(len(frames))
    fused_xyz = np.asarray(fused_xyz, np.float64).reshape(-1, 3)
    fused_rgb = np.asarray(fused_rgb, np.float64).reshape(-1, 3)

    baseline = o3d.io.read_point_cloud(str(BASELINE))
    base_xyz = np.asarray(baseline.points, np.float64)
    base_rgb = np.asarray(baseline.colors, np.float64)
    if len(fused_xyz):
        distance, _ = cKDTree(base_xyz).query(fused_xyz, workers=-1)
        add = (distance >= 0.012) & (distance <= 0.10)
        fused_xyz, fused_rgb = fused_xyz[add], fused_rgb[add]
    combined = o3d.geometry.PointCloud()
    combined.points = o3d.utility.Vector3dVector(np.vstack([base_xyz, fused_xyz]))
    combined.colors = o3d.utility.Vector3dVector(np.vstack([base_rgb, fused_rgb]))
    o3d.io.write_point_cloud(str(OUTPUT), combined, write_ascii=False, compressed=False)

    check_xyz = np.asarray(o3d.io.read_point_cloud(str(OUTPUT)).points)
    report = {
        "status": "candidate_only",
        "baseline": str(BASELINE),
        "baseline_points": int(len(base_xyz)),
        "anchor_correspondences": int(len(source_xyz)),
        "anchor_scale": float(scale),
        "anchor_median_error_m": float(np.median(residual_metric)),
        "anchor_p90_error_m": float(np.quantile(residual_metric, .9)),
        "supported_floor_voxels": int(len(supports)),
        "added_real_floor_points": int(len(fused_xyz)),
        "output_points": int(len(check_xyz)),
        "immutable_prefix_max_error": float(np.max(np.abs(check_xyz[:len(base_xyz)] - base_xyz))),
        "official_selection_changed": False,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
