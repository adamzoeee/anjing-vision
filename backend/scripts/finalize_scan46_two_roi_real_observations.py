"""Combine the two accepted real-observation ROI patches over immutable Figure 4."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import open3d as o3d


ROOT = Path(__file__).resolve().parents[2]
POST = ROOT / "backend/data/work/46/postprocess"
BASE = POST / "scene_preview_local_surface_repair_candidate.ply"
FLOOR = POST / "scene_preview_figure4_floor_direct_candidate.ply"
WINDOW = POST / "scene_preview_figure4_window_direct_candidate.ply"
OUTPUT = POST / "scene_preview_figure4_two_roi_real_final.ply"
REPORT = POST / "figure4_two_roi_real_final_report.json"


def load(path: Path) -> tuple[np.ndarray, np.ndarray]:
    cloud = o3d.io.read_point_cloud(str(path))
    return np.asarray(cloud.points, np.float64), np.asarray(cloud.colors, np.float64)


def digest(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def main() -> None:
    base_xyz, base_rgb = load(BASE)
    floor_xyz, floor_rgb = load(FLOOR)
    window_xyz, window_rgb = load(WINDOW)
    n = len(base_xyz)
    for name, xyz, rgb in (("floor", floor_xyz, floor_rgb), ("window", window_xyz, window_rgb)):
        if len(xyz) < n or not np.array_equal(xyz[:n], base_xyz) or not np.array_equal(rgb[:n], base_rgb):
            raise RuntimeError(f"{name} candidate does not preserve immutable Figure 4 prefix")

    floor_add, floor_color = floor_xyz[n:], floor_rgb[n:]
    floor_ok = (
        (floor_add[:, 0] >= -.48) & (floor_add[:, 0] <= .55)
        & (floor_add[:, 1] >= .55) & (floor_add[:, 1] <= 1.39)
        & (floor_add[:, 2] >= -.035) & (floor_add[:, 2] <= .045)
    )
    if not np.all(floor_ok):
        raise RuntimeError("floor patch escaped protected ROI")

    window_add, window_color = window_xyz[n:], window_rgb[n:]
    x, y, z = window_add.T
    window_band = (x <= .02) | (x >= 1.30) | (z <= 1.04) | (z >= 2.06)
    window_ok = (
        (x >= -.20) & (x <= 1.52) & (y >= 1.302) & (y <= 1.372)
        & (z >= .82) & (z <= 2.28) & window_band
    )
    if not np.all(window_ok):
        raise RuntimeError("window patch escaped frame/wall border ROI")

    xyz = np.vstack([base_xyz, floor_add, window_add])
    rgb = np.vstack([base_rgb, floor_color, window_color])
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(xyz)
    cloud.colors = o3d.utility.Vector3dVector(rgb)
    if not o3d.io.write_point_cloud(str(OUTPUT), cloud, write_ascii=False, compressed=False):
        raise RuntimeError(OUTPUT)

    check_xyz, check_rgb = load(OUTPUT)
    report = {
        "status": "candidate_pending_full_visual_qa",
        "method": "immutable_figure4_plus_two_real_observation_roi_patches",
        "baseline": BASE.name,
        "baseline_sha256": digest(BASE),
        "baseline_points": n,
        "floor_real_points_added": int(len(floor_add)),
        "floor_z_p05_p50_p95_m": np.quantile(floor_add[:, 2], [.05, .5, .95]).tolist(),
        "window_real_points_added": int(len(window_add)),
        "output": OUTPUT.name,
        "output_points": int(len(xyz)),
        "immutable_prefix_xyz_max_error": float(np.max(np.abs(check_xyz[:n] - base_xyz))),
        "immutable_prefix_rgb_max_error": float(np.max(np.abs(check_rgb[:n] - base_rgb))),
        "outside_two_roi_changed": False,
        "synthetic_points": 0,
        "structure_dimensions_passage_changed": False,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
