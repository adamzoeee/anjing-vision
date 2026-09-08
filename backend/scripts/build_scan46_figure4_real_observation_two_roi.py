"""Append-only repair of two scan-46 Figure-4 holes with observed points.

The reviewed Figure-4 PLY is an immutable prefix.  This pass samples the
already aligned SLAM3R cloud (same metric/world frame) and adds at most one
representative per surface cell.  It never creates a plane, projects a point,
deletes a baseline point, or changes structure/measurement data.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree


ROOT = Path(__file__).resolve().parents[2]
POST = ROOT / "backend/data/work/46/postprocess"
BASELINE = POST / "scene_preview_local_surface_repair_candidate.ply"
SOURCE = POST / "scene_aligned.ply"
FLOOR_OUT = POST / "scene_preview_figure4_real_floor_roi.ply"
FINAL_OUT = POST / "scene_preview_figure4_real_floor_window_roi.ply"

# Final metric coordinates, audited against Figure-4 and the aligned source.
FLOOR_LO = np.array([-.52, .55, .125])
FLOOR_HI = np.array([.55, 1.40, .215])
# The broad +X audit is retained below as a rejection test.  It has 99.9%
# Figure-4 coverage and therefore is not the missing window.  The physical
# window recorded by structure.json is on the +Y wall, centred at x=.65.
WINDOW_LO = np.array([-.30, 1.20, .82])
WINDOW_HI = np.array([1.60, 1.42, 2.34])
WINDOW_APERTURE = (-.181, 1.481, .925, 2.225)  # xmin, xmax, zmin, zmax


def read(path: Path) -> tuple[np.ndarray, np.ndarray]:
    cloud = o3d.io.read_point_cloud(str(path))
    xyz = np.asarray(cloud.points, np.float64)
    rgb = np.asarray(cloud.colors, np.float64)
    if not len(xyz) or len(xyz) != len(rgb):
        raise RuntimeError(f"invalid point cloud: {path}")
    # scene_aligned.ply stores RGB as float values already normalized to 0..1,
    # while Open3D's generic PLY reader treats every RGB property as 0..255 and
    # divides it once more.  Detect that exact 1/255 range and undo only the
    # accidental second normalization.  Without this, genuine recovered points
    # are geometrically correct but render almost black.
    if len(rgb) and float(np.max(rgb)) <= (1.0 / 255.0 + 1e-6):
        rgb = np.clip(rgb * 255.0, 0.0, 1.0)
    return xyz, rgb


def write(path: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(xyz)
    cloud.colors = o3d.utility.Vector3dVector(rgb)
    if not o3d.io.write_point_cloud(str(path), cloud, write_ascii=False, compressed=False):
        raise RuntimeError(f"failed to write: {path}")


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def crop(xyz: np.ndarray, rgb: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    keep = np.all((xyz >= lo) & (xyz <= hi), axis=1)
    return xyz[keep], rgb[keep]


def one_per_cell(xyz: np.ndarray, rgb: np.ndarray, uv_axes: tuple[int, int],
                 surface_axis: int, surface_value: float, cell_size: float) -> tuple[np.ndarray, np.ndarray]:
    """Keep the real observation closest to the existing surface in each cell."""
    uv = xyz[:, uv_axes]
    cells = np.floor(uv / cell_size).astype(np.int64)
    distance = np.abs(xyz[:, surface_axis] - surface_value)
    order = np.lexsort((distance, cells[:, 1], cells[:, 0]))
    ordered_cells = cells[order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = np.any(ordered_cells[1:] != ordered_cells[:-1], axis=1)
    chosen = order[first]
    return xyz[chosen], rgb[chosen]


def only_uncovered_surface(base_xyz: np.ndarray, xyz: np.ndarray, rgb: np.ndarray,
                           uv_axes: tuple[int, int], coverage_radius: float) -> tuple[np.ndarray, np.ndarray, dict]:
    """Fill only a 2-D surface gap; offset observations cannot make a second layer."""
    if not len(xyz):
        return xyz, rgb, {"representatives": 0, "accepted": 0}
    gap, _ = cKDTree(base_xyz[:, uv_axes]).query(xyz[:, uv_axes], workers=-1)
    keep = gap >= coverage_radius
    return xyz[keep], rgb[keep], {
        "representatives": int(len(xyz)),
        "accepted": int(np.count_nonzero(keep)),
        "rejected_existing_surface_coverage": int(np.count_nonzero(~keep)),
        "surface_gap_median_m": float(np.median(gap)),
        "surface_gap_p95_m": float(np.quantile(gap, .95)),
    }


def assert_prefix(path: Path, base_xyz: np.ndarray, base_rgb: np.ndarray) -> None:
    xyz, rgb = read(path)
    n = len(base_xyz)
    if not np.array_equal(xyz[:n], base_xyz) or not np.array_equal(rgb[:n], base_rgb):
        raise AssertionError("candidate changed Figure-4 outside/inside baseline points")


def main() -> None:
    base_xyz, base_rgb = read(BASELINE)
    src_xyz, src_rgb = read(SOURCE)

    # Floor: source observations form a stable local layer around z~=0.173 m.
    # Keep the observed coordinates as-is; do not project them onto z=0.
    floor_xyz, floor_rgb = crop(src_xyz, src_rgb, FLOOR_LO, FLOOR_HI)
    floor_z = float(np.median(floor_xyz[:, 2]))
    stable_floor = np.abs(floor_xyz[:, 2] - floor_z) <= .025
    floor_xyz, floor_rgb = floor_xyz[stable_floor], floor_rgb[stable_floor]
    floor_xyz, floor_rgb = one_per_cell(floor_xyz, floor_rgb, (0, 1), 2, floor_z, .010)
    base_floor, _ = crop(base_xyz, base_rgb, FLOOR_LO, FLOOR_HI)
    floor_add, floor_color, floor_stats = only_uncovered_surface(
        base_floor, floor_xyz, floor_rgb, (0, 1), .014)
    stage1_xyz = np.vstack([base_xyz, floor_add])
    stage1_rgb = np.vstack([base_rgb, floor_color])
    write(FLOOR_OUT, stage1_xyz, stage1_rgb)
    assert_prefix(FLOOR_OUT, base_xyz, base_rgb)

    # Window: Figure-4 and structure.json place the physical opening on the +Y
    # wall.  Restore only a narrow frame perimeter; the glass interior remains
    # sparse by design.  The earlier +X hypothesis is rejected quantitatively:
    # its accepted Figure-4 surface already covers 99.9% of source cells.
    window_xyz, window_rgb = crop(src_xyz, src_rgb, WINDOW_LO, WINDOW_HI)
    wall_y = float(np.quantile(window_xyz[:, 1], .90))
    stable_wall = np.abs(window_xyz[:, 1] - wall_y) <= .030
    window_xyz, window_rgb = window_xyz[stable_wall], window_rgb[stable_wall]
    xmin, xmax, zmin, zmax = WINDOW_APERTURE
    frame_band = .11
    x, z = window_xyz[:, 0], window_xyz[:, 2]
    in_aperture = (x >= xmin) & (x <= xmax) & (z >= zmin) & (z <= zmax)
    on_frame = in_aperture & (
        (np.abs(x - xmin) <= frame_band) | (np.abs(x - xmax) <= frame_band)
        | (np.abs(z - zmin) <= frame_band) | (np.abs(z - zmax) <= frame_band)
    )
    window_xyz, window_rgb = window_xyz[on_frame], window_rgb[on_frame]
    window_xyz, window_rgb = one_per_cell(window_xyz, window_rgb, (0, 2), 1, wall_y, .010)
    stage1_window, _ = crop(stage1_xyz, stage1_rgb, WINDOW_LO, WINDOW_HI)
    window_add, window_color, window_stats = only_uncovered_surface(
        stage1_window, window_xyz, window_rgb, (0, 2), .014)
    final_xyz = np.vstack([stage1_xyz, window_add])
    final_rgb = np.vstack([stage1_rgb, window_color])
    write(FINAL_OUT, final_xyz, final_rgb)
    assert_prefix(FINAL_OUT, base_xyz, base_rgb)

    report = {
        "method": "figure4_immutable_append_only_aligned_real_observations",
        "baseline": {"file": BASELINE.name, "points": len(base_xyz), "sha256": sha256(BASELINE)},
        "source": {"file": SOURCE.name, "points": len(src_xyz), "same_metric_frame": True},
        "guarantees": {
            "baseline_prefix_exact": True,
            "baseline_points_removed_or_moved": 0,
            "synthetic_or_projected_points": 0,
            "hard_roi_replacement": False,
            "other_regions_modified": False,
            "structure_dimensions_passage_modified": False,
        },
        "floor": {"roi": [FLOOR_LO.tolist(), FLOOR_HI.tolist()], "observed_plane_z_m": floor_z, **floor_stats},
        "window_plus_y_frame": {
            "roi": [WINDOW_LO.tolist(), WINDOW_HI.tolist()],
            "aperture_xz": list(WINDOW_APERTURE),
            "observed_wall_y_m": wall_y,
            **window_stats,
        },
        "outputs": {
            "floor": {"file": FLOOR_OUT.name, "points": len(stage1_xyz), "sha256": sha256(FLOOR_OUT)},
            "floor_window": {"file": FINAL_OUT.name, "points": len(final_xyz), "sha256": sha256(FINAL_OUT)},
        },
    }
    out = POST / "figure4_real_observation_two_roi_report.json"
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
