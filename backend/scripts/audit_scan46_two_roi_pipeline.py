"""Read-only audit of scan 46's locked baseline and two repair ROIs.

This script never writes a PLY.  It establishes the exact coordinate chain,
counts observations at each processing layer, and fingerprints every file the
viewer may serve before any candidate repair is allowed to be promoted.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path

import numpy as np
import open3d as o3d


ROOT = Path(__file__).resolve().parents[2]
SCAN45 = ROOT / "backend/data/work/45"
SCAN46 = ROOT / "backend/data/work/46"
POST = SCAN46 / "postprocess"

# Metric/z-up coordinates used by scene_aligned.ply and scene_preview*.ply.
# These are the two user-approved repair targets only.
REGIONS = {
    "entrance_floor": (
        np.array([-.5144042583, .7989766623, -.04]),
        np.array([.3155957417, 1.3889766623, .16]),
    ),
    # Verified against the accepted Figure-4 geometry: the window wall is y-max.
    # Earlier +X bounds selected an unrelated wall and caused false slabs.
    "window_frame_wall": (
        np.array([-.20, 1.275, .82]),
        np.array([1.52, 1.505, 2.28]),
    ),
}


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def file_info(path: Path) -> dict:
    stat = path.stat()
    return {
        "path": str(path.resolve()),
        "bytes": stat.st_size,
        "mtime": datetime.fromtimestamp(stat.st_mtime).isoformat(timespec="seconds"),
        "sha256": sha256(path),
    }


def read_cloud(path: Path) -> tuple[np.ndarray, np.ndarray]:
    cloud = o3d.io.read_point_cloud(str(path))
    xyz = np.asarray(cloud.points, dtype=np.float64)
    rgb = np.asarray(cloud.colors, dtype=np.float64)
    if len(rgb) != len(xyz):
        rgb = np.empty((0, 3), dtype=np.float64)
    return xyz, rgb


def region_stats(xyz: np.ndarray, rgb: np.ndarray | None = None) -> dict:
    answer = {}
    for name, (lo, hi) in REGIONS.items():
        mask = np.all((xyz >= lo) & (xyz <= hi), axis=1)
        selected = xyz[mask]
        entry = {"points": int(len(selected))}
        if len(selected):
            entry["bbox_min"] = selected.min(axis=0).tolist()
            entry["bbox_max"] = selected.max(axis=0).tolist()
            entry["median"] = np.median(selected, axis=0).tolist()
            entry["xyz_quantiles_05_50_95"] = {
                axis: np.quantile(selected[:, index], [.05, .50, .95]).tolist()
                for index, axis in enumerate(("x", "y", "z"))
            }
            if rgb is not None and len(rgb) == len(xyz):
                colors = rgb[mask]
                entry["rgb_median_0_255"] = np.rint(np.median(colors, axis=0) * 255).astype(int).tolist()
        answer[name] = entry
    return answer


def transform_raw_to_metric(xyz: np.ndarray, rotation: np.ndarray, floor_z: float, scale: float) -> np.ndarray:
    aligned = xyz @ rotation.T
    aligned[:, 2] -= floor_z
    return aligned * scale


def ply_layer(path: Path, *, raw_to_metric=None) -> dict:
    xyz, rgb = read_cloud(path)
    raw_bbox = {"min": xyz.min(axis=0).tolist(), "max": xyz.max(axis=0).tolist()}
    if raw_to_metric is not None:
        xyz = raw_to_metric(xyz)
    return {
        **file_info(path),
        "vertices": int(len(xyz)),
        "file_coordinate_bbox": raw_bbox,
        "metric_regions": region_stats(xyz, rgb),
    }


def main() -> None:
    alignment = json.loads((SCAN45 / "postprocess/alignment.json").read_text(encoding="utf-8"))
    rotation = np.asarray(alignment["alignment"]["rotation"], dtype=np.float64)
    floor_z = float(alignment["alignment"]["floor_z_raw_aligned"])
    scale = float(alignment["scale"]["applied"])

    raw_to_metric_4x4 = np.eye(4)
    raw_to_metric_4x4[:3, :3] = scale * rotation
    raw_to_metric_4x4[2, 3] = -scale * floor_z
    metric_to_raw_4x4 = np.linalg.inv(raw_to_metric_4x4)

    anchors_metric = {
        f"{name}_{corner}": (lo if corner == "min" else hi)
        for name, (lo, hi) in REGIONS.items()
        for corner in ("min", "max")
    }
    round_trip = {}
    for name, metric in anchors_metric.items():
        metric_h = np.r_[metric, 1.0]
        raw_h = metric_to_raw_4x4 @ metric_h
        restored = raw_to_metric_4x4 @ raw_h
        round_trip[name] = {
            "metric": metric.tolist(),
            "raw": raw_h[:3].tolist(),
            "round_trip_error_m": float(np.linalg.norm(restored[:3] - metric)),
        }

    selection_path = POST / "preview_selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    selected_path = POST / selection["accepted_file"]

    raw_transform = lambda points: transform_raw_to_metric(points, rotation, floor_z, scale)
    layers = {}
    raw_recon = SCAN45 / "slam3r/scene/45_frames_recon.ply"
    if raw_recon.is_file():
        layers["scene_recon_raw"] = ply_layer(raw_recon, raw_to_metric=raw_transform)
    for key, path in {
        "scene_aligned": SCAN45 / "postprocess/scene_aligned.ply",
        "figure4_locked_baseline": selected_path,
        "rejected_latest_candidate": POST / "scene_preview_figure4_plus_floor_window_ymax_consensus.ply",
    }.items():
        if path.is_file():
            layers[key] = ply_layer(path)

    maps_path = SCAN45 / "slam3r/scene/preds/registered_pcds.npy"
    confs_path = SCAN45 / "slam3r/scene/preds/registered_confs.npy"
    maps = np.load(maps_path, mmap_mode="r")
    confs = np.load(confs_path, mmap_mode="r")
    thresholds = (0.0, 2.0, 4.0, 10.0)
    observations = {str(t): {name: 0 for name in REGIONS} for t in thresholds}
    supported_frames = {str(t): {name: 0 for name in REGIONS} for t in thresholds}
    confidence_values = {name: [] for name in REGIONS}
    coordinate_values = {name: [] for name in REGIONS}
    for frame_idx in range(len(maps)):
        xyz_raw = np.asarray(maps[frame_idx], dtype=np.float64).reshape(-1, 3)
        conf = np.asarray(confs[frame_idx], dtype=np.float32).reshape(-1)
        finite = np.isfinite(xyz_raw).all(axis=1) & np.isfinite(conf)
        xyz = raw_transform(xyz_raw[finite])
        conf = conf[finite]
        for region_name, (lo, hi) in REGIONS.items():
            inside = np.all((xyz >= lo) & (xyz <= hi), axis=1)
            if inside.any():
                values = conf[inside]
                # A bounded deterministic sample is enough for robust quantiles.
                if len(confidence_values[region_name]) < 200000:
                    remaining = max(0, 200000 - len(confidence_values[region_name]))
                    confidence_values[region_name].extend(values[:remaining].tolist())
                    coordinate_values[region_name].extend(xyz[inside][:remaining].tolist())
                for threshold in thresholds:
                    count = int(np.count_nonzero(values >= threshold))
                    observations[str(threshold)][region_name] += count
                    supported_frames[str(threshold)][region_name] += int(count > 0)

    confidence_summary = {}
    for name, values in confidence_values.items():
        arr = np.asarray(values, dtype=np.float32)
        confidence_summary[name] = {
            "sample_count": int(len(arr)),
            "quantiles": np.quantile(arr, [0, .01, .05, .25, .5, .75, .95, .99, 1]).tolist() if len(arr) else [],
        }

    coordinate_summary = {}
    for name, values in coordinate_values.items():
        arr = np.asarray(values, dtype=np.float64)
        coordinate_summary[name] = {
            "sample_count": int(len(arr)),
            "xyz_quantiles": {
                axis: np.quantile(arr[:, axis_index], [0, .01, .05, .25, .5, .75, .95, .99, 1]).tolist()
                for axis_index, axis in enumerate(("x", "y", "z"))
            } if len(arr) else {},
        }

    report = {
        "selection": {**selection, "selection_path": str(selection_path.resolve()), "served_file": file_info(selected_path)},
        "coordinate_chain": {
            "raw_to_metric_4x4": raw_to_metric_4x4.tolist(),
            "metric_to_raw_4x4": metric_to_raw_4x4.tolist(),
            "viewer_transform": "worldGroup.rotation.x = -pi/2; PLY geometry itself is not rescaled or translated",
            "round_trip_anchors": round_trip,
        },
        "regions_metric": {name: {"min": lo.tolist(), "max": hi.tolist()} for name, (lo, hi) in REGIONS.items()},
        "registered_observations": {
            "frames": int(len(maps)),
            "points_by_confidence": observations,
            "frames_with_support": supported_frames,
            "confidence_samples": confidence_summary,
            "coordinate_samples": coordinate_summary,
        },
        "layers": layers,
    }
    output = POST / "two_roi_pipeline_audit.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    print(json.dumps({
        "selection": report["selection"],
        "registered_observations": report["registered_observations"],
        "layer_region_counts": {
            key: {name: stats["points"] for name, stats in layer["metric_regions"].items()}
            for key, layer in layers.items()
        },
    }, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
