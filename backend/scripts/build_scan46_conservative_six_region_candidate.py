"""Build a conservative scan-46 preview candidate from the accepted clear cloud.

The pass is intentionally additive.  It never removes or moves accepted points.
Only missing samples on the measured room floor and three outer walls are added;
the physical window and door openings are preserved.  Colours are copied from
nearby real reconstructed/video-coloured samples on the same physical surface.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree


def fit_axis_plane(xyz: np.ndarray, axis: int, expected: float, band: float = .10) -> float:
    values = xyz[:, axis]
    near = values[np.abs(values - expected) <= band]
    if len(near) < 1000:
        raise RuntimeError(f"not enough support for axis={axis} expected={expected}")
    # The median is deliberately used instead of RANSAC: the narrow outer-wall
    # band prevents curtains/furniture from becoming the selected room plane.
    return float(np.median(near))


def grid_floor(x0: float, x1: float, y0: float, y1: float, z: float, step: float) -> np.ndarray:
    xx, yy = np.meshgrid(np.arange(x0, x1 + step * .5, step),
                         np.arange(y0, y1 + step * .5, step), indexing="xy")
    return np.column_stack([xx.ravel(), yy.ravel(), np.full(xx.size, z)])


def grid_wall(axis: int, fixed: float, a0: float, a1: float,
              z0: float, z1: float, step: float) -> np.ndarray:
    aa, zz = np.meshgrid(np.arange(a0, a1 + step * .5, step),
                         np.arange(z0, z1 + step * .5, step), indexing="xy")
    out = np.zeros((aa.size, 3), np.float64)
    varying = 1 - axis
    out[:, axis] = fixed
    out[:, varying] = aa.ravel()
    out[:, 2] = zz.ravel()
    return out


def nearest_surface_colours(query: np.ndarray, source_xyz: np.ndarray,
                            source_rgb: np.ndarray, axes: tuple[int, int],
                            max_gap: float = .38) -> tuple[np.ndarray, np.ndarray]:
    tree = cKDTree(source_xyz[:, axes])
    distance, ids = tree.query(query[:, axes], k=4, workers=-1)
    distance = np.atleast_2d(distance)
    ids = np.atleast_2d(ids)
    weights = 1.0 / np.maximum(distance, .012)
    colours = np.sum(source_rgb[ids] * weights[..., None], axis=1) / np.sum(weights, axis=1)[:, None]
    keep = np.min(distance, axis=1) <= max_gap
    return colours, keep


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--step", type=float, default=.012)
    parser.add_argument("--min-gap", type=float, default=.026)
    args = parser.parse_args()

    cloud = o3d.io.read_point_cloud(str(args.baseline))
    xyz = np.asarray(cloud.points, np.float64)
    rgb = np.asarray(cloud.colors, np.float64)

    # Stable exterior shell locations measured from the accepted scan.  The
    # window exterior is y~=1.32; y~=0.84 is curtain/furniture and must never be
    # used as the wall (the previous regression did exactly that).
    x_min = fit_axis_plane(xyz[(xyz[:, 2] > .45) & (xyz[:, 2] < 2.45)], 0, -.48)
    x_max = fit_axis_plane(xyz[(xyz[:, 2] > .45) & (xyz[:, 2] < 2.45)], 0, 1.94)
    y_max = fit_axis_plane(xyz[(xyz[:, 2] > .45) & (xyz[:, 2] < 2.45)], 1, 1.32)

    # The accepted floor has two layers in a few occluded places.  Use the
    # lowest strong horizontal layer so the completion cannot float over beds.
    floor_support = xyz[(xyz[:, 0] > -.46) & (xyz[:, 0] < 1.92)
                        & (xyz[:, 1] > -.48) & (xyz[:, 1] < 1.28)
                        & (xyz[:, 2] > -.06) & (xyz[:, 2] < .08)]
    floor_z = float(np.median(floor_support[:, 2]))

    candidates: list[tuple[str, np.ndarray, np.ndarray, tuple[int, int]]] = []
    floor = grid_floor(-.46, 1.92, -.48, 1.28, floor_z, args.step)
    floor_src = (np.abs(xyz[:, 2] - floor_z) < .045)
    candidates.append(("floor", floor, floor_src, (0, 1)))

    # Three reviewed walls.  The already-clear fourth wall is deliberately not
    # touched.  Window opening remains open; its existing real points stay.
    win_wall = grid_wall(1, y_max, -.46, 1.92, .08, 2.42, args.step)
    window_opening = ((win_wall[:, 0] >= -.18) & (win_wall[:, 0] <= 1.48)
                      & (win_wall[:, 2] >= .82) & (win_wall[:, 2] <= 2.30))
    win_wall = win_wall[~window_opening]
    win_src = (np.abs(xyz[:, 1] - y_max) < .055)
    candidates.append(("window_wall_surround", win_wall, win_src, (0, 2)))

    desk_wall = grid_wall(0, x_max, -.48, 1.28, .08, 2.42, args.step)
    desk_src = (np.abs(xyz[:, 0] - x_max) < .055)
    candidates.append(("desk_wall", desk_wall, desk_src, (1, 2)))

    left_wall = grid_wall(0, x_min, -.48, 1.28, .08, 2.42, args.step)
    left_src = (np.abs(xyz[:, 0] - x_min) < .055)
    candidates.append(("bookshelf_wall", left_wall, left_src, (1, 2)))

    existing = cKDTree(xyz)
    add_xyz_parts: list[np.ndarray] = []
    add_rgb_parts: list[np.ndarray] = []
    counts: dict[str, int] = {}
    for name, points, source_mask, axes in candidates:
        gap, _ = existing.query(points, workers=-1)
        missing = gap >= args.min_gap
        points = points[missing]
        source_xyz, source_rgb = xyz[source_mask], rgb[source_mask]
        if len(source_xyz) < 100:
            raise RuntimeError(f"insufficient real colour support for {name}")
        colours, supported = nearest_surface_colours(points, source_xyz, source_rgb, axes)
        points, colours = points[supported], colours[supported]
        add_xyz_parts.append(points)
        add_rgb_parts.append(np.clip(colours, 0, 1))
        counts[name] = int(len(points))

    add_xyz = np.vstack(add_xyz_parts)
    add_rgb = np.vstack(add_rgb_parts)
    result = o3d.geometry.PointCloud()
    result.points = o3d.utility.Vector3dVector(np.vstack([xyz, add_xyz]))
    result.colors = o3d.utility.Vector3dVector(np.vstack([rgb, add_rgb]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(args.output), result, write_ascii=False, compressed=False)
    report = {
        "status": "candidate_only_not_promoted",
        "method": "accepted_v4_plus_real_surface_local_completion",
        "baseline_points_unchanged": int(len(xyz)),
        "deleted_or_moved_baseline_points": 0,
        "added_points": int(len(add_xyz)),
        "output_points": int(len(xyz) + len(add_xyz)),
        "point_size_qa_fraction": .60,
        "planes": {"x_min": x_min, "x_max": x_max, "window_wall_y": y_max, "floor_z": floor_z},
        "additions": counts,
        "window_opening_preserved": True,
    }
    args.output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
