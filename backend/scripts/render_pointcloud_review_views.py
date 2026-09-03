"""CPU-render point clouds at the viewer's medium (~60%) point radius."""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw


def render(points: np.ndarray, colors: np.ndarray, azimuth: float, elevation: float,
           output: Path, radius: int = 2) -> None:
    width, height = 1500, 1100
    center = np.median(points, axis=0)
    az, el = np.deg2rad(azimuth), np.deg2rad(elevation)
    camera_vec = np.asarray([np.cos(az) * np.cos(el), np.sin(az) * np.cos(el), np.sin(el)])
    forward = -camera_vec
    right = np.cross(forward, np.asarray([0., 0., 1.]))
    if np.linalg.norm(right) < 1e-5:
        right = np.asarray([1., 0., 0.])
    right /= np.linalg.norm(right)
    up = np.cross(right, forward); up /= np.linalg.norm(up)
    relative = points - center
    x = relative @ right; y = relative @ up; depth = relative @ forward
    xlo, xhi = np.quantile(x, [.002, .998]); ylo, yhi = np.quantile(y, [.002, .998])
    scale = min((width - 80) / max(xhi - xlo, 1e-6), (height - 80) / max(yhi - ylo, 1e-6))
    px = ((x - xlo) * scale + 40).astype(np.int32)
    py = ((yhi - y) * scale + 40).astype(np.int32)
    valid = (px >= 0) & (px < width) & (py >= 0) & (py < height)
    px, py, depth = px[valid], py[valid], depth[valid]
    rgb = np.clip(colors[valid] * 255, 0, 255).astype(np.uint8)
    order = np.argsort(depth)  # far to near: nearer points overwrite
    canvas = np.full((height, width, 3), (15, 19, 26), np.uint8)
    for dy in range(-radius, radius + 1):
        for dx in range(-radius, radius + 1):
            if dx * dx + dy * dy > radius * radius + 1:
                continue
            xx = np.clip(px[order] + dx, 0, width - 1)
            yy = np.clip(py[order] + dy, 0, height - 1)
            canvas[yy, xx] = rgb[order]
    image = Image.fromarray(canvas)
    draw = ImageDraw.Draw(image)
    draw.text((14, 12), f"az={azimuth:.0f} elev={elevation:.0f} point_radius={radius}px (~60%)", fill=(255, 220, 50))
    output.parent.mkdir(parents=True, exist_ok=True); image.save(output)


def main() -> None:
    p = argparse.ArgumentParser(); p.add_argument("ply", type=Path); p.add_argument("output", type=Path)
    p.add_argument("--radius", type=int, default=2); args = p.parse_args()
    cloud = o3d.io.read_point_cloud(str(args.ply))
    xyz = np.asarray(cloud.points, float); rgb = np.asarray(cloud.colors, float)
    for az, el in ((-135, 48), (-45, 48), (45, 48), (135, 48), (-90, 72), (0, 72), (0, 89)):
        render(xyz, rgb, az, el, args.output / f"az{az}_el{el}.png", args.radius)


if __name__ == "__main__": main()
