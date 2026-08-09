"""本地真实 GroundingDINO→SAM 静态图片验证工具（结果目录必须保持 Git 忽略）。"""

from __future__ import annotations

import argparse
import json
import platform
import sys
import time
from collections import Counter
from pathlib import Path

import numpy as np
import torch
from PIL import Image, ImageDraw, ImageFont

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from pipeline.semantic import (
    CHINESE_PROMPT_OBJECTS,
    PROMPT_OBJECTS,
    analyze_image,
    model_runtime_info,
)

COLORS = {
    "纸箱": (255, 140, 0),
    "杂物": (255, 40, 40),
    "椅子": (40, 160, 255),
    "桌子": (160, 80, 255),
    "盆栽": (40, 200, 80),
    "宠物": (255, 80, 180),
    "水桶": (30, 210, 210),
    "行李箱": (230, 190, 20),
    "收纳箱": (140, 100, 60),
}


def _font(size: int = 18):
    candidates = [
        Path("C:/Windows/Fonts/msyh.ttc"),
        Path("C:/Windows/Fonts/simhei.ttf"),
    ]
    for path in candidates:
        if path.is_file():
            return ImageFont.truetype(str(path), size=size)
    return ImageFont.load_default()


def _select_images(files: list[Path], phase: str, limit: int | None) -> list[Path]:
    if phase == "first" and len(files) > 30:
        indices = np.linspace(0, len(files) - 1, 30, dtype=int)
        files = [files[index] for index in indices]
    if limit is not None:
        files = files[:limit]
    return files


def _draw_result(rgb: np.ndarray, detections: list[dict]) -> Image.Image:
    overlay = rgb.astype(np.float32).copy()
    for detection in detections:
        mask = detection["mask"]
        color = np.asarray(COLORS.get(detection["label"], (255, 255, 0)), dtype=np.float32)
        overlay[mask] = overlay[mask] * 0.55 + color * 0.45
    image = Image.fromarray(np.clip(overlay, 0, 255).astype(np.uint8))
    draw = ImageDraw.Draw(image)
    font = _font()
    for detection in detections:
        color = COLORS.get(detection["label"], (255, 255, 0))
        x1, y1, x2, y2 = detection["bbox"]
        draw.rectangle((x1, y1, x2, y2), outline=color, width=3)
        text = f'{detection["label"]} {detection["score"]:.2f} SAM {detection["mask_score"]:.2f}'
        text_box = draw.textbbox((x1, y1), text, font=font)
        text_y = max(0, y1 - (text_box[3] - text_box[1]) - 4)
        draw.rectangle((x1, text_y, min(image.width, text_box[2] + 4), y1), fill=(0, 0, 0))
        draw.text((x1 + 2, text_y), text, fill=color, font=font)
    return image


def main() -> int:
    backend = BACKEND_ROOT
    local_data = backend.parent / "task4_local"
    parser = argparse.ArgumentParser()
    parser.add_argument("--samples", type=Path, default=local_data / "samples")
    parser.add_argument("--results", type=Path, default=local_data / "results" / "full")
    parser.add_argument("--phase", choices=("first", "full"), default="full")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--prompt", choices=("english", "chinese"), default="english")
    parser.add_argument("--box-threshold", type=float, default=0.30)
    parser.add_argument("--text-threshold", type=float, default=0.25)
    args = parser.parse_args()

    files = sorted(
        path
        for path in args.samples.iterdir()
        if path.suffix.lower() in {".jpg", ".jpeg", ".png", ".webp"}
    )
    files = _select_images(files, args.phase, args.limit)
    if not files:
        raise SystemExit(f"No images found in {args.samples}")
    args.results.mkdir(parents=True, exist_ok=True)
    texts = PROMPT_OBJECTS if args.prompt == "english" else CHINESE_PROMPT_OBJECTS

    label_counts = Counter()
    records = []
    successful = 0
    detected_images = 0
    valid_masks = 0
    invalid_masks = 0
    started = time.perf_counter()
    for index, path in enumerate(files, 1):
        image_started = time.perf_counter()
        try:
            rgb = np.asarray(Image.open(path).convert("RGB"))
            detections = analyze_image(
                rgb,
                texts=texts,
                box_threshold=args.box_threshold,
                text_threshold=args.text_threshold,
            )
            _draw_result(rgb, detections).save(args.results / f"{path.stem}_overlay.jpg", quality=92)
            serialized = []
            for detection in detections:
                label_counts[detection["label"]] += 1
                valid_masks += int(detection["mask_valid"])
                invalid_masks += int(not detection["mask_valid"])
                serialized.append(
                    {
                        key: value
                        for key, value in detection.items()
                        if key != "mask"
                    }
                )
            successful += 1
            detected_images += int(bool(detections))
            records.append(
                {
                    "file": path.name,
                    "status": "ok",
                    "detections": serialized,
                    "elapsed_seconds": round(time.perf_counter() - image_started, 3),
                }
            )
            print(f"[{index}/{len(files)}] {path.name}: {len(detections)} detections")
        except Exception as exc:  # 本地批量验证边界：保留单图错误并继续统计其余样本
            if torch.cuda.is_available() and isinstance(exc, torch.OutOfMemoryError):
                torch.cuda.empty_cache()
            records.append(
                {
                    "file": path.name,
                    "status": "error",
                    "error_type": type(exc).__name__,
                    "error": str(exc),
                    "elapsed_seconds": round(time.perf_counter() - image_started, 3),
                }
            )
            print(f"[{index}/{len(files)}] {path.name}: ERROR {type(exc).__name__}: {exc}")

    report = {
        "phase": args.phase,
        "prompt": args.prompt,
        "box_threshold": args.box_threshold,
        "text_threshold": args.text_threshold,
        "runtime": {
            **model_runtime_info(),
            "python": platform.python_version(),
            "torch": torch.__version__,
            "torch_cuda": torch.version.cuda,
            "gpu": torch.cuda.get_device_name(0) if torch.cuda.is_available() else None,
        },
        "summary": {
            "total_images": len(files),
            "successful_images": successful,
            "failed_images": len(files) - successful,
            "detected_images": detected_images,
            "no_detection_images": successful - detected_images,
            "total_detections": sum(label_counts.values()),
            "valid_masks": valid_masks,
            "invalid_masks": invalid_masks,
            "label_counts": dict(sorted(label_counts.items())),
            "elapsed_seconds": round(time.perf_counter() - started, 3),
        },
        "images": records,
    }
    (args.results / "results.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report["summary"], ensure_ascii=False, indent=2))
    return 0 if successful == len(files) else 1


if __name__ == "__main__":
    raise SystemExit(main())
