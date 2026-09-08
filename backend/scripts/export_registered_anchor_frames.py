"""Export selected registered input frames with pixel-coordinate grids.

This is a diagnostic utility only. It lets point-cloud repair code derive a
world-space ROI from pixels on a known object instead of guessing an axis or
hard-coding a viewer-space box.
"""
from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("images", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("frames", nargs="+", type=int)
    parser.add_argument("--scale", type=int, default=4)
    parser.add_argument("--grid", type=int, default=16)
    args = parser.parse_args()

    images = np.load(args.images, mmap_mode="r")
    args.output.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()

    for frame_id in args.frames:
        array = np.asarray(images[frame_id])
        if np.issubdtype(array.dtype, np.floating) and float(np.nanmax(array)) <= 1.5:
            array = array * 255.0
        array = np.clip(array, 0, 255).astype(np.uint8)
        source = Image.fromarray(array)
        canvas = source.resize(
            (source.width * args.scale, source.height * args.scale),
            Image.Resampling.NEAREST,
        )
        draw = ImageDraw.Draw(canvas)
        for pixel in range(0, source.width + 1, args.grid):
            position = pixel * args.scale
            draw.line((position, 0, position, canvas.height), fill=(255, 0, 255), width=1)
            draw.text((position + 2, 2), str(pixel), fill=(255, 0, 255), font=font)
        for pixel in range(0, source.height + 1, args.grid):
            position = pixel * args.scale
            draw.line((0, position, canvas.width, position), fill=(0, 255, 255), width=1)
            draw.text((2, position + 2), str(pixel), fill=(0, 255, 255), font=font)
        target = args.output / f"registered_frame_{frame_id:04d}_grid.png"
        canvas.save(target)
        print(target)


if __name__ == "__main__":
    main()
