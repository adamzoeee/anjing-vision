"""Build a conservative, append-only two-ROI candidate for scan 46.

The accepted Figure-4 cloud is an immutable prefix.  Only registered SLAM3R
observations from the original 900-frame reconstruction may be appended.
For the floor we keep one robust observation per XY cell; for the window wall
we keep one robust observation per XZ cell.  This prevents the thick shells
created by blindly stacking all frame observations.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree


ROOT = Path(__file__).resolve().parents[2]
WORK45 = ROOT / "backend/data/work/45"
POST46 = ROOT / "backend/data/work/46/postprocess"
BASELINE = POST46 / "scene_preview_local_surface_repair_candidate.ply"
FLOOR_OUT = POST46 / "scene_preview_figure4_plus_floor_ymax_consensus.ply"
FINAL_OUT = POST46 / "scene_preview_figure4_plus_floor_window_ymax_consensus.ply"
REPORT = POST46 / "figure4_ymax_consensus_report.json"

# Viewer/metric coordinates, confirmed against the accepted cloud.  The
# window is on y-max; x-max is the bed-side wall and must not be touched.
FLOOR = (np.array([-.49, .72, .075]), np.array([.38, 1.18, .185]))
WINDOW = (np.array([-.20, 1.275, .82]), np.array([1.52, 1.505, 2.28]))
VOXEL = .010
MIN_FRAMES = 3
MIN_BLOCKS = 2
BLOCK_SIZE = 60
MIN_GAP = .012
MAX_GAP = .22


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def write_cloud(path: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(xyz)
    cloud.colors = o3d.utility.Vector3dVector(rgb)
    if not o3d.io.write_point_cloud(str(path), cloud, write_ascii=False, compressed=False):
        raise RuntimeError(f"failed to write {path}")


def collect(region: tuple[np.ndarray, np.ndarray], projection_axes: tuple[int, int],
            maps: np.ndarray, confs: np.ndarray, images: np.ndarray,
            rotation: np.ndarray, floor_z: float, scale: float) -> tuple[np.ndarray, np.ndarray, dict]:
    lo, hi = region
    records: dict[tuple[int, int, int], list] = {}
    for frame_id in range(len(maps)):
        xyz = np.asarray(maps[frame_id], np.float64).reshape(-1, 3)
        conf = np.asarray(confs[frame_id], np.float32).reshape(-1)
        rgb = np.asarray(images[frame_id], np.float32).reshape(-1, 3)
        valid = np.isfinite(xyz).all(axis=1) & np.isfinite(conf) & (conf >= 4.0)
        xyz, conf, rgb = xyz[valid], conf[valid], rgb[valid]
        xyz = xyz @ rotation.T
        xyz[:, 2] -= floor_z
        xyz *= scale
        inside = np.all((xyz >= lo) & (xyz <= hi), axis=1)
        if not np.any(inside):
            continue
        xyz, conf, rgb = xyz[inside], conf[inside], rgb[inside]
        ijk = np.floor((xyz - lo) / VOXEL).astype(np.int32)
        # One observation per 3-D voxel and frame, chosen by confidence.
        keys = [tuple(row) for row in ijk]
        frame_best: dict[tuple[int, int, int], int] = {}
        for index, key in enumerate(keys):
            old = frame_best.get(key)
            if old is None or conf[index] > conf[old]:
                frame_best[key] = index
        block = frame_id // BLOCK_SIZE
        for key, index in frame_best.items():
            item = records.get(key)
            weight = float(np.log1p(max(float(conf[index]), 0.0)))
            if item is None:
                # weighted xyz, weighted rgb, weight, frame count, block bits
                records[key] = [xyz[index] * weight, rgb[index] * weight, weight, 1, {block}]
            else:
                item[0] += xyz[index] * weight
                item[1] += rgb[index] * weight
                item[2] += weight
                item[3] += 1
                item[4].add(block)

    supported = []
    for key, item in records.items():
        if item[3] >= MIN_FRAMES and len(item[4]) >= MIN_BLOCKS:
            supported.append((key, item[0] / item[2], item[1] / item[2], item[3], len(item[4])))

    # Collapse along the surface normal: one representative per XY (floor)
    # or XZ (window wall).  Prefer the cell with greatest temporal support.
    surface: dict[tuple[int, int], tuple] = {}
    for row in supported:
        key = row[0]
        projected = (key[projection_axes[0]], key[projection_axes[1]])
        old = surface.get(projected)
        score = (row[3], row[4])
        if old is None or score > (old[3], old[4]):
            surface[projected] = row
    xyz = np.asarray([row[1] for row in surface.values()], np.float64)
    rgb = np.clip(np.asarray([row[2] for row in surface.values()], np.float64), 0, 255) / 255.0
    return xyz, rgb, {
        "observed_voxels": len(records),
        "temporally_supported_voxels": len(supported),
        "single_surface_cells": len(surface),
    }


def missing_only(xyz: np.ndarray, rgb: np.ndarray, baseline_xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray, dict]:
    if not len(xyz):
        return xyz, rgb, {"added": 0}
    gap, _ = cKDTree(baseline_xyz).query(xyz, workers=-1)
    keep = (gap >= MIN_GAP) & (gap <= MAX_GAP)
    return xyz[keep], rgb[keep], {
        "added": int(np.count_nonzero(keep)),
        "gap_median_m": float(np.median(gap[keep])) if np.any(keep) else None,
        "gap_max_m": float(np.max(gap[keep])) if np.any(keep) else None,
    }


def main() -> None:
    alignment = json.loads((WORK45 / "postprocess/alignment.json").read_text(encoding="utf-8"))
    rotation = np.asarray(alignment["alignment"]["rotation"], np.float64)
    floor_z = float(alignment["alignment"]["floor_z_raw_aligned"])
    scale = float(alignment["scale"]["applied"])
    preds = WORK45 / "slam3r/scene/preds"
    maps = np.load(preds / "registered_pcds.npy", mmap_mode="r")
    confs = np.load(preds / "registered_confs.npy", mmap_mode="r")
    images = np.load(preds / "input_imgs.npy", mmap_mode="r")

    cloud = o3d.io.read_point_cloud(str(BASELINE))
    base_xyz = np.asarray(cloud.points, np.float64)
    base_rgb = np.asarray(cloud.colors, np.float64)
    baseline_hash = sha256(BASELINE)

    floor_xyz, floor_rgb, floor_stats = collect(FLOOR, (0, 1), maps, confs, images, rotation, floor_z, scale)
    floor_xyz, floor_rgb, floor_gap = missing_only(floor_xyz, floor_rgb, base_xyz)
    stage_xyz = np.vstack([base_xyz, floor_xyz])
    stage_rgb = np.vstack([base_rgb, floor_rgb])
    write_cloud(FLOOR_OUT, stage_xyz, stage_rgb)

    window_xyz, window_rgb, window_stats = collect(WINDOW, (0, 2), maps, confs, images, rotation, floor_z, scale)
    window_xyz, window_rgb, window_gap = missing_only(window_xyz, window_rgb, stage_xyz)
    final_xyz = np.vstack([stage_xyz, window_xyz])
    final_rgb = np.vstack([stage_rgb, window_rgb])
    write_cloud(FINAL_OUT, final_xyz, final_rgb)

    # Exact protection check: the immutable prefix must survive bit-for-bit in
    # memory (Open3D writes doubles, so compare reloaded values separately too).
    candidate = o3d.io.read_point_cloud(str(FINAL_OUT))
    cand_xyz = np.asarray(candidate.points, np.float64)
    cand_rgb = np.asarray(candidate.colors, np.float64)
    prefix_xyz_error = float(np.max(np.abs(cand_xyz[:len(base_xyz)] - base_xyz)))
    prefix_rgb_error = float(np.max(np.abs(cand_rgb[:len(base_rgb)] - base_rgb)))
    report = {
        "status": "candidate_not_promoted",
        "method": "figure4_immutable_prefix_original_900_frame_temporal_surface_consensus",
        "baseline": BASELINE.name,
        "baseline_sha256": baseline_hash,
        "baseline_points": int(len(base_xyz)),
        "window_wall_axis": "y-max",
        "floor_region": {"lower": FLOOR[0].tolist(), "upper": FLOOR[1].tolist(), **floor_stats, **floor_gap},
        "window_region": {"lower": WINDOW[0].tolist(), "upper": WINDOW[1].tolist(), **window_stats, **window_gap},
        "output": FINAL_OUT.name,
        "output_points": int(len(final_xyz)),
        "immutable_prefix_xyz_max_error": prefix_xyz_error,
        "immutable_prefix_rgb_max_error_0_1": prefix_rgb_error,
        "outside_two_rois_changed": False,
        "structure_measurements_passage_changed": False,
        "synthetic_planes": 0,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
