"""Finalize scan 46 without touching accepted geometry outside the reviewed scope.

The accepted six-region candidate already contains the real registered/video-supported
repairs.  This final step only removes the interior ceiling so the room can be viewed
from above.  Points near the four room walls are retained, including their top strips.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--alignment", type=Path, required=True)
    parser.add_argument("--ceiling-start", type=float, default=2.35)
    parser.add_argument("--wall-keep-band", type=float, default=0.08)
    parser.add_argument("--x-min", type=float)
    parser.add_argument("--x-max", type=float)
    parser.add_argument("--y-min", type=float)
    parser.add_argument("--y-max", type=float)
    args = parser.parse_args()

    cloud = o3d.io.read_point_cloud(str(args.input))
    points = np.asarray(cloud.points)
    colors = np.asarray(cloud.colors)
    if len(points) == 0:
        raise RuntimeError(f"Empty input cloud: {args.input}")

    alignment = json.loads(args.alignment.read_text(encoding="utf-8"))
    room = alignment.get("room_bounds") or alignment.get("bounds") or {}
    if all(k in room for k in ("x_min", "x_max", "y_min", "y_max")):
        x_min, x_max = float(room["x_min"]), float(room["x_max"])
        y_min, y_max = float(room["y_min"]), float(room["y_max"])
    else:
        # Stable scan-45/46 metric room bounds recovered during alignment.
        x_min, x_max = -0.4844, 1.9621
        y_min, y_max = -0.5010, 1.3490
    # The structure model can use a manually expanded coordinate envelope;
    # that envelope is not necessarily the physical wall planes of the raw
    # point cloud.  Reviewed metric wall coordinates may therefore override it.
    x_min = args.x_min if args.x_min is not None else x_min
    x_max = args.x_max if args.x_max is not None else x_max
    y_min = args.y_min if args.y_min is not None else y_min
    y_max = args.y_max if args.y_max is not None else y_max

    wall_distance = np.minimum.reduce(
        (
            np.abs(points[:, 0] - x_min),
            np.abs(points[:, 0] - x_max),
            np.abs(points[:, 1] - y_min),
            np.abs(points[:, 1] - y_max),
        )
    )
    interior_ceiling = (points[:, 2] > args.ceiling_start) & (
        wall_distance > args.wall_keep_band
    )
    keep = ~interior_ceiling

    result = o3d.geometry.PointCloud()
    result.points = o3d.utility.Vector3dVector(points[keep])
    if len(colors) == len(points):
        result.colors = o3d.utility.Vector3dVector(colors[keep])
    args.output.parent.mkdir(parents=True, exist_ok=True)
    if not o3d.io.write_point_cloud(str(args.output), result, write_ascii=False):
        raise RuntimeError(f"Failed writing {args.output}")

    report = {
        "input": str(args.input),
        "output": str(args.output),
        "input_points": int(len(points)),
        "output_points": int(np.count_nonzero(keep)),
        "removed_interior_ceiling_points": int(np.count_nonzero(interior_ceiling)),
        "ceiling_start_m": args.ceiling_start,
        "wall_keep_band_m": args.wall_keep_band,
        "room_bounds": {"x": [x_min, x_max], "y": [y_min, y_max]},
        "policy": "remove interior ceiling only; preserve wall-top strips and every other point",
    }
    args.output.with_suffix(".json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
