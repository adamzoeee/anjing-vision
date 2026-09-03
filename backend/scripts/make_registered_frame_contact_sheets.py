"""Render indexed contact sheets for SLAM3R registered input frames."""
from __future__ import annotations

import argparse
import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("images", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--per-sheet", type=int, default=100)
    parser.add_argument("--columns", type=int, default=10)
    parser.add_argument("--width", type=int, default=180)
    args = parser.parse_args()

    images = np.load(args.images, mmap_mode="r")
    args.output.mkdir(parents=True, exist_ok=True)
    font = ImageFont.load_default()
    count = len(images)
    for page_start in range(0, count, args.per_sheet):
        page_end = min(page_start + args.per_sheet, count)
        slots = page_end - page_start
        rows = math.ceil(slots / args.columns)
        label_h = 22
        sheet = Image.new("RGB", (args.columns * args.width, rows * (args.width + label_h)), "white")
        draw = ImageDraw.Draw(sheet)
        for slot, frame_id in enumerate(range(page_start, page_end)):
            array = np.asarray(images[frame_id])
            if np.issubdtype(array.dtype, np.floating):
                if float(np.nanmax(array)) <= 1.5:
                    array = array * 255.0
            array = np.clip(array, 0, 255).astype(np.uint8)
            image = Image.fromarray(array).resize((args.width, args.width), Image.Resampling.BILINEAR)
            x = (slot % args.columns) * args.width
            y = (slot // args.columns) * (args.width + label_h)
            sheet.paste(image, (x, y))
            draw.text((x + 4, y + args.width + 4), f"registered frame {frame_id}", fill="black", font=font)
        target = args.output / f"frames_{page_start:04d}_{page_end - 1:04d}.jpg"
        sheet.save(target, quality=92)
        print(target)


if __name__ == "__main__":
    main()
