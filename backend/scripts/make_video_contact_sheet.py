"""Create timestamped, uniformly sampled contact sheets from videos."""

from __future__ import annotations

import argparse
import math
from pathlib import Path

import cv2
from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("video", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--samples", type=int, default=24)
    parser.add_argument("--columns", type=int, default=6)
    parser.add_argument("--width", type=int, default=320)
    args = parser.parse_args()

    cap = cv2.VideoCapture(str(args.video))
    if not cap.isOpened():
        raise RuntimeError(f"Cannot open {args.video}")
    frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
    fps = float(cap.get(cv2.CAP_PROP_FPS) or 30.0)
    count = min(max(args.samples, 1), max(frames, 1))
    indices = [round(i * (frames - 1) / max(count - 1, 1)) for i in range(count)]
    label_h = 28
    thumb_h = round(args.width * 9 / 16)
    rows = math.ceil(count / args.columns)
    sheet = Image.new("RGB", (args.columns * args.width, rows * (thumb_h + label_h)), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for slot, frame_index in enumerate(indices):
        cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
        ok, bgr = cap.read()
        if not ok:
            continue
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        image = Image.fromarray(rgb)
        image.thumbnail((args.width, thumb_h))
        cell_x = (slot % args.columns) * args.width
        cell_y = (slot // args.columns) * (thumb_h + label_h)
        x = cell_x + (args.width - image.width) // 2
        y = cell_y + (thumb_h - image.height) // 2
        sheet.paste(image, (x, y))
        seconds = frame_index / fps
        label = f"{seconds // 60:02.0f}:{seconds % 60:05.2f}  f{frame_index}"
        draw.text((cell_x + 4, cell_y + thumb_h + 6), label, fill="black", font=font)
    cap.release()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output, quality=92)
    print(f"{args.output}\tframes={frames}\tfps={fps:.3f}\tduration={frames/fps:.2f}s")


if __name__ == "__main__":
    main()
