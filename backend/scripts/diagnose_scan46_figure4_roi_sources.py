"""Diagnose real same-coordinate sources around scan 46's two locked ROIs.

Read-only: this script never writes a PLY.  It compares the pre-filtered
SLAM3R reconstruction with the user-selected Figure-4 baseline, quantifies
surface layers, and reports whether append-only recovery is geometrically
safe before a candidate may be built.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree


ROOT = Path(__file__).resolve().parents[2]
WORK45 = ROOT / "backend/data/work/45"
POST46 = ROOT / "backend/data/work/46/postprocess"
BASELINE = POST46 / "scene_preview_local_surface_repair_candidate.ply"
RECON = WORK45 / "slam3r/scene/45_frames_recon.ply"

REGIONS = {
    "entrance_floor": (
        np.array([-.5144042583, .7989766623, -.04]),
        np.array([.3155957417, 1.3889766623, .16]),
    ),
    "window_frame_wall": (
        np.array([1.66213544998, -.53097226549, .20]),
        np.array([1.99213544998, .81902773451, 1.55]),
    ),
}


def read_cloud(path: Path) -> tuple[np.ndarray, np.ndarray]:
    cloud = o3d.io.read_point_cloud(str(path))
    return np.asarray(cloud.points), np.asarray(cloud.colors)


def quantiles(values: np.ndarray) -> list[float]:
    return np.quantile(values, [0, .01, .05, .25, .5, .75, .95, .99, 1]).tolist() if len(values) else []


def fitted_surface(xyz: np.ndarray, axis: int) -> dict:
    predictors = [index for index in range(3) if index != axis]
    matrix = np.c_[xyz[:, predictors], np.ones(len(xyz))]
    coefficients, *_ = np.linalg.lstsq(matrix, xyz[:, axis], rcond=None)
    residual = xyz[:, axis] - matrix @ coefficients
    return {
        "axis": "xyz"[axis],
        "predictors": ["xyz"[index] for index in predictors],
        "coefficients": coefficients.tolist(),
        "absolute_residual_m_quantiles": quantiles(np.abs(residual)),
        "within_0.01m": int(np.count_nonzero(np.abs(residual) <= .01)),
        "within_0.02m": int(np.count_nonzero(np.abs(residual) <= .02)),
    }


def main() -> None:
    alignment = json.loads((WORK45 / "postprocess/alignment.json").read_text(encoding="utf-8"))
    rotation = np.asarray(alignment["alignment"]["rotation"], dtype=np.float64)
    floor_z = float(alignment["alignment"]["floor_z_raw_aligned"])
    scale = float(alignment["scale"]["applied"])

    source_xyz, source_rgb = read_cloud(RECON)
    source_xyz = source_xyz @ rotation.T
    source_xyz[:, 2] -= floor_z
    source_xyz *= scale
    base_xyz, base_rgb = read_cloud(BASELINE)

    report: dict[str, object] = {
        "baseline": str(BASELINE.resolve()),
        "source": str(RECON.resolve()),
        "regions": {},
    }
    base_tree = cKDTree(base_xyz)
    for name, (lo, hi) in REGIONS.items():
        source_mask = np.all((source_xyz >= lo) & (source_xyz <= hi), axis=1)
        base_mask = np.all((base_xyz >= lo) & (base_xyz <= hi), axis=1)
        src = source_xyz[source_mask]
        rgb = source_rgb[source_mask]
        base = base_xyz[base_mask]
        gap, _ = base_tree.query(src, workers=-1)

        # Surface coordinate: z for floor, x for the verified +X window wall.
        axis = 2 if name == "entrance_floor" else 0
        voxel = .01
        keys = np.floor((src - lo) / voxel).astype(np.int64)
        unique_keys, inverse, counts = np.unique(keys, axis=0, return_inverse=True, return_counts=True)
        representatives = np.vstack([np.median(src[inverse == idx], axis=0) for idx in range(len(unique_keys))])
        representative_rgb = np.vstack([np.median(rgb[inverse == idx], axis=0) for idx in range(len(unique_keys))])
        representative_gap, _ = base_tree.query(representatives, workers=-1)

        report["regions"][name] = {
            "bounds": {"min": lo.tolist(), "max": hi.tolist()},
            "source_points": int(len(src)),
            "baseline_points": int(len(base)),
            "source_surface_axis": "z" if axis == 2 else "x",
            "source_surface_quantiles": quantiles(src[:, axis]),
            "baseline_surface_quantiles": quantiles(base[:, axis]),
            "source_to_baseline_gap_m_quantiles": quantiles(gap),
            "source_points_missing_by_gap": {
                "ge_0.01": int(np.count_nonzero(gap >= .01)),
                "ge_0.02": int(np.count_nonzero(gap >= .02)),
                "ge_0.04": int(np.count_nonzero(gap >= .04)),
                "ge_0.08": int(np.count_nonzero(gap >= .08)),
            },
            "voxel_0.01_representatives": int(len(representatives)),
            "representative_surface_quantiles": quantiles(representatives[:, axis]),
            "representative_gap_m_quantiles": quantiles(representative_gap),
            "representatives_missing_ge_0.02": int(np.count_nonzero(representative_gap >= .02)),
            "median_rgb_0_255": np.rint(np.median(representative_rgb, axis=0) * 255).astype(int).tolist(),
            "voxel_observation_count_quantiles": quantiles(counts),
            "source_fitted_surface": fitted_surface(src, axis),
        }

    staged_candidates = {
        "floor": (POST46 / "scene_preview_figure2_plus_real_floor_consensus_candidate.ply", 1192),
        "window": (POST46 / "scene_preview_figure2_plus_real_floor_window_consensus_candidate.ply", 24541),
    }
    report["prior_candidate_suffixes"] = {}
    for name, (path, suffix_count) in staged_candidates.items():
        if not path.is_file():
            continue
        xyz, _ = read_cloud(path)
        suffix = xyz[-suffix_count:]
        report["prior_candidate_suffixes"][name] = {
            "points": int(len(suffix)),
            "bbox_min": suffix.min(axis=0).tolist(),
            "bbox_max": suffix.max(axis=0).tolist(),
            "xyz_quantiles": {
                axis_name: quantiles(suffix[:, axis])
                for axis, axis_name in enumerate(("x", "y", "z"))
            },
        }

    output = POST46 / "figure4_roi_source_diagnostic.json"
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(output)
    print(json.dumps(report["regions"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
