"""Render scan 46 from all four metric wall directions; read-only audit."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import open3d as o3d
from PIL import Image


ROOT = Path(__file__).resolve().parents[2]
WORK45 = ROOT / "backend/data/work/45"
POST = ROOT / "backend/data/work/46/postprocess"
BASELINE = POST / "scene_preview_local_surface_repair_candidate.ply"
SOURCE = WORK45 / "slam3r/scene/45_frames_recon.ply"
OUT = POST / "qa_figure4_all_wall_views"
CELL = .008
X_RANGE = (-.55, 2.05)
Y_RANGE = (-.55, 1.36)
Z_RANGE = (.05, 2.45)


def read(path: Path) -> tuple[np.ndarray, np.ndarray]:
    cloud = o3d.io.read_point_cloud(str(path))
    return np.asarray(cloud.points, np.float64), np.asarray(cloud.colors, np.float64)


def render(xyz: np.ndarray, rgb: np.ndarray, axis: int, sign: int, name: str) -> None:
    # axis X: image horizontal=Y; axis Y: image horizontal=X.  Closest point
    # to the selected wall-facing camera wins per pixel.
    h_axis = 1 if axis == 0 else 0
    h_range = Y_RANGE if axis == 0 else X_RANGE
    keep = (
        (xyz[:, h_axis] >= h_range[0]) & (xyz[:, h_axis] <= h_range[1])
        & (xyz[:, 2] >= Z_RANGE[0]) & (xyz[:, 2] <= Z_RANGE[1])
    )
    p, c = xyz[keep], rgb[keep]
    height = int(np.ceil((Z_RANGE[1] - Z_RANGE[0]) / CELL))
    width = int(np.ceil((h_range[1] - h_range[0]) / CELL))
    row = np.floor((Z_RANGE[1] - p[:, 2]) / CELL).astype(np.int32)
    col = np.floor((p[:, h_axis] - h_range[0]) / CELL).astype(np.int32)
    valid = (row >= 0) & (row < height) & (col >= 0) & (col < width)
    p, c, row, col = p[valid], c[valid], row[valid], col[valid]
    flat = row.astype(np.int64) * width + col
    order = np.lexsort((sign * p[:, axis], flat))
    ordered = flat[order]
    chosen_mask = np.ones(len(order), bool)
    chosen_mask[:-1] = ordered[:-1] != ordered[1:]
    chosen = order[chosen_mask]
    canvas = np.zeros((height, width, 3), np.uint8)
    canvas[row[chosen], col[chosen]] = np.clip(c[chosen] * 255.0, 0, 255).astype(np.uint8)
    if sign < 0:
        canvas = canvas[:, ::-1]
    Image.fromarray(canvas).resize((width * 4, height * 4), Image.Resampling.NEAREST).save(OUT / name)


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
    OUT.mkdir(parents=True, exist_ok=True)
    for label, axis, sign in (("plus_x", 0, 1), ("minus_x", 0, -1), ("plus_y", 1, 1), ("minus_y", 1, -1)):
        render(source, source_rgb, axis, sign, f"raw_{label}.png")
        render(base, base_rgb, axis, sign, f"figure4_{label}.png")
    print(OUT)


if __name__ == "__main__":
    main()
