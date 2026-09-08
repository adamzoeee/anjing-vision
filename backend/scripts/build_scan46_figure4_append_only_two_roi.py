"""Build conservative scan-46 ROI candidates without changing Figure-4.

The user-selected Figure-4 cloud is an immutable prefix.  This script only
reuses strict multi-frame consensus observations produced from the original
registered SLAM3R maps.  It does not synthesize planes, delete baseline points,
move points, or consume independently reconstructed supplemental videos.
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
OLD_FLOOR = POST / "scene_preview_figure2_plus_real_floor_consensus_candidate.ply"
OLD_BOTH = POST / "scene_preview_figure2_plus_real_floor_window_consensus_candidate.ply"

# Counts are recorded by the earlier strict-consensus audit.  Only these
# appended tails came from registered maps; the leading portion was a baseline.
FLOOR_TAIL = 1_192
WINDOW_TAIL = 24_541

FLOOR_OUTPUT = POST / "scene_preview_figure4_plus_real_floor_append_only.ply"
BOTH_OUTPUT = POST / "scene_preview_figure4_plus_real_floor_window_append_only.ply"


def read(path: Path) -> tuple[np.ndarray, np.ndarray]:
    cloud = o3d.io.read_point_cloud(str(path))
    xyz = np.asarray(cloud.points, dtype=np.float64)
    rgb = np.asarray(cloud.colors, dtype=np.float64)
    if not len(xyz) or len(xyz) != len(rgb):
        raise RuntimeError(f"Invalid point cloud: {path}")
    return xyz, rgb


def write(path: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(xyz)
    cloud.colors = o3d.utility.Vector3dVector(rgb)
    if not o3d.io.write_point_cloud(str(path), cloud, write_ascii=False, compressed=False):
        raise RuntimeError(f"Failed to write {path}")


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def select_missing(
    baseline_xyz: np.ndarray,
    source_xyz: np.ndarray,
    source_rgb: np.ndarray,
    *,
    min_gap: float,
    max_gap: float,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | int]]:
    """Keep real consensus representatives that occupy a genuine baseline gap."""
    gap, _ = cKDTree(baseline_xyz).query(source_xyz, workers=-1)
    keep = (gap >= min_gap) & (gap <= max_gap)
    return source_xyz[keep], source_rgb[keep], {
        "source_points": int(len(source_xyz)),
        "accepted_points": int(np.count_nonzero(keep)),
        "rejected_too_close": int(np.count_nonzero(gap < min_gap)),
        "rejected_too_far": int(np.count_nonzero(gap > max_gap)),
        "gap_median_m": float(np.median(gap)) if len(gap) else 0.0,
        "gap_p95_m": float(np.quantile(gap, .95)) if len(gap) else 0.0,
    }


def select_window_surface_gaps(
    baseline_xyz: np.ndarray,
    source_xyz: np.ndarray,
    source_rgb: np.ndarray,
    *,
    surface_x: float,
    surface_tolerance: float = .024,
    yz_voxel: float = .012,
    coverage_radius: float = .014,
) -> tuple[np.ndarray, np.ndarray, dict[str, float | int]]:
    """Fill only uncovered Y/Z cells on the verified +X wall.

    A 3-D nearest-neighbour gap is deliberately not used here: it favours
    observations offset from the accepted wall and therefore creates a second
    thick layer.  Instead, baseline coverage is measured in wall coordinates
    (Y/Z), one real observation is kept per wall cell, and the representative
    closest to the accepted wall surface is selected.  No coordinate is moved
    or synthesised.
    """
    distance_to_surface = np.abs(source_xyz[:, 0] - surface_x)
    on_surface = distance_to_surface <= surface_tolerance
    xyz = source_xyz[on_surface]
    rgb = source_rgb[on_surface]
    distance_to_surface = distance_to_surface[on_surface]
    if not len(xyz):
        return xyz, rgb, {
            "source_points": int(len(source_xyz)),
            "surface_band_points": 0,
            "accepted_points": 0,
        }

    # Keep exactly one real observation for each Y/Z cell.  Sorting makes the
    # first record in a cell the observation closest to the accepted wall.
    cell = np.floor(xyz[:, 1:3] / yz_voxel).astype(np.int64)
    order = np.lexsort((distance_to_surface, cell[:, 1], cell[:, 0]))
    cell_ordered = cell[order]
    first = np.ones(len(order), dtype=bool)
    first[1:] = np.any(cell_ordered[1:] != cell_ordered[:-1], axis=1)
    representatives = order[first]
    xyz = xyz[representatives]
    rgb = rgb[representatives]

    # Only fill a wall-coordinate hole.  Points already covered in Y/Z are
    # discarded even if their X differs, preventing double walls/thick edges.
    baseline_band = np.abs(baseline_xyz[:, 0] - surface_x) <= .045
    baseline_yz = baseline_xyz[baseline_band, 1:3]
    if len(baseline_yz):
        yz_gap, _ = cKDTree(baseline_yz).query(xyz[:, 1:3], workers=-1)
        missing = yz_gap >= coverage_radius
    else:
        yz_gap = np.full(len(xyz), np.inf)
        missing = np.ones(len(xyz), dtype=bool)
    return xyz[missing], rgb[missing], {
        "source_points": int(len(source_xyz)),
        "surface_band_points": int(np.count_nonzero(on_surface)),
        "wall_cell_representatives": int(len(xyz)),
        "accepted_points": int(np.count_nonzero(missing)),
        "rejected_existing_yz_coverage": int(np.count_nonzero(~missing)),
        "surface_x_m": float(surface_x),
        "surface_tolerance_m": float(surface_tolerance),
        "yz_voxel_m": float(yz_voxel),
        "coverage_radius_m": float(coverage_radius),
        "yz_gap_median_m": float(np.median(yz_gap[np.isfinite(yz_gap)]))
        if np.any(np.isfinite(yz_gap)) else 0.0,
    }


def assert_prefix(path: Path, baseline_xyz: np.ndarray, baseline_rgb: np.ndarray) -> None:
    xyz, rgb = read(path)
    count = len(baseline_xyz)
    if len(xyz) < count:
        raise AssertionError("candidate is shorter than immutable baseline")
    if not np.array_equal(xyz[:count], baseline_xyz):
        raise AssertionError("candidate changed baseline point coordinates")
    if not np.array_equal(rgb[:count], baseline_rgb):
        raise AssertionError("candidate changed baseline point colors")


def main() -> None:
    base_xyz, base_rgb = read(BASELINE)
    old_floor_xyz, old_floor_rgb = read(OLD_FLOOR)
    old_both_xyz, old_both_rgb = read(OLD_BOTH)

    floor_xyz = old_floor_xyz[-FLOOR_TAIL:]
    floor_rgb = old_floor_rgb[-FLOOR_TAIL:]
    # Strict consensus points occupy a narrow real floor/threshold layer.
    # A 9 mm gap avoids duplicating the accepted baseline; 25 cm rejects any
    # accidental disconnected observation without manufacturing geometry.
    floor_add_xyz, floor_add_rgb, floor_stats = select_missing(
        base_xyz, floor_xyz, floor_rgb, min_gap=.009, max_gap=.25
    )
    stage1_xyz = np.vstack([base_xyz, floor_add_xyz])
    stage1_rgb = np.vstack([base_rgb, floor_add_rgb])
    write(FLOOR_OUTPUT, stage1_xyz, stage1_rgb)
    assert_prefix(FLOOR_OUTPUT, base_xyz, base_rgb)

    window_xyz = old_both_xyz[-WINDOW_TAIL:]
    window_rgb = old_both_rgb[-WINDOW_TAIL:]
    # Coordinate audit proves the window is the +X wall.  The accepted Figure-4
    # wall surface is x=1.9581354499810883 m.  Fill only uncovered Y/Z cells;
    # never retain an offset observation merely because it is far from the wall.
    verified_axis = (window_xyz[:, 0] >= 1.900) & (window_xyz[:, 0] <= 1.980)
    window_xyz, window_rgb = window_xyz[verified_axis], window_rgb[verified_axis]
    window_add_xyz, window_add_rgb, window_stats = select_window_surface_gaps(
        stage1_xyz,
        window_xyz,
        window_rgb,
        surface_x=1.9581354499810883,
    )
    final_xyz = np.vstack([stage1_xyz, window_add_xyz])
    final_rgb = np.vstack([stage1_rgb, window_add_rgb])
    write(BOTH_OUTPUT, final_xyz, final_rgb)
    assert_prefix(BOTH_OUTPUT, base_xyz, base_rgb)

    report = {
        "method": "figure4_immutable_prefix_append_only_real_consensus",
        "baseline": {
            "path": str(BASELINE.resolve()),
            "points": int(len(base_xyz)),
            "sha256": sha256(BASELINE),
        },
        "guarantees": {
            "baseline_prefix_coordinates_exact": True,
            "baseline_prefix_colors_exact": True,
            "baseline_points_removed": 0,
            "baseline_points_moved": 0,
            "synthetic_points": 0,
            "hard_roi_replacement": False,
            "supplemental_video_points_used": 0,
            "structure_measurement_passage_modified": False,
        },
        "floor": floor_stats,
        "window_plus_x_wall": window_stats,
        "outputs": {
            "floor_only": {
                "path": str(FLOOR_OUTPUT.resolve()),
                "points": int(len(stage1_xyz)),
                "sha256": sha256(FLOOR_OUTPUT),
            },
            "floor_and_window": {
                "path": str(BOTH_OUTPUT.resolve()),
                "points": int(len(final_xyz)),
                "sha256": sha256(BOTH_OUTPUT),
            },
        },
    }
    report_path = POST / "figure4_append_only_two_roi_report.json"
    report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
