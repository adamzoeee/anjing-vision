"""Read-only audit of camera convention and opening-ray wall intersections."""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import open3d as o3d

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from pipeline.semantic import project_points_to_view
from pipeline.semantic_evidence import _merge_openings_from_view_rays, _semantic_camera


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    args = parser.parse_args()
    work = args.work
    points = np.asarray(o3d.io.read_point_cloud(str(work / "postprocess/scene_semantic.ply")).points)
    cameras = json.loads((work / "gaussian/cameras.json").read_text(encoding="utf-8"))
    evidence = json.loads((work / "diagnostics/semantic_evidence.json").read_text(encoding="utf-8"))
    by_id = {int(item.get("id", i)): item for i, item in enumerate(cameras)}
    result = {"direct": [], "transpose": []}
    for view in evidence.get("views", []):
        raw = by_id.get(int(view["view_id"]))
        if raw is None:
            continue
        position = np.asarray(raw["position"], dtype=float)
        rotation = np.asarray(raw["rotation"], dtype=float)
        for mode, R in (("direct", rotation), ("transpose", rotation.T)):
            camera = {
                "K": [[raw["fx"], 0, raw["cx"]], [0, raw["fy"], raw["cy"]], [0, 0, 1]],
                "R": R, "t": -R @ position, "camera_model": "PINHOLE",
                "image_size": [raw["width"], raw["height"]],
            }
            _uv, _depth, valid = project_points_to_view(
                points, camera, image_shape=(int(raw["height"]), int(raw["width"])),
            )
            result[mode].append(int(valid.sum()))
    print(json.dumps({key: {
        "views": len(values), "median_valid": float(np.median(values)),
        "mean_valid": float(np.mean(values)), "nonempty": sum(value > 0 for value in values),
    } for key, values in result.items()}, ensure_ascii=False, indent=2))
    structure = json.loads((work / "postprocess/structure.json").read_text(encoding="utf-8"))
    structure["doors"] = []
    structure["windows"] = []
    records = []
    for view in evidence.get("views", []):
        raw = by_id.get(int(view["view_id"]))
        if raw is not None:
            records.append({
                "view_id": int(view["view_id"]), "camera": _semantic_camera(raw),
                "detections": view.get("detections", []),
            })
    _merge_openings_from_view_rays(structure, records)
    print(json.dumps({"ray_doors": structure.get("doors"), "ray_windows": structure.get("windows")},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
