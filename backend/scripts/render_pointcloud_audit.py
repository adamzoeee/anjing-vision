"""Render fixed-axis point-cloud projections for before/after coverage audits."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw


def render(path: Path, axes: tuple[int, int], depth_axis: int, bounds: np.ndarray, size=(720, 560)) -> Image.Image:
    cloud = o3d.io.read_point_cloud(str(path))
    points = np.asarray(cloud.points)
    colors = np.asarray(cloud.colors)
    # Tensor PLY writer stores float RGB after an additional /255 conversion;
    # the web loader compensates for it, and this audit renderer must do so too.
    if colors.size and float(colors.max()) <= 0.01:
        colors = colors * 255.0
    colors = (np.clip(colors, 0, 1) * 255).astype(np.uint8)
    width, height = size
    lo, hi = bounds[0], bounds[1]
    u = np.clip(((points[:, axes[0]] - lo[axes[0]]) / (hi[axes[0]] - lo[axes[0]]) * (width - 1)).astype(int), 0, width - 1)
    v = np.clip(((hi[axes[1]] - points[:, axes[1]]) / (hi[axes[1]] - lo[axes[1]]) * (height - 1)).astype(int), 0, height - 1)
    order = np.argsort(points[:, depth_axis])
    canvas = np.zeros((height, width, 3), dtype=np.uint8)
    canvas[v[order], u[order]] = colors[order]
    return Image.fromarray(canvas)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("before", type=Path)
    parser.add_argument("after", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    bounds = np.asarray([[-0.55, -0.57, -0.05], [2.03, 1.42, 2.67]], dtype=float)
    panels = []
    for path, title in ((args.before, "BEFORE"), (args.after, "REFUSED")):
        for axes, depth, view in (((0, 1), 2, "TOP XY"), ((0, 2), 1, "FRONT XZ"), ((1, 2), 0, "SIDE YZ")):
            image = render(path, axes, depth, bounds)
            draw = ImageDraw.Draw(image)
            draw.rectangle((0, 0, 190, 28), fill=(10, 15, 25))
            draw.text((8, 7), f"{title} {view}", fill=(255, 255, 255))
            panels.append(image)
    output = Image.new("RGB", (720 * 3, 560 * 2), (15, 15, 15))
    for index, image in enumerate(panels):
        output.paste(image, ((index % 3) * 720, (index // 3) * 560))
    output.save(args.output, quality=95)


if __name__ == "__main__":
    main()
