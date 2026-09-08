"""Read-only color/occupancy audit of scan 46's metric Y-max wall."""
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
OUT = POST / "qa_figure4_ymax_wall"


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

    # Determine the physical Y-max wall from the accepted cloud, never from
    # the viewer's rotated display axes.
    wall_band = base[(base[:, 1] > 1.28) & (base[:, 2] > .20)]
    wall_y = float(np.median(wall_band[:, 1]))
    depth = .035
    x_range = (-.55, 2.05)
    z_range = (.05, 2.45)
    cell = .008
    shape = (
        int(np.ceil((z_range[1] - z_range[0]) / cell)),
        int(np.ceil((x_range[1] - x_range[0]) / cell)),
    )

    def select(xyz: np.ndarray, rgb: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        keep = (
            (np.abs(xyz[:, 1] - wall_y) <= depth)
            & (xyz[:, 0] >= x_range[0]) & (xyz[:, 0] <= x_range[1])
            & (xyz[:, 2] >= z_range[0]) & (xyz[:, 2] <= z_range[1])
        )
        return xyz[keep], rgb[keep]

    def indices(xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        row = np.floor((z_range[1] - xyz[:, 2]) / cell).astype(np.int32)
        col = np.floor((xyz[:, 0] - x_range[0]) / cell).astype(np.int32)
        valid = (row >= 0) & (row < shape[0]) & (col >= 0) & (col < shape[1])
        return row, col, valid

    def color_map(xyz: np.ndarray, rgb: np.ndarray, name: str) -> None:
        row, col, valid = indices(xyz)
        xyz, rgb, row, col = xyz[valid], rgb[valid], row[valid], col[valid]
        flat = row.astype(np.int64) * shape[1] + col
        order = np.lexsort((np.abs(xyz[:, 1] - wall_y), flat))
        ordered = flat[order]
        first = np.ones(len(order), bool)
        first[1:] = ordered[1:] != ordered[:-1]
        chosen = order[first]
        canvas = np.zeros((*shape, 3), np.uint8)
        canvas[row[chosen], col[chosen]] = np.clip(rgb[chosen] * 255, 0, 255).astype(np.uint8)
        Image.fromarray(canvas).resize((shape[1] * 4, shape[0] * 4), Image.Resampling.NEAREST).save(OUT / name)

    b, bc = select(base, base_rgb)
    s, sc = select(source, source_rgb)
    OUT.mkdir(parents=True, exist_ok=True)
    color_map(b, bc, "figure4_ymax_rgb.png")
    color_map(s, sc, "raw_recon_ymax_rgb.png")

    br, bcol, bv = indices(b)
    sr, scol, sv = indices(s)
    base_occ = np.zeros(shape, bool)
    source_count = np.zeros(shape, np.uint16)
    base_occ[br[bv], bcol[bv]] = True
    np.add.at(source_count, (sr[sv], scol[sv]), 1)
    supported = source_count >= 2
    canvas = np.zeros((*shape, 3), np.uint8)
    canvas[base_occ] = (70, 140, 235)
    canvas[supported & base_occ] = (80, 220, 120)
    canvas[supported & ~base_occ] = (255, 100, 60)
    Image.fromarray(canvas).resize((shape[1] * 4, shape[0] * 4), Image.Resampling.NEAREST).save(OUT / "ymax_xz_occupancy.png")

    report = {
        "baseline": str(BASELINE),
        "source": str(SOURCE),
        "wall_axis_metric_ply": "+Y",
        "wall_y_m": wall_y,
        "depth_m": depth,
        "baseline_points": int(len(b)),
        "source_points": int(len(s)),
        "missing_supported_cells": int(np.count_nonzero(supported & ~base_occ)),
    }
    (OUT / "report.json").write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
