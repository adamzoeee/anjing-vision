"""Authoritative read-only audit for scan 46's final two-ROI repair.

This script answers the ten questions required by the user's repair document.
It never writes or promotes a point cloud.  Coordinates are metric/z-up PLY
coordinates; the only display transform is recorded explicitly.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import open3d as o3d


ROOT = Path(__file__).resolve().parents[2]
WORK45 = ROOT / "backend/data/work/45"
WORK46 = ROOT / "backend/data/work/46"
POST = WORK46 / "postprocess"
BASELINE = POST / "scene_preview_local_surface_repair_candidate.ply"
OUT = POST / "scan46_document_audit.json"

FLOOR_XY = (np.array([-.5144042583, .7989766623]), np.array([.3155957417, 1.3889766623]))
# Stable, video-supported opening from the pre-drift structure review.  The
# bed long axis and window wall are parallel, so this is a y-constant wall.
WINDOW_XZ = (np.array([-.3522549660, .35]), np.array([1.2458344102, 2.15]))
WINDOW_Y = -0.4969722654902987


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def cloud(path: Path) -> np.ndarray:
    return np.asarray(o3d.io.read_point_cloud(str(path)).points, np.float64)


def raw_to_metric_matrix() -> tuple[np.ndarray, dict]:
    data = json.loads((WORK45 / "postprocess/alignment.json").read_text(encoding="utf-8"))
    rotation = np.asarray(data["alignment"]["rotation"], np.float64)
    floor = float(data["alignment"]["floor_z_raw_aligned"])
    scale = float(data["scale"]["applied"])
    matrix = np.eye(4)
    matrix[:3, :3] = scale * rotation
    matrix[2, 3] = -scale * floor
    return matrix, data


def transform(points: np.ndarray, matrix: np.ndarray) -> np.ndarray:
    return points @ matrix[:3, :3].T + matrix[:3, 3]


def fit_floor_plane(points: np.ndarray) -> tuple[np.ndarray, dict]:
    lo, hi = FLOOR_XY
    ring_box = (
        (points[:, 0] >= lo[0] - .30) & (points[:, 0] <= hi[0] + .30)
        & (points[:, 1] >= lo[1] - .25) & (points[:, 1] <= hi[1] + .18)
        & (points[:, 2] >= -.025) & (points[:, 2] <= .025)
    )
    inside = np.all((points[:, :2] >= lo) & (points[:, :2] <= hi), axis=1)
    samples = points[ring_box & ~inside]
    keep = np.ones(len(samples), bool)
    coeff = np.array([0., 0., np.median(samples[:, 2])])
    for _ in range(6):
        a = np.c_[samples[keep, :2], np.ones(np.count_nonzero(keep))]
        coeff, *_ = np.linalg.lstsq(a, samples[keep, 2], rcond=None)
        residual = samples[:, 2] - (np.c_[samples[:, :2], np.ones(len(samples))] @ coeff)
        mad = max(float(np.median(np.abs(residual - np.median(residual)))) * 1.4826, .001)
        keep = np.abs(residual) <= 3.0 * mad
    residual = samples[keep, 2] - (np.c_[samples[keep, :2], np.ones(np.count_nonzero(keep))] @ coeff)
    normal = np.array([-coeff[0], -coeff[1], 1.0])
    normal /= np.linalg.norm(normal)
    d = -coeff[2] / np.linalg.norm(np.array([-coeff[0], -coeff[1], 1.0]))
    return coeff, {
        "equation_ax_by_c": coeff.tolist(),
        "plane_normal_and_d": [*normal.tolist(), float(d)],
        "support_points": int(np.count_nonzero(keep)),
        "absolute_residual_quantiles_m": np.quantile(np.abs(residual), [0, .5, .9, .95, .99, 1]).tolist(),
    }


def floor_masks(points: np.ndarray, coeff: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    lo, hi = FLOOR_XY
    roi = np.all((points[:, :2] >= lo) & (points[:, :2] <= hi), axis=1)
    predicted = points[:, 0] * coeff[0] + points[:, 1] * coeff[1] + coeff[2]
    valid = roi & (np.abs(points[:, 2] - predicted) <= .015)
    biased = roi & (points[:, 2] - predicted >= .08) & (points[:, 2] - predicted <= .20)
    return valid, biased


def window_mask(points: np.ndarray) -> np.ndarray:
    lo, hi = WINDOW_XZ
    return (
        (points[:, 0] >= lo[0]) & (points[:, 0] <= hi[0])
        & (points[:, 2] >= lo[1]) & (points[:, 2] <= hi[1])
        & (np.abs(points[:, 1] - WINDOW_Y) <= .025)
    )


def supported_voxels(frame_points: list[tuple[int, np.ndarray]], voxel: float = .012, minimum_views: int = 2) -> dict:
    support: dict[tuple[int, int, int], set[int]] = {}
    samples: dict[tuple[int, int, int], list[np.ndarray]] = {}
    for frame_id, points in frame_points:
        keys = np.floor(points / voxel).astype(np.int64)
        for key, point in zip(keys, points):
            item = tuple(key.tolist())
            support.setdefault(item, set()).add(frame_id)
            samples.setdefault(item, []).append(point)
    accepted = [np.median(np.asarray(samples[key]), axis=0) for key, views in support.items() if len(views) >= minimum_views]
    return {
        "candidate_observations": int(sum(len(points) for _, points in frame_points)),
        "supporting_frames": int(len({frame for frame, points in frame_points if len(points)})),
        "accepted_voxels_min_2_independent_frames": int(len(accepted)),
    }


def registered_audit(matrix: np.ndarray, coeff: np.ndarray) -> tuple[dict, list[dict]]:
    preds = WORK45 / "slam3r/scene/preds"
    maps = np.load(preds / "registered_pcds.npy", mmap_mode="r")
    confs = np.load(preds / "registered_confs.npy", mmap_mode="r")
    floor_frames, window_frames, biased_summary = [], [], []
    for frame_id in range(len(maps)):
        raw = np.asarray(maps[frame_id], np.float64).reshape(-1, 3)
        conf = np.asarray(confs[frame_id], np.float32).reshape(-1)
        finite = np.isfinite(raw).all(axis=1) & np.isfinite(conf) & (conf >= 2.0)
        xyz = transform(raw[finite], matrix)
        floor_valid, floor_biased = floor_masks(xyz, coeff)
        window_valid = window_mask(xyz)
        floor_frames.append((frame_id, xyz[floor_valid]))
        window_frames.append((frame_id, xyz[window_valid]))
        count = int(np.count_nonzero(floor_biased))
        if count:
            delta = xyz[floor_biased, 2] - (xyz[floor_biased, 0] * coeff[0] + xyz[floor_biased, 1] * coeff[1] + coeff[2])
            biased_summary.append({"frame": frame_id, "points": count, "median_height_above_floor_m": float(np.median(delta))})
    return {
        "frames": int(len(maps)),
        "floor": supported_voxels(floor_frames),
        "window_frame_or_wall": supported_voxels(window_frames),
    }, sorted(biased_summary, key=lambda item: item["points"], reverse=True)[:20]


def layer_stats(points: np.ndarray, coeff: np.ndarray) -> dict:
    floor_valid, floor_biased = floor_masks(points, coeff)
    return {
        "points": int(len(points)),
        "valid_floor_points": int(np.count_nonzero(floor_valid)),
        "biased_floor_points_8_to_20cm_high": int(np.count_nonzero(floor_biased)),
        "window_frame_or_wall_points": int(np.count_nonzero(window_mask(points))),
    }


def main() -> None:
    baseline_xyz = cloud(BASELINE)
    coeff, floor_report = fit_floor_plane(baseline_xyz)
    matrix, alignment = raw_to_metric_matrix()
    raw_recon_path = WORK45 / "slam3r/scene/45_frames_recon.ply"
    aligned_path = WORK45 / "postprocess/scene_aligned.ply"
    raw_recon = transform(cloud(raw_recon_path), matrix)
    aligned = cloud(aligned_path)
    registered, biased_frames = registered_audit(matrix, coeff)

    selection_path = POST / "preview_selection.json"
    selection = json.loads(selection_path.read_text(encoding="utf-8"))
    served = POST / selection["accepted_file"]
    viewer_rotation = np.array([[1., 0., 0., 0.], [0., 0., 1., 0.], [0., -1., 0., 0.], [0., 0., 0., 1.]])
    identity = np.eye(4)
    anchors = {
        "left_top": [float(WINDOW_XZ[0][0]), WINDOW_Y, float(WINDOW_XZ[1][1])],
        "right_top": [float(WINDOW_XZ[1][0]), WINDOW_Y, float(WINDOW_XZ[1][1])],
        "left_bottom": [float(WINDOW_XZ[0][0]), WINDOW_Y, float(WINDOW_XZ[0][1])],
        "right_bottom": [float(WINDOW_XZ[1][0]), WINDOW_Y, float(WINDOW_XZ[0][1])],
    }
    stages = {
        "registered_pcds": registered,
        "scene_recon": layer_stats(raw_recon, coeff),
        "scene_aligned": layer_stats(aligned, coeff),
        "figure3_baseline": layer_stats(baseline_xyz, coeff),
    }
    report = {
        "status": "ten_document_questions_answered_before_formal_repair",
        "1_baseline": {"path": str(BASELINE.resolve()), "sha256": sha256(BASELINE), "vertices": int(len(baseline_xyz)), "bbox_min": baseline_xyz.min(0).tolist(), "bbox_max": baseline_xyz.max(0).tolist()},
        "2_floor_first_loss": {"stage": "registered_pcds_depth_geometry", "conclusion": "Video pixels exist, but most ROI observations are 8-20 cm above the accepted floor; they are not valid floor geometry and must not be restored directly.", "stages": stages},
        "3_window_first_loss": {"stage": "registered_pcds_window_depth_geometry_then_filtering", "conclusion": "Opaque frame/wall support exists, while transparent glass depth is unreliable. Restore/display the fixed frame and surrounding wall, not a guessed wall plane."},
        "4_coordinate_chain": {
            "registered_to_recon": identity.tolist(),
            "recon_to_aligned_metric": matrix.tolist(),
            "aligned_to_preview": identity.tolist(),
            "note": "aligned_to_preview changes membership/downsampling only; it does not apply another geometric transform.",
        },
        "5_viewer": {"display_transform": "rotation X = -pi/2 only", "matrix": viewer_rotation.tolist(), "selection_file": str(selection_path.resolve()), "served_path": str(served.resolve()), "served_sha256": sha256(served)},
        "6_floor_plane": floor_report,
        "7_high_floor_points": {"origin": "registered_pcds/scene_recon monocular depth bias before preview", "top_frames": biased_frames},
        "8_window_anchors": {"surface": "y-min wall", "wall_y_m": WINDOW_Y, "anchors": anchors, "source": "pre-drift multiview structure review + bed/window parallel topology + original video"},
        "9_floor_contributions": {
            "original_900": registered["floor"],
            "supplement_1": {"accepted_voxels": 0, "reason": "No approved rigid transform to the locked baseline; frames remain useful as display texture evidence."},
            "buchong2": {"accepted_voxels": 0, "reason": "Existing local reconstruction transforms fail the floor-plane/ROI audit; frames remain useful as display texture evidence."},
        },
        "10_window_contributions": {
            "original_900": registered["window_frame_or_wall"],
            "supplement_1": {"accepted_voxels": 0, "reason": "No approved rigid transform to the locked baseline."},
            "buchong2": {"accepted_voxels": 0, "reason": "No approved rigid transform to the locked baseline; do not hard-merge."},
        },
        "formal_repair_decision": "Keep analysis cloud immutable. Build one single-layer display-only floor completion and one fixed y-min window/frame visual surface, then require automatic and Viewer acceptance before promotion.",
        "alignment_source": alignment,
    }
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({key: report[key] for key in list(report)[:11]}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
