"""Build a conservative Scan 46 point-cloud repair from traced observations.

The script intentionally does not infer walls or merge soft-object states.  It
removes only source-traced window-jamb samples that were assigned to the
curtain and adds only same-state R3/R5 bed-cover observations which pass a
fixed-view gap/occlusion guard.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from pathlib import Path

import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw
from scipy.spatial import cKDTree

from review_window_camera import render
from verify_september12_candidate import VIEWS, project


ROOT = Path(__file__).resolve().parents[1]
POST = ROOT / "data/work/46/postprocess"
RESEARCH = ROOT / "data/work/46/local_gaps_20260912"
DIAGNOSTIC = RESEARCH / "viewer_diagnostic"
BASELINE_NAME = "local_gaps_20260912.ply"
OUTPUT_NAME = "local_gaps_source_traced_repair.ply"
BASELINE_SHA256 = "8748b641065cae16f62f8ab9c6a56c03c3786a6f6b966b380481d9064990e4a9"


def traced_jamb_ids(lineage: np.lib.npyio.NpzFile) -> np.ndarray:
    """Return only exact source pixels lying inside the audited window jamb."""
    uv = np.asarray(lineage["source_uv"])
    exact = np.asarray(lineage["exact"], dtype=bool)
    jamb_right = 88.5 - (uv[:, 1] - 25.0) * 4.5 / 185.0
    mask = (
        exact
        & (uv[:, 1] >= 25.0)
        & (uv[:, 1] <= 190.0)
        & (uv[:, 0] < jamb_right - 0.5)
    )
    return np.unique(np.asarray(lineage["baseline_ids"], dtype=np.int64)[mask])


def depth_buffer(points: np.ndarray, view: tuple, radius: int = 2) -> np.ndarray:
    xy, depth, valid = project(points, view)
    xy, depth = xy[valid], depth[valid]
    result = np.full((896, 896), np.inf, dtype=np.float64)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            np.minimum.at(result, (xy[:, 1] + dy, xy[:, 0] + dx), depth)
    return result


def gap_occlusion_guard(
    points: np.ndarray,
    reference_points: np.ndarray,
    *,
    surface_tolerance_m: float = 0.001,
) -> tuple[np.ndarray, dict]:
    """Keep points visible in a gap and never substantially in front of truth."""
    gaps = np.zeros(len(points), dtype=np.int16)
    rejected = np.zeros(len(points), dtype=bool)
    per_view: dict[str, dict[str, int]] = {}
    for name, view in VIEWS.items():
        old = depth_buffer(reference_points, view)
        xy, depth, valid = project(points, view)
        ids = np.flatnonzero(valid)
        if not len(ids):
            per_view[name] = {"projects": 0, "gap": 0, "foreground_rejected": 0}
            continue
        x, y = xy[ids].T
        center = old[y, x]
        gaps[ids] += ~np.isfinite(center)
        view_rejected = np.zeros(len(ids), dtype=bool)
        for dy in range(-2, 3):
            for dx in range(-2, 3):
                surface = old[y + dy, x + dx]
                # Reject z-fighting as well as actual foreground occlusion.
                # A candidate may touch an occupied sprite only when it is
                # unambiguously behind the already accepted surface.
                view_rejected |= np.isfinite(surface) & (
                    depth[ids] <= surface + surface_tolerance_m
                )
        rejected[ids[view_rejected]] = True
        per_view[name] = {
            "projects": int(len(ids)),
            "gap": int((~np.isfinite(center)).sum()),
            "foreground_rejected": int(view_rejected.sum()),
        }
    keep = (gaps > 0) & ~rejected
    return keep, per_view


def sample_observed_mesh(
    positions: np.ndarray,
    uv: np.ndarray,
    faces: np.ndarray,
    *,
    spacing_m: float = 0.006,
) -> tuple[np.ndarray, np.ndarray]:
    """Sample only inside observed triangles, never across a missing cell."""
    sampled_points: list[np.ndarray] = []
    sampled_uv: list[np.ndarray] = []
    for face in faces:
        triangle = positions[face]
        triangle_uv = uv[face]
        edges = np.linalg.norm(triangle[[1, 2, 0]] - triangle[[0, 1, 2]], axis=1)
        divisions = max(1, int(np.ceil(edges.max() / spacing_m)))
        for i in range(divisions + 1):
            for j in range(divisions + 1 - i):
                weights = np.array(
                    [i, j, divisions - i - j], dtype=np.float64
                ) / divisions
                sampled_points.append(weights @ triangle)
                sampled_uv.append(weights @ triangle_uv)
    return np.asarray(sampled_points), np.asarray(sampled_uv)


def load_observed_vertices(name: str) -> tuple[np.ndarray, np.ndarray, dict]:
    patch = json.loads((DIAGNOSTIC / f"{name}_patch.json").read_text(encoding="utf-8"))
    audit = json.loads(
        (DIAGNOSTIC / f"{name}_patch_audit.json").read_text(encoding="utf-8")
    )
    points = np.asarray(patch["positions"], dtype=np.float64)
    uv = np.asarray(patch["uv"], dtype=np.float64)
    faces = np.asarray(patch["faces"], dtype=np.int64)
    points, uv = sample_observed_mesh(points, uv, faces)
    texture = np.asarray(Image.open(DIAGNOSTIC / "r3_texture.png").convert("RGB"))
    pixel = np.rint(uv).astype(np.int64)
    pixel[:, 0] = np.clip(pixel[:, 0], 0, texture.shape[1] - 1)
    pixel[:, 1] = np.clip(pixel[:, 1], 0, texture.shape[0] - 1)
    colors = texture[pixel[:, 1], pixel[:, 0]].astype(np.float64) / 255.0
    return points, colors, audit


def fixed_view_metrics(before: Image.Image, after: Image.Image) -> dict[str, int]:
    a, b = np.asarray(before), np.asarray(after)
    background = np.array([15, 19, 26], dtype=np.uint8)
    old_black = np.all(a == background, axis=-1)
    new_black = np.all(b == background, axis=-1)
    return {
        "filled_black_pixels": int(np.sum(old_black & ~new_black)),
        "new_black_pixels": int(np.sum(~old_black & new_black)),
        "changed_existing_pixels": int(np.sum(np.any(a != b, axis=-1) & ~old_black)),
    }


def build(output: Path, *, include_observed_r3: bool = False) -> dict:
    source = POST / BASELINE_NAME
    source_sha256 = hashlib.sha256(source.read_bytes()).hexdigest()
    if source_sha256 != BASELINE_SHA256:
        raise RuntimeError(
            f"accepted source drift: expected {BASELINE_SHA256}, got {source_sha256}"
        )
    cloud = o3d.io.read_point_cloud(str(source))
    baseline_points = np.asarray(cloud.points)
    baseline_colors = np.asarray(cloud.colors)
    lineage = np.load(DIAGNOSTIC / "curtain_exact_lineage.npz")
    remove_ids = traced_jamb_ids(lineage)
    if len(remove_ids) != 727:
        raise RuntimeError(f"source identity drift: expected 727 jamb points, got {len(remove_ids)}")
    if np.any(remove_ids < 0) or np.any(remove_ids >= len(baseline_points)):
        raise RuntimeError("source-traced removal ids are outside the accepted baseline")

    retained = np.ones(len(baseline_points), dtype=bool)
    retained[remove_ids] = False
    repair_points: list[np.ndarray] = []
    repair_colors: list[np.ndarray] = []
    component_audit: dict[str, dict] = {}
    reference_points = baseline_points
    tree = cKDTree(reference_points)
    # R3 is useful for research, but its point rendering is still visibly
    # incomplete.  Publication defaults to correction-only until that
    # surface passes final viewer QA.
    for name in (("r3",) if include_observed_r3 else ()):
        points, colors, source_audit = load_observed_vertices(name)
        gap_keep, view_audit = gap_occlusion_guard(points, reference_points)
        nonduplicate = tree.query(points, k=1)[0] > 0.004
        keep = gap_keep & nonduplicate
        repair_points.append(points[keep])
        repair_colors.append(colors[keep])
        component_audit[name] = {
            "source_frame": source_audit["source_frame"],
            "surface": source_audit["surface"],
            "coordinates_unchanged": bool(source_audit["coordinates_unchanged"]),
            "input_vertices": int(len(points)),
            "gap_occlusion_kept": int(gap_keep.sum()),
            "nonduplicate_kept": int(keep.sum()),
            "views": view_audit,
        }

    if repair_points:
        added_points = np.vstack(repair_points)
        added_colors = np.vstack(repair_colors)
        voxel = np.floor(added_points / 0.003).astype(np.int64)
        _, unique = np.unique(voxel, axis=0, return_index=True)
        unique.sort()
        added_points, added_colors = added_points[unique], added_colors[unique]
    else:
        added_points = np.empty((0, 3), dtype=np.float64)
        added_colors = np.empty((0, 3), dtype=np.float64)
    final_points = np.vstack((baseline_points[retained], added_points))
    final_colors = np.vstack((baseline_colors[retained], added_colors))

    result = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(final_points))
    result.colors = o3d.utility.Vector3dVector(final_colors)
    output.parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_point_cloud(str(output), result):
        raise RuntimeError(f"failed to write {output}")
    saved = o3d.io.read_point_cloud(str(output))
    saved_points, saved_colors = np.asarray(saved.points), np.asarray(saved.colors)
    if not np.array_equal(saved_points[: retained.sum()], baseline_points[retained]):
        raise RuntimeError("retained baseline coordinates changed during serialization")
    if not np.array_equal(saved_colors[: retained.sum()], baseline_colors[retained]):
        raise RuntimeError("retained baseline RGB changed during serialization")

    visual = json.loads(source.with_suffix(".visual.json").read_text(encoding="utf-8"))
    visual["pointcloud"] = output.name
    visual["repair_policy"] = "source-traced-jamb-removal-and-observed-bed-surfaces"
    output.with_suffix(".visual.json").write_text(
        json.dumps(visual, ensure_ascii=False), encoding="utf-8"
    )

    original_window = o3d.io.read_point_cloud(str(POST / "window_components_reveal_review.ply"))
    display_path = (
        ROOT
        / "data/work/46/window_local_sequence_20260907/window_components_reveal_review_display_qa.ply"
    )
    if display_path.exists():
        display = o3d.io.read_point_cloud(str(display_path))
        display_points = np.asarray(display.points)[len(original_window.points) :]
        display_colors = np.asarray(display.colors)[len(original_window.points) :]
    else:
        # The reproducible research bundle intentionally excludes this old
        # intermediate QA cloud.  The accepted PLY remains authoritative;
        # the unchanged visual-only quad is retained in the copied visual JSON.
        display_points = np.empty((0, 3), dtype=np.float64)
        display_colors = np.empty((0, 3), dtype=np.float64)
    before_points = np.vstack((baseline_points, display_points))
    before_colors = np.vstack((baseline_colors, display_colors))
    clean_points = np.vstack((baseline_points[retained], display_points))
    clean_colors = np.vstack((baseline_colors[retained], display_colors))
    after_points = np.vstack((final_points, display_points))
    after_colors = np.vstack((final_colors, display_colors))

    view_metrics: dict[str, dict] = {}
    for name, (camera, rotation, center) in VIEWS.items():
        before = render(before_points, before_colors, camera, rotation, center)
        clean = render(clean_points, clean_colors, camera, rotation, center)
        after = render(after_points, after_colors, camera, rotation, center)
        addition_metrics = fixed_view_metrics(clean, after)
        if addition_metrics["changed_existing_pixels"] or addition_metrics["new_black_pixels"]:
            raise RuntimeError(f"{name}: additions altered a protected rendered pixel")
        comparison = Image.new("RGB", (1792, 930), (15, 19, 26))
        comparison.paste(before, (0, 34))
        comparison.paste(after, (896, 34))
        draw = ImageDraw.Draw(comparison)
        draw.text((12, 10), "ACCEPTED BASELINE", fill="white")
        draw.text((908, 10), "SOURCE-TRACED REPAIR", fill="white")
        comparison.save(output.with_name(f"{output.stem}_{name}_comparison.png"))
        view_metrics[name] = {
            "identity_cleanup": fixed_view_metrics(before, clean),
            "observed_additions": addition_metrics,
        }

    report = {
        "status": "candidate",
        "source": str(source),
        "source_sha256": source_sha256,
        "output": str(output),
        "baseline_points": int(len(baseline_points)),
        "source_traced_jamb_points_removed": int(len(remove_ids)),
        "observed_points_added": int(len(added_points)),
        "output_points": int(len(final_points)),
        "retained_baseline_coordinates_and_rgb_exact": True,
        "excluded_surfaces": [
            item
            for item in (
                "r1_unverified",
                "r2_incomplete_soft_objects",
                "r3_observed_but_incomplete_point_render"
                if not include_observed_r3
                else None,
                "r5_pending_source_review",
                "unobserved_wall",
            )
            if item
        ],
        "components": component_audit,
        "include_observed_r3": include_observed_r3,
        "views": view_metrics,
    }
    output.with_suffix(".audit.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    return report


def accept_candidate(output: Path, report: dict) -> None:
    if output.resolve().parent != POST.resolve():
        raise RuntimeError("only a verified postprocess candidate may be accepted")
    if report["include_observed_r3"] or report["observed_points_added"]:
        raise RuntimeError("research additions cannot be accepted by the conservative policy")
    selection_path = POST / "preview_selection.json"
    backup = POST / "preview_selection.before_source_traced_repair.json"
    if not backup.exists():
        shutil.copyfile(selection_path, backup)
    selection = {
        "accepted_file": output.name,
        "baseline_scan": 45,
        "policy": "source_traced_window_jamb_identity_correction",
        "reason": (
            "Remove only 727 exact source-traced window-jamb pixels historically "
            "misclassified as curtain. Preserve every other accepted coordinate and "
            "RGB value; add no inferred geometry. Backup: "
            "preview_selection.before_source_traced_repair.json."
        ),
    }
    selection_path.write_text(
        json.dumps(selection, ensure_ascii=False, indent=2), encoding="utf-8"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", type=Path, default=POST / OUTPUT_NAME)
    parser.add_argument("--include-observed-r3", action="store_true")
    parser.add_argument("--accept", action="store_true")
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    report = build(args.output, include_observed_r3=args.include_observed_r3)
    if args.accept:
        accept_candidate(args.output, report)
