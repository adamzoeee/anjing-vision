"""Rank historical scan 46 preview clouds for each of the six repair regions.

Read-only diagnostic: dense coverage is only a candidate signal; the report does
not promote or modify any preview file.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d


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


def occupancy(xyz: np.ndarray, axes: tuple[int, int], lo: np.ndarray,
              hi: np.ndarray, bins: tuple[int, int] = (160, 160)) -> int:
    if not len(xyz):
        return 0
    u = (xyz[:, axes[0]] - lo[axes[0]]) / max(hi[axes[0]] - lo[axes[0]], 1e-9)
    v = (xyz[:, axes[1]] - lo[axes[1]]) / max(hi[axes[1]] - lo[axes[1]], 1e-9)
    x = np.clip((u * (bins[0] - 1)).astype(int), 0, bins[0] - 1)
    y = np.clip((v * (bins[1] - 1)).astype(int), 0, bins[1] - 1)
    return int(np.unique(y * bins[0] + x).size)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("region_report", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("ply", type=Path, nargs="+")
    args = parser.parse_args()
    spec = json.loads(args.region_report.read_text(encoding="utf-8"))
    regions = {
        name: (np.asarray(value["lower"], float), np.asarray(value["upper"], float))
        for name, value in spec["regions"].items()
    }
    result: dict[str, object] = {"sources": {}}
    for path in args.ply:
        cloud = o3d.io.read_point_cloud(str(path))
        xyz = np.asarray(cloud.points, dtype=float)
        rgb = np.asarray(cloud.colors, dtype=float)
        item = {"points": int(len(xyz)), "regions": {}}
        for name, (lo, hi) in regions.items():
            mask = inside(xyz, lo, hi)
            colors = rgb[mask]
            item["regions"][name] = {
                "points": int(mask.sum()),
                "occupied_cells_160": occupancy(xyz[mask], PLANES[name], lo, hi),
                "median_brightness": float(np.median(colors.mean(axis=1))) if len(colors) else None,
                "dark_fraction_lt_008": float(np.mean(colors.mean(axis=1) < 0.08)) if len(colors) else None,
            }
        result["sources"][path.name] = item
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
