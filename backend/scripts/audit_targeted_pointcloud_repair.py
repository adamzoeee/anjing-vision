"""Audit that a point-cloud repair changes only explicitly allowed ROIs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d


def load_cloud(path: Path) -> tuple[np.ndarray, np.ndarray]:
    cloud = o3d.io.read_point_cloud(str(path))
    return np.asarray(cloud.points), np.asarray(cloud.colors)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline", type=Path)
    parser.add_argument("candidate", type=Path)
    parser.add_argument("regions", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--immutable-prefix-points", type=int, default=0)
    args = parser.parse_args()

    spec = json.loads(args.regions.read_text(encoding="utf-8"))
    bounds = [
        (np.asarray(item["lower"], float), np.asarray(item["upper"], float))
        for item in spec["regions"].values()
    ]
    base_xyz, base_rgb = load_cloud(args.baseline)
    out_xyz, out_rgb = load_cloud(args.candidate)

    immutable = min(args.immutable_prefix_points, len(base_xyz))
    inside_any = np.zeros(len(base_xyz), dtype=bool)
    for lower, upper in bounds:
        inside_any |= np.all((base_xyz >= lower) & (base_xyz <= upper), axis=1)
    keep = np.arange(len(base_xyz)) < immutable
    keep |= ~inside_any
    expected_xyz = base_xyz[keep]
    expected_rgb = base_rgb[keep]

    prefix_ok = len(out_xyz) >= len(expected_xyz)
    if prefix_ok:
        prefix_ok = bool(
            np.array_equal(out_xyz[: len(expected_xyz)], expected_xyz)
            and np.array_equal(out_rgb[: len(expected_rgb)], expected_rgb)
        )

    report = {
        "baseline_points": int(len(base_xyz)),
        "candidate_points": int(len(out_xyz)),
        "immutable_prefix_points": int(immutable),
        "removed_mutable_points_inside_rois": int((~keep).sum()),
        "preserved_existing_points": int(keep.sum()),
        "new_points": int(len(out_xyz) - keep.sum()),
        "preserved_points_are_exact_candidate_prefix": prefix_ok,
        "outside_rois_unchanged": prefix_ok,
        "pass": prefix_ok and len(out_xyz) >= len(expected_xyz),
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if not report["pass"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
