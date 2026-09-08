"""Fill scan-46 reviewed holes on planes fitted in the point-cloud frame.

Unlike the legacy pass, this does not assume that structure.json room bounds
share the raw aligned cloud extents.  Floor and both reviewed wall planes are
estimated from registered original-video observations, and only genuinely
missing samples are appended.  Existing clear points are never replaced.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree

from complete_scan46_structural_surfaces_from_video import sample_video_colours


def metric(raw: np.ndarray, alignment: dict) -> np.ndarray:
    xyz = np.asarray(raw, np.float64) @ np.asarray(alignment["alignment"]["rotation"], float).T
    xyz[..., 2] -= float(alignment["alignment"]["floor_z_raw_aligned"])
    xyz *= float(alignment["scale"]["applied"])
    return xyz


def fitted_plane(points: np.ndarray, dominant_axis: int, threshold: float = .022,
                 min_alignment: float = .82) -> np.ndarray:
    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(points)
    cloud = cloud.voxel_down_sample(.018)
    candidates: list[tuple[int, np.ndarray]] = []
    remaining = cloud
    for _ in range(8):
        if len(remaining.points) < 500:
            break
        model, ids = remaining.segment_plane(threshold, 3, 1600)
        normal = np.asarray(model[:3], float)
        # A large furniture face can contain more inliers than the room wall.
        # Require the reviewed room surfaces to be close to their Manhattan
        # axis instead of silently selecting a slanted cabinet/bed face.
        if abs(normal[dominant_axis]) >= min_alignment:
            candidates.append((len(ids), np.asarray(model, float)))
        remaining = remaining.select_by_index(ids, invert=True)
    if not candidates:
        raise RuntimeError(f"No plane found for dominant axis {dominant_axis}")
    return max(candidates, key=lambda item: item[0])[1]


def registered_points(work: Path, alignment: dict, frame_ids: range,
                      z_range: tuple[float, float]) -> np.ndarray:
    preds = work / "slam3r/scene/preds"
    if not (preds / "registered_pcds.npy").exists():
        # Portable scan-46 packages keep the registered maps at the work root.
        preds = work
    maps = np.load(preds / "registered_pcds.npy", mmap_mode="r")
    confs = np.load(preds / "registered_confs.npy", mmap_mode="r")
    chunks = []
    for frame_id in frame_ids:
        xyz = np.asarray(maps[frame_id]).reshape(-1, 3)
        conf = np.asarray(confs[frame_id]).reshape(-1)
        keep = np.isfinite(xyz).all(axis=1) & np.isfinite(conf) & (conf >= 1.0)
        xyz = metric(xyz[keep], alignment)
        xyz = xyz[(xyz[:, 2] >= z_range[0]) & (xyz[:, 2] <= z_range[1])]
        chunks.append(xyz[::3])
    return np.vstack(chunks)


def plane_grid(model: np.ndarray, fixed_axis: int, axis_a: int,
               range_a: tuple[float, float], axis_b: int,
               range_b: tuple[float, float], step: float) -> tuple[np.ndarray, np.ndarray]:
    a = np.arange(range_a[0], range_a[1] + step * .5, step)
    b = np.arange(range_b[0], range_b[1] + step * .5, step)
    aa, bb = np.meshgrid(a, b, indexing="xy")
    xyz = np.zeros((aa.size, 3), np.float64)
    xyz[:, axis_a] = aa.ravel()
    xyz[:, axis_b] = bb.ravel()
    normal, d = np.asarray(model[:3]), float(model[3])
    xyz[:, fixed_axis] = -(d + normal[axis_a] * xyz[:, axis_a] + normal[axis_b] * xyz[:, axis_b]) / normal[fixed_axis]
    normals = np.repeat((normal / np.linalg.norm(normal))[None], len(xyz), axis=0)
    return xyz, normals


def video_colour_with_local_completion(points: np.ndarray, normals: np.ndarray,
                                       labels: np.ndarray, work: Path,
                                       alignment: dict, min_views: int,
                                       max_radius: float = .32) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Colour reviewed surface samples without leaving visible checkerboard holes.

    Directly observed samples always keep their real registered-video colour.
    A sample missed only because of projection/occlusion discretisation may use
    the nearest directly observed colour on the *same physical surface*.  The
    radius is deliberately local, so colours never bleed between floor, walls
    and the bed, and no constant/white synthetic patch is introduced.
    """
    rgb, views, score = sample_video_colours(points, normals, work, alignment)
    # A projected pixel can technically have a view while still being the
    # black background of a misregistered depth map.  Treating that as a real
    # colour recreates the reviewed black holes.  Only photometrically valid
    # observations seed completion; this test applies solely to newly fitted
    # room surfaces and never recolours existing furniture.
    luminance = rgb @ np.asarray([.2126, .7152, .0722])
    direct = (views >= min_views) & np.isfinite(score) & (luminance >= .10)
    completed = direct.copy()
    for label in np.unique(labels):
        ids = np.flatnonzero(labels == label)
        observed = ids[direct[ids]]
        missing = ids[~direct[ids]]
        if len(observed) == 0 or len(missing) == 0:
            continue
        neighbour_count = min(4, len(observed))
        distances, nearest = cKDTree(points[observed]).query(
            points[missing], k=neighbour_count, workers=-1)
        if neighbour_count == 1:
            distances = distances[:, None]
            nearest = nearest[:, None]
        fill = distances[:, 0] <= max_radius
        targets = missing[fill]
        d = np.maximum(distances[fill], 1e-4)
        weights = 1.0 / d
        colours = rgb[observed[nearest[fill]]]
        rgb[targets] = np.sum(colours * weights[..., None], axis=1) / np.sum(weights, axis=1)[:, None]
        completed[targets] = True
    return rgb, direct, completed


