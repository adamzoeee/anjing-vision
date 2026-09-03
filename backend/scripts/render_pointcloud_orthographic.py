"""Render deterministic RGB orthographic point-cloud audits without WebGL."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw


def render(points: np.ndarray, colors: np.ndarray, axes: tuple[int, int], output: Path) -> None:
    width, height = 1400, 1000
    valid = np.isfinite(points).all(axis=1)
    points, colors = points[valid], colors[valid]
    lo = np.quantile(points[:, axes], 0.001, axis=0)
    hi = np.quantile(points[:, axes], 0.999, axis=0)
    span = np.maximum(hi - lo, 1e-9)
    scale = min((width - 80) / span[0], (height - 80) / span[1])
    x = np.clip(((points[:, axes[0]] - lo[0]) * scale + 40).astype(int), 0, width - 1)
    y = np.clip(((hi[1] - points[:, axes[1]]) * scale + 40).astype(int), 0, height - 1)
    rgb = np.full((height, width, 3), 15, dtype=np.uint8)
    source = np.clip(colors * 255.0, 0, 255).astype(np.uint8)
    depth_axis = ({0, 1, 2} - set(axes)).pop()
    order = np.argsort(points[:, depth_axis])
    for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
        rgb[np.clip(y[order] + dy, 0, height - 1), np.clip(x[order] + dx, 0, width - 1)] = source[order]
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    names = "XYZ"
    draw.text((12, 10), f"{names[axes[0]]}: {lo[0]:.3f}..{hi[0]:.3f} m | {names[axes[1]]}: {lo[1]:.3f}..{hi[1]:.3f} m", fill=(255, 220, 40))
    output.parent.mkdir(parents=True, exist_ok=True)
    image.save(output)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ply", type=Path)
    parser.add_argument("output_dir", type=Path)
    args = parser.parse_args()
    cloud = o3d.io.read_point_cloud(str(args.ply))
    points = np.asarray(cloud.points, dtype=np.float64)
    colors = np.asarray(cloud.colors, dtype=np.float64)
    for name, axes in (("xy", (0, 1)), ("xz", (0, 2)), ("yz", (1, 2))):
        render(points, colors, axes, args.output_dir / f"{name}.png")


if __name__ == "__main__":
    main()
