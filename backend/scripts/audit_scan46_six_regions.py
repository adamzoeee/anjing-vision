"""Audit scan 46 six-region candidate without modifying any reconstruction."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw, ImageFont
from scipy.spatial import cKDTree


PLANES = {
    "door_floor": (0, 1),
    "bedhead_desk_wall": (1, 2),
    "window_wall": (1, 2),
    "bookshelf": (0, 2),
    "bed_gap": (0, 1),
    "foreground_floor": (0, 1),
}


def inside(xyz: np.ndarray, lo: np.ndarray, hi: np.ndarray) -> np.ndarray:
    return np.all((xyz >= lo) & (xyz <= hi), axis=1)


def project(xyz: np.ndarray, rgb: np.ndarray, axes: tuple[int, int], lo: np.ndarray,
            hi: np.ndarray, size: tuple[int, int]) -> Image.Image:
    width, height = size
    canvas = np.full((height, width, 3), 18, dtype=np.uint8)
    if not len(xyz):
        return Image.fromarray(canvas)
    u = (xyz[:, axes[0]] - lo[axes[0]]) / max(hi[axes[0]] - lo[axes[0]], 1e-9)
    v = (xyz[:, axes[1]] - lo[axes[1]]) / max(hi[axes[1]] - lo[axes[1]], 1e-9)
    px = np.clip((u * (width - 1)).astype(int), 0, width - 1)
    py = np.clip(((1.0 - v) * (height - 1)).astype(int), 0, height - 1)
    color = np.clip(rgb * 255.0, 0, 255).astype(np.uint8)
    # Stable back-to-front overwrite is sufficient for a density/coverage audit.
    canvas[py, px] = color
    return Image.fromarray(canvas)


def occupancy(xyz: np.ndarray, axes: tuple[int, int], lo: np.ndarray, hi: np.ndarray,
              bins: tuple[int, int] = (100, 100)) -> int:
    if not len(xyz):
        return 0
    u = (xyz[:, axes[0]] - lo[axes[0]]) / max(hi[axes[0]] - lo[axes[0]], 1e-9)
    v = (xyz[:, axes[1]] - lo[axes[1]]) / max(hi[axes[1]] - lo[axes[1]], 1e-9)
    x = np.clip((u * (bins[0] - 1)).astype(int), 0, bins[0] - 1)
    y = np.clip((v * (bins[1] - 1)).astype(int), 0, bins[1] - 1)
    return int(np.unique(y * bins[0] + x).size)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("candidate_report", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()

    report = json.loads(args.candidate_report.read_text(encoding="utf-8"))
    regions = {
        name: (np.asarray(spec["lower"], dtype=float), np.asarray(spec["upper"], dtype=float))
        for name, spec in report["regions"].items()
    }
    base = o3d.io.read_point_cloud(str(args.baseline))
    cand = o3d.io.read_point_cloud(str(args.candidate))
    base_xyz, base_rgb = np.asarray(base.points), np.asarray(base.colors)
    cand_xyz, cand_rgb = np.asarray(cand.points), np.asarray(cand.colors)
    tree = cKDTree(base_xyz)
    nearest, _ = tree.query(cand_xyz, workers=-1)
    new_mask = nearest >= 0.006

    args.output_dir.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    rows = []
    metrics = {}
    for name, (lo, hi) in regions.items():
        axes = PLANES[name]
        bm = inside(base_xyz, lo, hi)
        cm = inside(cand_xyz, lo, hi)
        nm = cm & new_mask
        before = project(base_xyz[bm], base_rgb[bm], axes, lo, hi, (520, 300))
        after = project(cand_xyz[cm], cand_rgb[cm], axes, lo, hi, (520, 300))
        panel = Image.new("RGB", (1080, 345), (10, 14, 20))
        panel.paste(before, (10, 35))
        panel.paste(after, (550, 35))
        draw = ImageDraw.Draw(panel)
        before_occ = occupancy(base_xyz[bm], axes, lo, hi)
        after_occ = occupancy(cand_xyz[cm], axes, lo, hi)
        gain = after_occ - before_occ
        draw.text((10, 8), f"{name}  BEFORE points={int(bm.sum())} occ={before_occ}", fill=(230, 230, 230), font=font)
        draw.text((550, 8), f"AFTER points={int(cm.sum())} new={int(nm.sum())} occ={after_occ} gain={gain:+d}", fill=(130, 235, 160), font=font)
        rows.append(panel)
        metrics[name] = {
            "baseline_points": int(bm.sum()),
            "candidate_points": int(cm.sum()),
            "new_or_replaced_points": int(nm.sum()),
            "baseline_occupied_cells": before_occ,
            "candidate_occupied_cells": after_occ,
            "occupied_cell_gain": gain,
        }

    sheet = Image.new("RGB", (1080, 345 * len(rows)), (10, 14, 20))
    for idx, row in enumerate(rows):
        sheet.paste(row, (0, idx * 345))
    sheet.save(args.output_dir / "six_region_before_after.png")

    base_q = np.quantile(base_rgb, [0.01, 0.5, 0.99], axis=0).tolist()
    cand_q = np.quantile(cand_rgb, [0.01, 0.5, 0.99], axis=0).tolist()
    audit = {
        "baseline_points": int(len(base_xyz)),
        "candidate_points": int(len(cand_xyz)),
        "candidate_points_not_in_baseline_6mm": int(new_mask.sum()),
        "baseline_color_quantiles": base_q,
        "candidate_color_quantiles": cand_q,
        "color_not_crushed": bool(np.quantile(cand_rgb, 0.5) > 0.08),
        "outside_six_regions_unchanged": bool(report.get("outside_six_regions_unchanged")),
        "regions": metrics,
    }
    (args.output_dir / "six_region_audit.json").write_text(
        json.dumps(audit, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(audit, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