def plane_distance(points: np.ndarray, model: np.ndarray) -> np.ndarray:
    normal = np.asarray(model[:3], np.float64)
    return np.abs(points @ normal + float(model[3])) / np.linalg.norm(normal)


def reviewed_dark_surface_mask(points: np.ndarray, colours: np.ndarray,
                               floor_model: np.ndarray,
                               window_model: np.ndarray,
                               desk_model: np.ndarray) -> np.ndarray:
    """Remove only dark invalid samples that occupy the six reviewed surfaces.

    These samples previously blocked the fitted video-coloured samples during
    the nearest-point gap test.  The bounds deliberately match only the room
    floor and the two reviewed walls; dark furniture and all other clear scene
    geometry are untouched.
    """
    luminance = colours @ np.asarray([.2126, .7152, .0722])
    dark = luminance < .075
    floor = (
        (plane_distance(points, floor_model) <= .035)
        & (points[:, 0] >= -.52) & (points[:, 0] <= 2.04)
        & (points[:, 1] >= -.88) & (points[:, 1] <= 1.50)
    )
    window = (
        (plane_distance(points, window_model) <= .035)
        & (points[:, 0] >= -.52) & (points[:, 0] <= 2.04)
        & (points[:, 2] >= .20) & (points[:, 2] <= 2.55)
    )
    desk = (
        (plane_distance(points, desk_model) <= .035)
        & (points[:, 1] >= -.88) & (points[:, 1] <= 1.50)
        & (points[:, 2] >= .20) & (points[:, 2] <= 2.55)
    )
    return dark & (floor | window | desk)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("baseline", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--step", type=float, default=.009)
    parser.add_argument("--min-gap", type=float, default=.012)
    parser.add_argument("--min-views", type=int, default=1)
    parser.add_argument("--structure", type=Path, default=None,
                        help="Optional accepted structure.json when video observations live in another scan work dir")
    args = parser.parse_args()

    # Keep the accepted room planes reproducible across verification runs.
    o3d.utility.random.seed(46)

    alignment = json.loads((args.work / "postprocess/alignment.json").read_text(encoding="utf-8"))
    base = o3d.io.read_point_cloud(str(args.baseline))
    base_xyz, base_rgb = np.asarray(base.points), np.asarray(base.colors)
    # These ranges are read from the indexed 900-frame audit. They contain
    # repeated, clear views of the corresponding real surfaces.
    window_obs = registered_points(args.work, alignment, range(130, 161), (.45, 2.42))
    desk_obs = registered_points(args.work, alignment, range(450, 580), (.42, 2.45))
    # SLAM3R's per-view map occasionally collapses an oblique floor view onto
    # a vertical plane.  The accepted aggregate cloud has many cross-view
    # observations, so it is the reliable source for the floor equation.
    floor_obs = base_xyz[(base_xyz[:, 2] >= .12) & (base_xyz[:, 2] <= .58)]
    floor_model = fitted_plane(floor_obs, 2, .026)
    window_wall_model = fitted_plane(window_obs, 1, min_alignment=.96)
    desk_wall_model = fitted_plane(desk_obs, 0, min_alignment=.96)

    # The floor continues through the doorway-side strip to the measured room
    # envelope (y≈1.35).  Limiting it to the curtain/window plane at y≈.84 was
    # the reason the reviewed entrance-floor hole survived earlier passes.
    floor_xyz, floor_n = plane_grid(floor_model, 2, 0, (-.52, 2.04), 1, (-.88, 1.50), args.step)
    window_xyz, window_n = plane_grid(window_wall_model, 1, 0, (-.52, 2.04), 2, (.20, 2.55), args.step)
    desk_xyz, desk_n = plane_grid(desk_wall_model, 0, 1, (-.88, 1.50), 2, (.20, 2.55), args.step)


    # A window is not empty space in the visual model: glass/curtain pixels are
    # visible in the source video.  Keep the full fitted surface and require
    # every sample (including the window rectangle) to receive real registered
    # video colour below.  Leaving this rectangle empty caused a persistent
    # black 'missing wall' in the viewer.
    candidates = np.vstack([floor_xyz, window_xyz, desk_xyz])
    normals = np.vstack([floor_n, window_n, desk_n])
    labels = np.concatenate([
        np.repeat("floor", len(floor_xyz)),
        np.repeat("window_wall_surround", len(window_xyz)),
        np.repeat("desk_wall", len(desk_xyz)),
    ])

    invalid_dark = reviewed_dark_surface_mask(
        base_xyz, base_rgb, floor_model, window_wall_model, desk_wall_model)
    clean_base_xyz = base_xyz[~invalid_dark]
    clean_base_rgb = base_rgb[~invalid_dark]
    gap, _ = cKDTree(clean_base_xyz).query(candidates, workers=-1)
    missing = gap >= args.min_gap
    candidates, normals, labels = candidates[missing], normals[missing], labels[missing]
    rgb, direct, keep = video_colour_with_local_completion(
        candidates, normals, labels, args.work, alignment, args.min_views)
    add_xyz, add_rgb, add_labels = candidates[keep], rgb[keep], labels[keep]

    result = o3d.geometry.PointCloud()
    result.points = o3d.utility.Vector3dVector(np.vstack([clean_base_xyz, add_xyz]))
    result.colors = o3d.utility.Vector3dVector(np.vstack([clean_base_rgb, add_rgb]))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(args.output), result, write_ascii=False, compressed=False)
    report = {
        "method": "registered_video_fitted_floor_and_two_wall_planes",
        "baseline_points": int(len(base_xyz)),
        "real_video_supported_additions": int(len(add_xyz)),
        "direct_video_colour_additions": int(np.sum(direct)),
        "local_same_surface_colour_completions": int(np.sum(keep & ~direct)),
        "removed_dark_invalid_surface_points": int(np.sum(invalid_dark)),
        "output_points": int(len(clean_base_xyz) + len(add_xyz)),
        "existing_clear_points_replaced": 0,
        "point_size_qa_fraction": .60,
        "planes": {
            "floor": floor_model.tolist(),
            "window_wall": window_wall_model.tolist(),
            "desk_wall": desk_wall_model.tolist(),
        },
        "additions": {name: int(np.sum(add_labels == name)) for name in np.unique(labels)},
    }
    args.output.with_suffix(".json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
