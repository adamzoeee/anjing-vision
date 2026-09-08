"""Audit scan 46's locked Figure-4 baseline at the floor and y-max window wall.

Read-only with respect to point clouds: it writes only JSON/PNG QA artifacts.
Registered SLAM3R observations are transformed with scan 45's recorded
alignment, then reduced to per-voxel frame support instead of being piled up.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
WORK45 = ROOT / "backend/data/work/45"
POST46 = ROOT / "backend/data/work/46/postprocess"
BASELINE = POST46 / "scene_preview_local_surface_repair_candidate.ply"
PREDS = WORK45 / "slam3r/scene/preds"
OUT = POST46 / "qa_figure4_ymax_window_floor"


def read_cloud(path: Path) -> tuple[np.ndarray, np.ndarray]:
    cloud = o3d.io.read_point_cloud(str(path))
    return np.asarray(cloud.points, dtype=np.float64), np.asarray(cloud.colors, dtype=np.float64)


def raster(points: np.ndarray, axes: tuple[int, int], bounds: tuple[tuple[float, float], tuple[float, float]], path: Path, title: str) -> None:
    width, height = 1400, 950
    rgb = np.full((height, width, 3), 14, dtype=np.uint8)
    (u0, u1), (v0, v1) = bounds
    u = ((points[:, axes[0]] - u0) / max(u1 - u0, 1e-9) * (width - 61) + 30).astype(int)
    v = ((v1 - points[:, axes[1]]) / max(v1 - v0, 1e-9) * (height - 61) + 30).astype(int)
    valid = (u >= 30) & (u < width - 30) & (v >= 30) & (v < height - 30)
    rgb[v[valid], u[valid]] = (230, 230, 230)
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    draw.text((18, 8), title, fill=(255, 220, 50))
    draw.rectangle((30, 30, width - 30, height - 30), outline=(80, 150, 255), width=2)
    image.save(path)


def supported_voxels(frames: np.ndarray, confs: np.ndarray, rotation: np.ndarray, floor_raw: float, scale: float,
                     lo: np.ndarray, hi: np.ndarray, voxel: float, min_conf: float) -> tuple[np.ndarray, np.ndarray, dict]:
    observations: dict[tuple[int, int, int], list[np.ndarray]] = {}
    frame_sets: dict[tuple[int, int, int], set[int]] = {}
    block_sets: dict[tuple[int, int, int], set[int]] = {}
    raw_count = 0
    for frame_index in range(frames.shape[0]):
        xyz = np.asarray(frames[frame_index], dtype=np.float64).reshape(-1, 3)
        confidence = np.asarray(confs[frame_index], dtype=np.float64).reshape(-1)
        valid = np.isfinite(xyz).all(axis=1) & (confidence >= min_conf)
        xyz = xyz[valid]
        xyz = xyz @ rotation.T
        xyz[:, 2] -= floor_raw
        xyz *= scale
        inside = np.all((xyz >= lo) & (xyz <= hi), axis=1)
        xyz = xyz[inside]
        raw_count += len(xyz)
        if not len(xyz):
            continue
        keys = np.floor((xyz - lo) / voxel).astype(np.int32)
        unique, inverse = np.unique(keys, axis=0, return_inverse=True)
        for idx, key_arr in enumerate(unique):
            key = tuple(int(x) for x in key_arr)
            observations.setdefault(key, []).append(np.median(xyz[inverse == idx], axis=0))
            frame_sets.setdefault(key, set()).add(frame_index)
            block_sets.setdefault(key, set()).add(frame_index // 60)
    keys = list(observations)
    representatives = np.vstack([np.median(np.vstack(observations[key]), axis=0) for key in keys]) if keys else np.empty((0, 3))
    support = np.asarray([[len(frame_sets[key]), len(block_sets[key])] for key in keys], dtype=np.int32) if keys else np.empty((0, 2), dtype=np.int32)
    stats = {
        "raw_observations": raw_count,
        "occupied_voxels": len(keys),
        "voxels_frames_ge_2": int(np.count_nonzero(support[:, 0] >= 2)) if len(support) else 0,
        "voxels_frames_ge_3_blocks_ge_2": int(np.count_nonzero((support[:, 0] >= 3) & (support[:, 1] >= 2))) if len(support) else 0,
    }
    return representatives, support, stats


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    alignment = json.loads((WORK45 / "postprocess/alignment.json").read_text(encoding="utf-8"))
    rotation = np.asarray(alignment["alignment"]["rotation"], dtype=np.float64)
    floor_raw = float(alignment["alignment"]["floor_z_raw_aligned"])
    scale = float(alignment["scale"]["applied"])
    ext = alignment["extents_m"]
    xmin, xmax = map(float, ext["x"])
    ymin, ymax = map(float, ext["y"])
    baseline, _ = read_cloud(BASELINE)

    # Only observed geometry near the established physical surfaces is audited.
    floor_lo = np.array([xmin, ymin, -0.025])
    floor_hi = np.array([xmax, ymax, 0.035])
    # Window dimensions come from the verified video annotation; wall axis and
    # wall coordinate come strictly from the Figure-4 point-cloud extents.
    window_lo = np.array([-0.20, ymax - 0.045, 0.88])
    window_hi = np.array([1.50, ymax + 0.045, 2.25])

    registered = np.load(PREDS / "registered_pcds.npy", mmap_mode="r")
    confs = np.load(PREDS / "registered_confs.npy", mmap_mode="r")
    report: dict[str, object] = {"baseline": str(BASELINE), "registered_shape": list(registered.shape), "regions": {}}
    for name, lo, hi in (("floor", floor_lo, floor_hi), ("window_ymax", window_lo, window_hi)):
        base_mask = np.all((baseline >= lo) & (baseline <= hi), axis=1)
        reps, support, stats = supported_voxels(registered, confs, rotation, floor_raw, scale, lo, hi, .012, 1.2)
        strong = (support[:, 0] >= 3) & (support[:, 1] >= 2) if len(support) else np.zeros(0, dtype=bool)
        strong_points = reps[strong]
        report["regions"][name] = {
            "bounds": {"min": lo.tolist(), "max": hi.tolist()},
            "baseline_points": int(np.count_nonzero(base_mask)),
            **stats,
            "strong_voxels": int(len(strong_points)),
            "strong_bbox_min": strong_points.min(axis=0).tolist() if len(strong_points) else None,
            "strong_bbox_max": strong_points.max(axis=0).tolist() if len(strong_points) else None,
        }
        if name == "floor":
            raster(baseline[base_mask], (0, 1), ((xmin, xmax), (ymin, ymax)), OUT / "baseline_floor_xy.png", "Figure4 baseline floor z=-0.025..0.035")
            raster(strong_points, (0, 1), ((xmin, xmax), (ymin, ymax)), OUT / "registered_floor_xy.png", "Registered strong floor observations")
        else:
            raster(baseline[base_mask], (0, 2), ((xmin, xmax), (0.0, 2.61)), OUT / "baseline_window_xz.png", "Figure4 baseline y-max window wall")
            raster(strong_points, (0, 2), ((xmin, xmax), (0.0, 2.61)), OUT / "registered_window_xz.png", "Registered strong y-max wall observations")
    output = OUT / "report.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
