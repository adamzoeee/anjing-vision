"""只读渲染已对齐点云的俯视高度图，供测量/结构几何审计。"""
from pathlib import Path
import argparse

import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("ply", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--pixels", type=int, default=1400)
    args = parser.parse_args()

    points = np.asarray(o3d.io.read_point_cloud(str(args.ply)).points, dtype=np.float64)
    points = points[np.isfinite(points).all(axis=1)]
    lo = np.percentile(points[:, :2], 0.2, axis=0)
    hi = np.percentile(points[:, :2], 99.8, axis=0)
    span = np.maximum(hi - lo, 1e-6)
    scale = (args.pixels - 40) / max(span)
    width = int(np.ceil(span[0] * scale)) + 40
    height = int(np.ceil(span[1] * scale)) + 40
    ix = np.clip(((points[:, 0] - lo[0]) * scale).astype(int) + 20, 0, width - 1)
    iy = np.clip(((hi[1] - points[:, 1]) * scale).astype(int) + 20, 0, height - 1)
    z = np.clip(points[:, 2], 0, np.percentile(points[:, 2], 99.5))
    zmax = np.full((height, width), -1.0, dtype=np.float32)
    density = np.zeros((height, width), dtype=np.uint16)
    np.maximum.at(zmax, (iy, ix), z.astype(np.float32))
    np.add.at(density, (iy, ix), 1)
    occupied = density > 0
    rgb = np.full((height, width, 3), 248, dtype=np.uint8)
    normalized = np.clip(zmax / max(float(z.max()), 1e-6), 0, 1)
    rgb[..., 0][occupied] = (35 + 210 * normalized[occupied]).astype(np.uint8)
    rgb[..., 1][occupied] = (65 + 150 * (1 - normalized[occupied])).astype(np.uint8)
    rgb[..., 2][occupied] = (230 - 160 * normalized[occupied]).astype(np.uint8)
    image = Image.fromarray(rgb)
    draw = ImageDraw.Draw(image)
    draw.text((22, 4), f"X {lo[0]:.2f}..{hi[0]:.2f} m | Y {lo[1]:.2f}..{hi[1]:.2f} m", fill=(0, 0, 0))
    args.output.parent.mkdir(parents=True, exist_ok=True)
    image.save(args.output)


if __name__ == "__main__":
    main()
