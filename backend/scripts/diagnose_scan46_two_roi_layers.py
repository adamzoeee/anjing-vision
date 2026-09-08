"""Read-only density audit for scan 46's two protected repair ROIs."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d


REGIONS = {
    "door_floor": (np.array([-.5144042583, .7989766623, -.04]), np.array([.3155957417, 1.3889766623, .16])),
    # The accepted Figure-4 geometry places the physical window on y-max.
    "window_wall": (np.array([-.20, 1.275, .82]), np.array([1.52, 1.505, 2.28])),
}


def counts(xyz: np.ndarray, voxel: float = .012) -> dict[str, dict[str, int]]:
    result = {}
    for name, (lo, hi) in REGIONS.items():
        selected = xyz[np.all((xyz >= lo) & (xyz <= hi), axis=1)]
        occupied = len(np.unique(np.floor((selected - lo) / voxel).astype(np.int32), axis=0)) if len(selected) else 0
        result[name] = {"points": int(len(selected)), "occupied_12mm_voxels": int(occupied)}
    return result


def surface_stats(xyz: np.ndarray, name: str) -> dict:
    if not len(xyz):
        return {}
    axis = 2 if name == "door_floor" else 1
    values = xyz[:, axis]
    return {
        "surface_axis": "z" if axis == 2 else "y",
        "quantiles": [float(v) for v in np.quantile(values, [0, .01, .05, .25, .5, .75, .95, .99, 1])],
    }


def read_ply(path: Path) -> np.ndarray:
    return np.asarray(o3d.io.read_point_cloud(str(path)).points, dtype=np.float64)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("scan45", type=Path)
    parser.add_argument("scan46", type=Path)
    args = parser.parse_args()

    alignment = json.loads((args.scan45 / "postprocess/alignment.json").read_text(encoding="utf-8"))
    rotation = np.asarray(alignment["alignment"]["rotation"], dtype=np.float64)
    floor_z = float(alignment["alignment"]["floor_z_raw_aligned"])
    scale = float(alignment["scale"]["applied"])
    preds = args.scan45 / "slam3r/scene/preds"
    maps = np.load(preds / "registered_pcds.npy", mmap_mode="r")
    confs = np.load(preds / "registered_confs.npy", mmap_mode="r")

    raw_by_conf = {}
    for threshold in (0.0, 2.0, 4.0, 10.0):
        region_points = {name: [] for name in REGIONS}
        for frame in range(len(maps)):
            xyz = np.asarray(maps[frame], dtype=np.float64).reshape(-1, 3)
            conf = np.asarray(confs[frame], dtype=np.float32).reshape(-1)
            valid = np.isfinite(xyz).all(axis=1) & np.isfinite(conf) & (conf >= threshold)
            xyz = xyz[valid] @ rotation.T
            xyz[:, 2] = (xyz[:, 2] - floor_z) * scale
            xyz[:, :2] *= scale
            for name, (lo, hi) in REGIONS.items():
                hit = xyz[np.all((xyz >= lo) & (xyz <= hi), axis=1)]
                if len(hit):
                    region_points[name].append(hit)
        raw_by_conf[str(threshold)] = {}
        for name, parts in region_points.items():
            selected = np.concatenate(parts) if parts else np.empty((0, 3))
            raw_by_conf[str(threshold)][name] = {
                **counts(selected)[name],
                **surface_stats(selected, name),
            }

    post = args.scan46 / "postprocess"
    layers = {}
    for filename in (
        "scene_aligned.ply",
        "scene_preview_fused_open_top_0855.ply",
        "scene_preview_local_surface_repair_candidate.ply",
    ):
        path = post / filename
        if path.exists():
            xyz = read_ply(path)
            layer_regions = counts(xyz)
            for name, (lo, hi) in REGIONS.items():
                selected = xyz[np.all((xyz >= lo) & (xyz <= hi), axis=1)]
                layer_regions[name].update(surface_stats(selected, name))
            layers[filename] = {"total_points": int(len(xyz)), "regions": layer_regions}

    print(json.dumps({"regions": {k: {"lower": a.tolist(), "upper": b.tolist()} for k, (a, b) in REGIONS.items()},
                      "registered_maps_by_confidence": raw_by_conf, "ply_layers": layers},
                     ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
