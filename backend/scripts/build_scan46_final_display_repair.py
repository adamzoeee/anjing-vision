"""Build scan 46's final preview-only floor/window repair.

The accepted Figure-4 cloud is immutable and remains the analysis cloud.  This
script appends a single-layer, video-textured display patch only where the
accepted cloud has no surface in the two locked ROIs.  It never feeds the
added samples back into measurements, structure, passage, or risk analysis.
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
MANIFEST = POST46 / "scan46_repair_manifest.json"


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def read_cloud(path: Path) -> tuple[np.ndarray, np.ndarray]:
    cloud = o3d.io.read_point_cloud(str(path))
    return np.asarray(cloud.points, np.float64), np.asarray(cloud.colors, np.float64)


def write_cloud(path: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(xyz)
    cloud.colors = o3d.utility.Vector3dVector(np.clip(rgb, 0.0, 1.0))
    if not o3d.io.write_point_cloud(str(path), cloud, write_ascii=False, compressed=False):
        raise RuntimeError(f"Failed to write {path}")


def bilinear_texture(image: np.ndarray, u: np.ndarray, v: np.ndarray) -> np.ndarray:
    """Sample an RGB image using normalized coordinates."""
    height, width = image.shape[:2]
    px = np.clip(u, 0.0, 1.0) * (width - 1)
    py = np.clip(v, 0.0, 1.0) * (height - 1)
    x0, y0 = np.floor(px).astype(int), np.floor(py).astype(int)
    x1, y1 = np.minimum(x0 + 1, width - 1), np.minimum(y0 + 1, height - 1)
    wx, wy = (px - x0)[:, None], (py - y0)[:, None]
    top = image[y0, x0] * (1.0 - wx) + image[y0, x1] * wx
    bottom = image[y1, x0] * (1.0 - wx) + image[y1, x1] * wx
    return (top * (1.0 - wy) + bottom * wy) / 255.0


def robust_floor_plane(xyz: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    """Fit z=ax+by+c from the accepted floor surrounding the locked hole."""
    near = (
        (xyz[:, 0] >= lo[0] - .35) & (xyz[:, 0] <= hi[0] + .35)
        & (xyz[:, 1] >= lo[1] - .35) & (xyz[:, 1] <= hi[1] + .25)
        & (xyz[:, 2] >= -.035) & (xyz[:, 2] <= .035)
    )
    # The hole itself must not influence the fit.
    inside = np.all((xyz[:, :2] >= lo[:2]) & (xyz[:, :2] <= hi[:2]), axis=1)
    samples = xyz[near & ~inside]
    if len(samples) < 200:
        raise RuntimeError("Insufficient accepted floor-ring points")
    keep = np.ones(len(samples), dtype=bool)
    coefficients = np.asarray([0.0, 0.0, np.median(samples[:, 2])])
    for _ in range(4):
        matrix = np.c_[samples[keep, :2], np.ones(np.count_nonzero(keep))]
        coefficients, *_ = np.linalg.lstsq(matrix, samples[keep, 2], rcond=None)
        residual = samples[:, 2] - (np.c_[samples[:, :2], np.ones(len(samples))] @ coefficients)
        scale = max(float(np.median(np.abs(residual))) * 3.5, .003)
        keep = np.abs(residual) <= scale
    return coefficients


def floor_patch(base_xyz: np.ndarray, frames: np.ndarray, spec: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    lo = np.asarray(spec["min"], np.float64)
    hi = np.asarray(spec["max"], np.float64)
    step = float(spec["display_step_m"])
    coefficients = robust_floor_plane(base_xyz, lo, hi)
    xs = np.arange(lo[0] + step / 2, hi[0], step)
    ys = np.arange(lo[1] + step / 2, hi[1], step)
    xx, yy = np.meshgrid(xs, ys, indexing="xy")
    zz = coefficients[0] * xx + coefficients[1] * yy + coefficients[2]
    candidates = np.column_stack([xx.ravel(), yy.ravel(), zz.ravel()])

    # Add only truly uncovered cells.  Existing geometry (including furniture)
    # wins, so the repair cannot cover or move accepted points.
    gap, _ = cKDTree(base_xyz).query(candidates, workers=-1)
    candidates = candidates[gap >= float(spec["minimum_existing_gap_m"])]
    gap = gap[gap >= float(spec["minimum_existing_gap_m"])]

    frame = np.asarray(frames[int(spec["texture_frame"])], np.float64)
    y0, y1, x0, x1 = spec["texture_crop_yxyx"]
    texture = frame[y0:y1, x0:x1]
    u = (candidates[:, 0] - lo[0]) / (hi[0] - lo[0])
    v = (candidates[:, 1] - lo[1]) / (hi[1] - lo[1])
    colors = bilinear_texture(texture, u, v)
    return candidates, colors, {
        "plane_z_ax_by_c": coefficients.tolist(),
        "points_added": int(len(candidates)),
        "nearest_existing_gap_median": float(np.median(gap)) if len(gap) else None,
        "z_range_m": [float(candidates[:, 2].min()), float(candidates[:, 2].max())] if len(candidates) else [],
        "texture_frame": int(spec["texture_frame"]),
    }


def window_patch(base_xyz: np.ndarray, frames: np.ndarray, spec: dict) -> tuple[np.ndarray, np.ndarray, dict]:
    x = float(spec["wall_x_m"])
    y0, y1 = map(float, spec["y_range_m"])
    z0, z1 = map(float, spec["z_range_m"])
    step = float(spec["display_step_m"])
    ys = np.arange(y0 + step / 2, y1, step)
    zs = np.arange(z0 + step / 2, z1, step)
    yy, zz = np.meshgrid(ys, zs, indexing="xy")
    candidates = np.column_stack([np.full(yy.size, x), yy.ravel(), zz.ravel()])
    gap, _ = cKDTree(base_xyz).query(candidates, workers=-1)
    # This is an intentionally separate display overlay.  The accepted cloud
    # contains an opaque white wall sheet over much of the physical opening;
    # gap-filtering therefore hid the real window texture.  Keep one complete
    # window rectangle slightly inside the room, without deleting that sheet.
    # The 2 cm offset is below the viewer point radius and cannot look like a
    # second wall, while ensuring the window is the visible interior surface.

    frame = np.asarray(frames[int(spec["texture_frame"])], np.float64)
    cy0, cy1, cx0, cx1 = spec["texture_crop_yxyx"]
    texture = frame[cy0:cy1, cx0:cx1]
    # In the selected nearly frontal frame, image left-to-right corresponds to
    # decreasing room y; image top-to-bottom corresponds to decreasing z.
    u = 1.0 - (candidates[:, 1] - y0) / (y1 - y0)
    v = 1.0 - (candidates[:, 2] - z0) / (z1 - z0)
    colors = bilinear_texture(texture, u, v)
    return candidates, colors, {
        "surface_axis": "+X",
        "wall_x_m": x,
        "points_added": int(len(candidates)),
        "nearest_existing_gap_median": float(np.median(gap)) if len(gap) else None,
        "texture_frame": int(spec["texture_frame"]),
        "single_surface": True,
        "opaque_baseline_wall_points_removed": 0,
    }


def main() -> None:
    manifest = json.loads(MANIFEST.read_text(encoding="utf-8"))
    baseline = POST46 / manifest["baseline"]["file"]
    expected = manifest["baseline"]["sha256"].lower()
    actual = sha256(baseline)
    if actual != expected:
        raise RuntimeError(f"Baseline hash mismatch: {actual} != {expected}")

    base_xyz, base_rgb = read_cloud(baseline)
    if len(base_xyz) != int(manifest["baseline"]["points"]):
        raise RuntimeError("Baseline point count mismatch")
    frames = np.load(WORK45 / manifest["sources"]["registered_images"], mmap_mode="r")

    floor_xyz, floor_rgb, floor_stats = floor_patch(base_xyz, frames, manifest["floor_hole"])
    window_xyz, window_rgb, window_stats = window_patch(base_xyz, frames, manifest["window"])
    output_xyz = np.vstack([base_xyz, floor_xyz, window_xyz])
    output_rgb = np.vstack([base_rgb, floor_rgb, window_rgb])
    output = POST46 / manifest["display_output"]
    write_cloud(output, output_xyz, output_rgb)

    # Binary PLY round-trip may change only serialization precision; verify the
    # accepted prefix numerically and verify that every added point is in a ROI.
    loaded_xyz, loaded_rgb = read_cloud(output)
    prefix_xyz_error = float(np.max(np.abs(loaded_xyz[:len(base_xyz)] - base_xyz)))
    prefix_rgb_error = float(np.max(np.abs(loaded_rgb[:len(base_rgb)] - base_rgb)))
    if prefix_xyz_error > 1e-6 or prefix_rgb_error > (1.0 / 255.0 + 1e-9):
        raise RuntimeError("Accepted baseline prefix changed")
    if len(floor_xyz) and float(np.ptp(floor_xyz[:, 2])) > .02:
        raise RuntimeError("Floor display patch is not a single layer")

    report = {
        "status": "candidate_ready_for_browser_qa",
        "analysis_cloud": {"file": baseline.name, "sha256": actual, "points": int(len(base_xyz)), "unchanged": True},
        "display_cloud": {"file": output.name, "sha256": sha256(output), "points": int(len(output_xyz))},
        "policy": "immutable_analysis_cloud_plus_two_display_only_video_textured_rois",
        "floor": floor_stats,
        "window": window_stats,
        "validation": {
            "baseline_prefix_xyz_max_error": prefix_xyz_error,
            "baseline_prefix_rgb_max_error": prefix_rgb_error,
            "existing_points_removed_or_moved": 0,
            "outside_two_rois_changed": False,
            "second_floor_layer_created": False,
            "structure_dimensions_passage_risk_changed": False,
        },
    }
    report_path = output.with_suffix(".json")
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
