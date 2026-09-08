"""Read-only window-wall axis audit from known visible SLAM3R frames."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[2]
WORK = ROOT / "backend/data/work/45"
PREDS = WORK / "slam3r/scene/preds"
OUT = ROOT / "backend/data/work/46/postprocess/window_axis_registered_frame_audit.json"

# Opaque window frame / adjacent wall pixels in exported 224x224 frames.
RECTS = {
    145: {
        "top_frame": (34, 50, 8, 120),
        "left_frame": (42, 205, 8, 31),
        "right_frame": (38, 205, 108, 132),
        "right_wall": (45, 190, 136, 210),
        "bottom_frame": (190, 215, 20, 120),
    },
    152: {
        "top_frame": (20, 42, 45, 175),
        "left_frame": (35, 195, 42, 68),
        "right_frame": (35, 195, 158, 182),
        "right_wall": (45, 180, 184, 220),
        "bottom_frame": (185, 214, 55, 170),
    },
}


def metric(raw: np.ndarray, rotation: np.ndarray, floor: float, scale: float) -> np.ndarray:
    xyz = raw @ rotation.T
    xyz[..., 2] -= floor
    return xyz * scale


def describe(xyz: np.ndarray, confidence: np.ndarray) -> dict:
    valid = np.isfinite(xyz).all(axis=1) & np.isfinite(confidence) & (confidence >= 1.2)
    xyz = xyz[valid]
    if not len(xyz):
        return {"count": 0}
    q = np.quantile(xyz, [.1, .5, .9], axis=0)
    return {
        "count": int(len(xyz)),
        "p10": q[0].tolist(),
        "median": q[1].tolist(),
        "p90": q[2].tolist(),
        "spread_p90_p10": (q[2] - q[0]).tolist(),
    }


def main() -> None:
    alignment = json.loads((WORK / "postprocess/alignment.json").read_text(encoding="utf-8"))
    rotation = np.asarray(alignment["alignment"]["rotation"], dtype=np.float64)
    floor = float(alignment["alignment"]["floor_z_raw_aligned"])
    scale = float(alignment["scale"]["applied"])
    maps = np.load(PREDS / "registered_pcds.npy", mmap_mode="r")
    confs = np.load(PREDS / "registered_confs.npy", mmap_mode="r")
    report = {}
    for frame, rectangles in RECTS.items():
        pointmap = metric(np.asarray(maps[frame], dtype=np.float64), rotation, floor, scale)
        confidence = np.asarray(confs[frame], dtype=np.float64)
        report[str(frame)] = {}
        for name, (y0, y1, x0, x1) in rectangles.items():
            report[str(frame)][name] = describe(
                pointmap[y0:y1, x0:x1].reshape(-1, 3),
                confidence[y0:y1, x0:x1].reshape(-1),
            )
    OUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
