"""Audit the +X wall of scan 46 without modifying the served preview."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
WORK45 = ROOT / "backend/data/work/45"
POST = ROOT / "backend/data/work/46/postprocess"
BASELINE = POST / "scene_preview_local_surface_repair_candidate.ply"
SOURCE = WORK45 / "slam3r/scene/45_frames_recon.ply"
OUT = POST / "qa_figure4_xmax_wall"


def read(path: Path) -> tuple[np.ndarray, np.ndarray]:
    cloud = o3d.io.read_point_cloud(str(path))
    return np.asarray(cloud.points, np.float64), np.asarray(cloud.colors, np.float64)


def main() -> None:
    alignment = json.loads((WORK45 / "postprocess/alignment.json").read_text(encoding="utf-8"))
    rotation = np.asarray(alignment["alignment"]["rotation"], np.float64)
    floor_z = float(alignment["alignment"]["floor_z_raw_aligned"])
    scale = float(alignment["scale"]["applied"])
    source, source_rgb = read(SOURCE)
    source = source @ rotation.T
    source[:, 2] = (source[:, 2] - floor_z) * scale
    source[:, :2] *= scale
    base, base_rgb = read(BASELINE)

    # The accepted Figure-4 +X wall is concentrated at x~=1.958 m.  Audit only
    # a narrow physical depth band so furniture in front of the wall cannot be
    # mistaken for missing wall observations.
    wall_x = float(np.median(base[(base[:, 0] > 1.94) & (base[:, 2] > .15), 0]))
    depth = .035
    y_range = (-.55, 1.36)
    z_range = (.05, 2.45)
    cell = .008

    def wall_points(xyz: np.ndarray, rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        keep = (
            (np.abs(xyz[:, 0] - wall_x) <= depth)
            & (xyz[:, 1] >= y_range[0]) & (xyz[:, 1] <= y_range[1])
            & (xyz[:, 2] >= z_range[0]) & (xyz[:, 2] <= z_range[1])
        )
        return xyz[keep], rgb[keep]

    b, bc = wall_points(base, base_rgb)
    s, sc = wall_points(source, source_rgb)
    shape = (
        int(np.ceil((z_range[1] - z_range[0]) / cell)),
        int(np.ceil((y_range[1] - y_range[0]) / cell)),
    )

    def cells(xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        col = np.floor((xyz[:, 1] - y_range[0]) / cell).astype(np.int32)
        row = np.floor((z_range[1] - xyz[:, 2]) / cell).astype(np.int32)
        valid = (row >= 0) & (row < shape[0]) & (col >= 0) & (col < shape[1])
        return row[valid], col[valid]

    br, bcol = cells(b)
    sr, scol = cells(s)
    base_occ = np.zeros(shape, bool)
    src_count = np.zeros(shape, np.uint16)
    base_occ[br, bcol] = True
    np.add.at(src_count, (sr, scol), 1)
    supported = src_count >= 2
    missing = supported & ~base_occ

    scale_px = 4
    canvas = np.zeros((shape[0], shape[1], 3), np.uint8)
    canvas[base_occ] = (95, 155, 235)
    canvas[supported & base_occ] = (100, 220, 130)
    canvas[missing] = (255, 110, 70)
    image = Image.fromarray(canvas).resize((shape[1] * scale_px, shape[0] * scale_px), Image.Resampling.NEAREST)
    draw = ImageDraw.Draw(image)
    draw.text((12, 10), "blue=Figure4  green=both  red=raw real observations missing from Figure4", fill=(255, 255, 255))
    OUT.mkdir(parents=True, exist_ok=True)
    image.save(OUT / "xmax_yz_occupancy.png")

    def color_map(xyz: np.ndarray, rgb_values: np.ndarray, name: str) -> None:
        row, col = cells(xyz)
        # Pick the observation closest to the accepted wall plane per YZ cell.
        valid = (
            (np.floor((z_range[1] - xyz[:, 2]) / cell).astype(np.int32) >= 0)
            & (np.floor((z_range[1] - xyz[:, 2]) / cell).astype(np.int32) < shape[0])
            & (np.floor((xyz[:, 1] - y_range[0]) / cell).astype(np.int32) >= 0)
            & (np.floor((xyz[:, 1] - y_range[0]) / cell).astype(np.int32) < shape[1])
        )
        xyz_v, rgb_v = xyz[valid], rgb_values[valid]
        row = np.floor((z_range[1] - xyz_v[:, 2]) / cell).astype(np.int32)
        col = np.floor((xyz_v[:, 1] - y_range[0]) / cell).astype(np.int32)
        flat = row.astype(np.int64) * shape[1] + col
        order = np.lexsort((np.abs(xyz_v[:, 0] - wall_x), flat))
        flat_ordered = flat[order]
        first = np.ones(len(order), bool)
        first[1:] = flat_ordered[1:] != flat_ordered[:-1]
        chosen = order[first]
        rgb_img = np.zeros((shape[0], shape[1], 3), np.uint8)
        rgb_img[row[chosen], col[chosen]] = np.clip(rgb_v[chosen] * 255.0, 0, 255).astype(np.uint8)
        Image.fromarray(rgb_img).resize(
            (shape[1] * scale_px, shape[0] * scale_px), Image.Resampling.NEAREST
        ).save(OUT / name)

    color_map(b, bc, "figure4_xmax_rgb.png")
    color_map(s, sc, "raw_recon_xmax_rgb.png")

    # Face-on +X view of the complete raw reconstruction.  The front-most
    # observation in each YZ cell makes the actual wall/window placement
    # inspectable without guessing it from a 2-D frame rectangle.
    full_keep = (
        (source[:, 1] >= y_range[0]) & (source[:, 1] <= y_range[1])
        & (source[:, 2] >= z_range[0]) & (source[:, 2] <= z_range[1])
    )
    full, full_rgb = source[full_keep], source_rgb[full_keep]
    row = np.floor((z_range[1] - full[:, 2]) / cell).astype(np.int32)
    col = np.floor((full[:, 1] - y_range[0]) / cell).astype(np.int32)
    valid = (row >= 0) & (row < shape[0]) & (col >= 0) & (col < shape[1])
    full, full_rgb, row, col = full[valid], full_rgb[valid], row[valid], col[valid]
    flat = row.astype(np.int64) * shape[1] + col
    # Sort ascending x and retain the last (largest x, nearest +X camera).
    order = np.lexsort((full[:, 0], flat))
    flat_ordered = flat[order]
    last = np.ones(len(order), bool)
    last[:-1] = flat_ordered[:-1] != flat_ordered[1:]
    chosen = order[last]
    full_img = np.zeros((shape[0], shape[1], 3), np.uint8)
    full_img[row[chosen], col[chosen]] = np.clip(full_rgb[chosen] * 255.0, 0, 255).astype(np.uint8)
    Image.fromarray(full_img).resize(
        (shape[1] * scale_px, shape[0] * scale_px), Image.Resampling.NEAREST
    ).save(OUT / "raw_recon_full_plus_x_view.png")

    # Equivalent complete +Y view (horizontal X, vertical Z).  This resolves
    # the frequent confusion between PLY metric axes and the viewer's -90deg
    # X rotation before any ROI is selected.
    x_range = (-.55, 2.05)
    yz_shape = (shape[0], int(np.ceil((x_range[1] - x_range[0]) / cell)))
    keep_y = (
        (source[:, 0] >= x_range[0]) & (source[:, 0] <= x_range[1])
        & (source[:, 2] >= z_range[0]) & (source[:, 2] <= z_range[1])
    )
    full_y, rgb_y = source[keep_y], source_rgb[keep_y]
    row_y = np.floor((z_range[1] - full_y[:, 2]) / cell).astype(np.int32)
    col_y = np.floor((full_y[:, 0] - x_range[0]) / cell).astype(np.int32)
    valid_y = (row_y >= 0) & (row_y < yz_shape[0]) & (col_y >= 0) & (col_y < yz_shape[1])
    full_y, rgb_y, row_y, col_y = full_y[valid_y], rgb_y[valid_y], row_y[valid_y], col_y[valid_y]
    flat_y = row_y.astype(np.int64) * yz_shape[1] + col_y
    order_y = np.lexsort((full_y[:, 1], flat_y))
    ordered_y = flat_y[order_y]
    last_y = np.ones(len(order_y), bool)
    last_y[:-1] = ordered_y[:-1] != ordered_y[1:]
    chosen_y = order_y[last_y]
    image_y = np.zeros((yz_shape[0], yz_shape[1], 3), np.uint8)
    image_y[row_y[chosen_y], col_y[chosen_y]] = np.clip(rgb_y[chosen_y] * 255.0, 0, 255).astype(np.uint8)
    Image.fromarray(image_y).resize(
        (yz_shape[1] * scale_px, yz_shape[0] * scale_px), Image.Resampling.NEAREST
    ).save(OUT / "raw_recon_full_plus_y_view.png")

    report = {
        "baseline": str(BASELINE),
        "source": str(SOURCE),
        "wall_axis": "+X",
        "wall_x_m": wall_x,
        "depth_tolerance_m": depth,
        "y_range_m": list(y_range),
        "z_range_m": list(z_range),
        "cell_m": cell,
        "baseline_wall_points": int(len(b)),
        "source_wall_points": int(len(s)),
        "baseline_cells": int(base_occ.sum()),
        "source_supported_cells_min2": int(supported.sum()),
        "missing_supported_cells": int(missing.sum()),
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
