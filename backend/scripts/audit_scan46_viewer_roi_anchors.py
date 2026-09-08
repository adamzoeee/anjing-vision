"""Audit scan-46 ROI coordinates by projecting the locked Figure-4 cloud.

This script is intentionally read-only.  It uses frames where the window is
visibly present and reports the metric 3-D points already seen at selected
image rectangles.  The result decides the wall axis before any repair is
allowed to run.
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import open3d as o3d

from fuse_scan46_buchong2_four_regions import render_metric_pointmap


ROOT = Path(__file__).resolve().parents[2]
POST = ROOT / "backend/data/work/46/postprocess"
BASELINE = POST / "scene_preview_local_surface_repair_candidate.ply"
CAMERAS = ROOT / "backend/data/work/45/gaussian/cameras.json"
OUTPUT = POST / "viewer_roi_anchor_audit.json"


# Rectangles are in the 224x224 SLAM3R input frames exported by
# export_registered_anchor_frames.py.  They deliberately sample opaque frame
# and adjacent wall, not the transparent glass interior.
WINDOW_RECTS = {
    145: {
        "top_frame": (18, 30, 32, 119),
        "left_frame": (28, 202, 14, 31),
        "right_frame": (30, 202, 118, 134),
        "bottom_frame": (196, 216, 24, 124),
        "wall_left": (45, 184, 0, 13),
        "wall_right": (45, 184, 136, 158),
    },
    152: {
        "top_frame": (12, 30, 58, 165),
        "left_frame": (22, 194, 48, 67),
        "right_frame": (22, 194, 158, 177),
        "bottom_frame": (190, 214, 58, 166),
        "wall_left": (50, 180, 25, 46),
        "wall_right": (50, 180, 180, 206),
    },
}


def stats(points: np.ndarray) -> dict:
    points = points[np.all(np.isfinite(points), axis=1)]
    if not len(points):
        return {"count": 0}
    return {
        "count": int(len(points)),
        "median": np.median(points, axis=0).tolist(),
        "p10": np.quantile(points, .10, axis=0).tolist(),
        "p90": np.quantile(points, .90, axis=0).tolist(),
    }


def main() -> None:
    cloud = o3d.io.read_point_cloud(str(BASELINE))
    points = np.asarray(cloud.points, np.float64)
    cameras = {int(camera["id"]): camera for camera in json.loads(CAMERAS.read_text(encoding="utf-8"))}
    frames = {}
    for frame_id, rectangles in WINDOW_RECTS.items():
        camera = cameras.get(frame_id)
        if camera is None:
            frames[str(frame_id)] = {"error": "camera_missing"}
            continue
        pointmap = render_metric_pointmap(points, camera)
        frame_result = {}
        for name, (y0, y1, x0, x1) in rectangles.items():
            frame_result[name] = stats(pointmap[y0:y1, x0:x1].reshape(-1, 3))
        frames[str(frame_id)] = frame_result

    # Floor-height audit at the locked baseline's entrance ROI.  Report modes
    # as quantiles rather than fitting or moving any geometry.
    floor_roi = (
        (points[:, 0] >= -.52) & (points[:, 0] <= .55)
        & (points[:, 1] >= .55) & (points[:, 1] <= 1.40)
        & (points[:, 2] >= -.12) & (points[:, 2] <= .25)
    )
    report = {
        "baseline": str(BASELINE.resolve()),
        "window_frame_projection": frames,
        "entrance_floor_baseline": stats(points[floor_roi]),
        "note": "Read-only coordinate audit; no point cloud was modified.",
    }
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
