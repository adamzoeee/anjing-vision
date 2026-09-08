"""Read-only audit of adjacent pre/post anchors for the scan-46 floor clip."""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from fuse_scan46_buchong2_four_regions import metric, robust_similarity


ROOT = Path(__file__).resolve().parents[2]
BASE = ROOT / "backend/data/work/45"
WORK = ROOT / "backend/data/work/46"
CLIP = WORK / "supplement_buchong2_clip3"
OUT = WORK / "postprocess/floor_segmented_anchor_audit.json"


def estimate(items: list, new_points: np.ndarray, new_conf: np.ndarray,
             base_points: np.ndarray, base_conf: np.ndarray, metric_scale: float) -> tuple:
    rng = np.random.default_rng(463)
    src_parts, dst_parts = [], []
    for item in items:
        ni, bi = int(item["output_index"]), int(item["baseline_index"])
        src = np.asarray(new_points[ni], np.float64).reshape(-1, 3)
        dst = np.asarray(base_points[bi], np.float64).reshape(-1, 3)
        conf = np.minimum(np.asarray(new_conf[ni]).reshape(-1), np.asarray(base_conf[bi]).reshape(-1))
        valid = np.isfinite(src).all(1) & np.isfinite(dst).all(1) & (conf >= 12)
        ids = np.flatnonzero(valid)
        if len(ids) > 3000:
            ids = rng.choice(ids, 3000, replace=False)
        src_parts.append(src[ids]); dst_parts.append(dst[ids])
    source, target = np.concatenate(src_parts), np.concatenate(dst_parts)
    scale, rotation, translation, residual = robust_similarity(source, target, iterations=8)
    return scale, rotation, translation, {
        "pairs": len(source), "scale": float(scale),
        "median_error_m": float(np.median(residual) * metric_scale),
        "p90_error_m": float(np.quantile(residual, .9) * metric_scale),
    }


def main() -> None:
    mapping = json.loads((CLIP / "supplement_mapping.json").read_text(encoding="utf-8"))["mapping"]
    supplements = [x for x in mapping if x["kind"] == "supplement"]
    anchors = [x for x in mapping if x["kind"] == "anchor"]
    first, last = min(x["output_index"] for x in supplements), max(x["output_index"] for x in supplements)
    pre = [x for x in anchors if x["output_index"] < first][-7:]
    post = [x for x in anchors if x["output_index"] > last][:7]
    base_dir = BASE / "slam3r/scene/preds"
    new_dir = CLIP / "slam3r/scene/preds"
    base_points = np.load(base_dir / "registered_pcds.npy", mmap_mode="r")
    base_conf = np.load(base_dir / "registered_confs.npy", mmap_mode="r")
    new_points = np.load(new_dir / "registered_pcds.npy", mmap_mode="r")
    new_conf = np.load(new_dir / "registered_confs.npy", mmap_mode="r")
    alignment = json.loads((WORK / "postprocess/alignment.json").read_text(encoding="utf-8"))
    metric_scale = float(alignment["scale"]["applied"])
    pre_t = estimate(pre, new_points, new_conf, base_points, base_conf, metric_scale)
    post_t = estimate(post, new_points, new_conf, base_points, base_conf, metric_scale)

    counts = {"pre": 0, "post": 0}
    bounds = {"pre": [np.full(3, np.inf), np.full(3, -np.inf)], "post": [np.full(3, np.inf), np.full(3, -np.inf)]}
    room_low_parts = {"pre": [], "post": []}
    all_parts = {"pre": [], "post": []}
    for label, transform in (("pre", pre_t[:3]), ("post", post_t[:3])):
        scale, rotation, translation = transform
        for item in supplements:
            frame = int(item["output_index"])
            raw = np.asarray(new_points[frame], np.float64).reshape(-1, 3)
            conf = np.asarray(new_conf[frame]).reshape(-1)
            valid = np.isfinite(raw).all(1) & np.isfinite(conf) & (conf >= 8)
            xyz = metric(scale * (raw[valid] @ rotation.T) + translation, alignment)
            all_parts[label].append(xyz[::50])
            in_room = (
                (xyz[:, 0] >= -.55) & (xyz[:, 0] <= 2.05)
                & (xyz[:, 1] >= -.55) & (xyz[:, 1] <= 1.40)
                & (xyz[:, 2] >= -.10) & (xyz[:, 2] <= .30)
            )
            if np.any(in_room):
                room_low_parts[label].append(xyz[in_room][::20])
            keep = (
                (xyz[:, 0] >= -.52) & (xyz[:, 0] <= .55)
                & (xyz[:, 1] >= .55) & (xyz[:, 1] <= 1.40)
                & (xyz[:, 2] >= -.04) & (xyz[:, 2] <= .05)
            )
            if np.any(keep):
                p = xyz[keep]
                counts[label] += len(p)
                bounds[label][0] = np.minimum(bounds[label][0], p.min(0))
                bounds[label][1] = np.maximum(bounds[label][1], p.max(0))
    room_low = {key: np.concatenate(value) if value else np.empty((0, 3)) for key, value in room_low_parts.items()}
    all_points = {key: np.concatenate(value) if value else np.empty((0, 3)) for key, value in all_parts.items()}
    payload = {
        "pre_anchor_base_frames": [x["baseline_index"] for x in pre],
        "post_anchor_base_frames": [x["baseline_index"] for x in post],
        "pre_transform": pre_t[3], "post_transform": post_t[3],
        "floor_points_by_transform": counts,
        "floor_bounds_by_transform": {
            key: [value[0].tolist(), value[1].tolist()] if np.isfinite(value[0]).all() else None
            for key, value in bounds.items()
        },
        "low_surface_summary": {
            key: {
                "points_sampled": int(len(value)),
                "xyz_p01": np.quantile(value, .01, axis=0).tolist() if len(value) else None,
                "xyz_p50": np.quantile(value, .50, axis=0).tolist() if len(value) else None,
                "xyz_p99": np.quantile(value, .99, axis=0).tolist() if len(value) else None,
            }
            for key, value in room_low.items()
        },
        "all_supplement_summary": {
            key: {
                "points_sampled": int(len(value)),
                "xyz_p01": np.quantile(value, .01, axis=0).tolist() if len(value) else None,
                "xyz_p50": np.quantile(value, .50, axis=0).tolist() if len(value) else None,
                "xyz_p99": np.quantile(value, .99, axis=0).tolist() if len(value) else None,
            }
            for key, value in all_points.items()
        },
    }
    OUT.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(payload, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
