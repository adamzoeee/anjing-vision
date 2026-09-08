"""Render close-up A/B projections for scan 46's two locked repair ROIs."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import open3d as o3d
from PIL import Image, ImageDraw


ROOT = Path(__file__).resolve().parents[2]
POST = ROOT / "backend/data/work/46/postprocess"
FILES = {
    "baseline": POST / "scene_preview_local_surface_repair_candidate.ply",
    "floor": POST / "scene_preview_figure4_plus_floor_ymax_consensus.ply",
    "floor+window": POST / "scene_preview_figure4_plus_floor_window_ymax_consensus.ply",
}
ROIS = {
    "entrance_floor_xy": (
        np.array([-.49, .72, -.04]),
        np.array([.38, 1.18, .20]), (0, 1),
    ),
    "window_wall_xz": (
        np.array([-.20, 1.275, .82]),
        np.array([1.52, 1.505, 2.28]), (0, 2),
    ),
}


def load(path: Path) -> tuple[np.ndarray, np.ndarray]:
    cloud = o3d.io.read_point_cloud(str(path))
    return np.asarray(cloud.points), np.asarray(cloud.colors)


def panel(xyz: np.ndarray, rgb: np.ndarray, lo: np.ndarray, hi: np.ndarray,
          axes: tuple[int, int], title: str) -> Image.Image:
    width, height = 700, 700
    mask = np.all((xyz >= lo) & (xyz <= hi), axis=1)
    pts, colors = xyz[mask], rgb[mask]
    canvas = np.full((height, width, 3), (15, 19, 26), dtype=np.uint8)
    if len(pts):
        span = hi[list(axes)] - lo[list(axes)]
        scale = min((width - 60) / span[0], (height - 90) / span[1])
        px = ((pts[:, axes[0]] - lo[axes[0]]) * scale + 30).astype(int)
        py = ((hi[axes[1]] - pts[:, axes[1]]) * scale + 55).astype(int)
        source = np.clip(colors * 255, 0, 255).astype(np.uint8)
        # Four pixels approximates the user's accepted ~60% viewer point size.
        for dx, dy in ((0, 0), (1, 0), (0, 1), (1, 1)):
            canvas[np.clip(py + dy, 0, height - 1), np.clip(px + dx, 0, width - 1)] = source
    image = Image.fromarray(canvas)
    draw = ImageDraw.Draw(image)
    draw.text((15, 12), f"{title} | points={len(pts)}", fill=(255, 220, 50))
    return image


def main() -> None:
    clouds = {name: load(path) for name, path in FILES.items()}
    output = POST / "qa_figure4_ymax_consensus_roi"
    output.mkdir(exist_ok=True)
    for roi_name, (lo, hi, axes) in ROIS.items():
        images = [panel(*clouds[name], lo, hi, axes, name) for name in FILES]
        joined = Image.new("RGB", (sum(im.width for im in images), images[0].height))
        x = 0
        for image in images:
            joined.paste(image, (x, 0)); x += image.width
        joined.save(output / f"{roi_name}.png")
        print(output / f"{roi_name}.png")


if __name__ == "__main__":
    main()
