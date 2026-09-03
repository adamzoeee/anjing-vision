"""Generate a compact contact sheet for reconstruction input auditing."""

from __future__ import annotations

import argparse
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--columns", type=int, default=6)
    parser.add_argument("--samples", type=int, default=24)
    parser.add_argument("--width", type=int, default=320)
    args = parser.parse_args()

    files = sorted(
        [*args.input_dir.glob("*.jpg"), *args.input_dir.glob("*.jpeg"), *args.input_dir.glob("*.png")]
    )
    if not files:
        raise SystemExit(f"no images found in {args.input_dir}")
    count = min(max(args.samples, 1), len(files))
    indices = sorted({round(index * (len(files) - 1) / max(count - 1, 1)) for index in range(count)})
    selected = [files[index] for index in indices]

    label_height = 26
    thumb_height = round(args.width * 9 / 16)
    rows = (len(selected) + args.columns - 1) // args.columns
    sheet = Image.new("RGB", (args.columns * args.width, rows * (thumb_height + label_height)), "white")
    draw = ImageDraw.Draw(sheet)
    font = ImageFont.load_default()
    for slot, path in enumerate(selected):
        with Image.open(path) as source:
            image = source.convert("RGB")
            image.thumbnail((args.width, thumb_height))
            cell_x = (slot % args.columns) * args.width
            cell_y = (slot // args.columns) * (thumb_height + label_height)
            x = cell_x + (args.width - image.width) // 2
            y = cell_y + (thumb_height - image.height) // 2
            sheet.paste(image, (x, y))
            draw.text((cell_x + 5, cell_y + thumb_height + 5), path.stem, fill="black", font=font)

    args.output.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(args.output, quality=92)
    print(args.output)


if __name__ == "__main__":
    main()
