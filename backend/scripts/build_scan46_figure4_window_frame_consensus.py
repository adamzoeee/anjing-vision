"""Append only original-frame consensus points around the scan-46 window frame."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree


ROOT = Path(__file__).resolve().parents[2]
WORK45 = ROOT / "backend/data/work/45"
POST = ROOT / "backend/data/work/46/postprocess"
BASELINE = POST / "scene_preview_local_surface_repair_candidate.ply"
OUTPUT = POST / "scene_preview_figure4_plus_window_frame_consensus.ply"
REPORT = POST / "figure4_window_frame_consensus_report.json"

# Verified metric-PLY coordinates.  The viewer rotates this coordinate system,
# which is why the same physical wall was previously (incorrectly) called +X.
LO = np.array([-.20, 1.302, .82])
HI = np.array([1.52, 1.372, 2.28])
WALL_Y = 1.3368
VOXEL = .010
MIN_FRAMES = 4
MIN_BLOCKS = 2
BLOCK_SIZE = 60
MIN_GAP = .013
MAX_GAP = .09


def digest(path: Path) -> str:
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
        raise RuntimeError(path)


def is_frame_band(xyz: np.ndarray) -> np.ndarray:
    # Preserve the glass opening.  Restore only the frame and surrounding wall.
    x, z = xyz[:, 0], xyz[:, 2]
    return (x <= .02) | (x >= 1.30) | (z <= 1.04) | (z >= 2.06)


def main() -> None:
    alignment = json.loads((WORK45 / "postprocess/alignment.json").read_text(encoding="utf-8"))
    rotation = np.asarray(alignment["alignment"]["rotation"], np.float64)
    floor_z = float(alignment["alignment"]["floor_z_raw_aligned"])
    scale = float(alignment["scale"]["applied"])
    preds = WORK45 / "slam3r/scene/preds"
    maps = np.load(preds / "registered_pcds.npy", mmap_mode="r")
    confs = np.load(preds / "registered_confs.npy", mmap_mode="r")
    images = np.load(preds / "input_imgs.npy", mmap_mode="r")

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
        inside = np.all((xyz >= LO) & (xyz <= HI), axis=1) & is_frame_band(xyz)
        xyz, conf, rgb = xyz[inside], conf[inside], rgb[inside]
        if not len(xyz):
            continue
        ijk = np.floor((xyz - LO) / VOXEL).astype(np.int32)
        frame_best: dict[tuple[int, int, int], int] = {}
        for index, key_array in enumerate(ijk):
            key = tuple(key_array)
            old = frame_best.get(key)
            if old is None or conf[index] > conf[old]:
                frame_best[key] = index
        block = frame_id // BLOCK_SIZE
        for key, index in frame_best.items():
            weight = float(np.log1p(max(float(conf[index]), 0.0)))
            item = records.get(key)
            if item is None:
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

    # One representative per XZ surface cell.  Prefer temporal support, then
    # the observation closest to the already verified wall plane.
    surface: dict[tuple[int, int], tuple] = {}
    for row in supported:
        key = row[0]
        projected = (key[0], key[2])
        score = (row[3], row[4], -abs(float(row[1][1]) - WALL_Y))
        old = surface.get(projected)
        if old is None or score > old[-1]:
            surface[projected] = (*row, score)
    add_xyz = np.asarray([row[1] for row in surface.values()], np.float64)
    add_rgb = np.clip(np.asarray([row[2] for row in surface.values()], np.float64), 0, 255) / 255.0

    base_cloud = o3d.io.read_point_cloud(str(BASELINE))
    base_xyz = np.asarray(base_cloud.points, np.float64)
    base_rgb = np.asarray(base_cloud.colors, np.float64)
    if len(add_xyz):
        gap, _ = cKDTree(base_xyz).query(add_xyz, workers=-1)
        keep = (gap >= MIN_GAP) & (gap <= MAX_GAP)
        add_xyz, add_rgb, kept_gap = add_xyz[keep], add_rgb[keep], gap[keep]
    else:
        kept_gap = np.empty(0)

    final_xyz = np.vstack([base_xyz, add_xyz])
    final_rgb = np.vstack([base_rgb, add_rgb])
    write_cloud(OUTPUT, final_xyz, final_rgb)
    loaded = o3d.io.read_point_cloud(str(OUTPUT))
    loaded_xyz = np.asarray(loaded.points, np.float64)
    loaded_rgb = np.asarray(loaded.colors, np.float64)
    report = {
        "status": "candidate_not_promoted",
        "baseline": BASELINE.name,
        "baseline_sha256": digest(BASELINE),
        "baseline_points": int(len(base_xyz)),
        "method": "immutable_figure4_prefix_original_900_frame_window_frame_consensus",
        "wall_axis_metric_ply": "+Y",
        "wall_y_m": WALL_Y,
        "roi_lower": LO.tolist(),
        "roi_upper": HI.tolist(),
        "glass_opening_preserved": True,
        "observed_voxels": len(records),
        "temporally_supported_voxels": len(supported),
        "surface_cells": len(surface),
        "added_points": int(len(add_xyz)),
        "added_gap_median_m": float(np.median(kept_gap)) if len(kept_gap) else None,
        "output": OUTPUT.name,
        "output_points": int(len(final_xyz)),
        "immutable_prefix_xyz_max_error": float(np.max(np.abs(loaded_xyz[:len(base_xyz)] - base_xyz))),
        "immutable_prefix_rgb_max_error": float(np.max(np.abs(loaded_rgb[:len(base_rgb)] - base_rgb))),
        "floor_changed": False,
        "outside_window_roi_changed": False,
        "synthetic_points": 0,
        "structure_measurements_passage_changed": False,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
