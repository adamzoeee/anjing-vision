"""Fuse saved SLAM3R registered point maps into a clean display cloud.

This is a read-only reuse of an existing reconstruction: it does not invoke
SLAM3R or Gaussian training.  Each registered point map is converted to a
metric depth image with its recovered camera pose and integrated into a TSDF
volume.  Unlike planar hole filling, TSDF only creates surfaces supported by
the saved multi-view depth observations, so doors and other openings are not
sealed by an invented wall plane.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path

import numpy as np
import open3d as o3d


def _metric_points(raw_points: np.ndarray, alignment: dict) -> np.ndarray:
    rotation = np.asarray(alignment["alignment"]["rotation"], dtype=np.float64)
    floor_z = float(alignment["alignment"]["floor_z_raw_aligned"])
    scale = float(alignment["scale"]["applied"])
    points = np.asarray(raw_points, dtype=np.float64).reshape(-1, 3) @ rotation.T
    points[:, 2] -= floor_z
    return points * scale


def _distributed(cameras: list[dict], max_views: int) -> list[dict]:
    if max_views <= 0 or len(cameras) <= max_views:
        return cameras
    indices = np.linspace(0, len(cameras) - 1, max_views).round().astype(int)
    return [cameras[int(index)] for index in np.unique(indices)]


def fuse(
    work: Path,
    *,
    voxel_m: float = 0.01,
    trunc_m: float = 0.04,
    min_confidence: float = 12.0,
    max_views: int = 0,
    remove_ceiling_margin_m: float = 0.10,
) -> dict:
    work = Path(work)
    preds = work / "slam3r" / "scene" / "preds"
    post = work / "postprocess"
    alignment = json.loads((post / "alignment.json").read_text(encoding="utf-8"))
    cameras = json.loads((work / "gaussian" / "cameras.json").read_text(encoding="utf-8"))
    cameras = _distributed(cameras, max_views)
    point_maps = np.load(preds / "registered_pcds.npy", mmap_mode="r")
    confidences = np.load(preds / "registered_confs.npy", mmap_mode="r")
    images = np.load(preds / "input_imgs.npy", mmap_mode="r")

    volume = o3d.pipelines.integration.ScalableTSDFVolume(
        voxel_length=float(voxel_m),
        sdf_trunc=float(trunc_m),
        color_type=o3d.pipelines.integration.TSDFVolumeColorType.RGB8,
    )
    integrated = 0
    for ordinal, camera in enumerate(cameras, 1):
        frame_id = int(camera["id"])
        raw = np.asarray(point_maps[frame_id])
        conf = np.asarray(confidences[frame_id])
        points = _metric_points(raw, alignment)
        rotation = np.asarray(camera["rotation"], dtype=np.float64)
        center = np.asarray(camera["position"], dtype=np.float64)
        camera_points = (points - center) @ rotation.T
        depth = camera_points[:, 2].reshape(raw.shape[:2]).astype(np.float32)
        valid = np.isfinite(depth) & np.isfinite(conf) & (conf >= min_confidence) & (depth > 0.05)
        depth[~valid] = 0.0

        color = np.asarray(images[frame_id])
        color = np.clip(color, 0, 255).astype(np.uint8)
        rgbd = o3d.geometry.RGBDImage.create_from_color_and_depth(
            o3d.geometry.Image(color),
            o3d.geometry.Image(depth),
            depth_scale=1.0,
            depth_trunc=6.0,
            convert_rgb_to_intensity=False,
        )
        intrinsic = o3d.camera.PinholeCameraIntrinsic(
            int(camera["width"]), int(camera["height"]),
            float(camera["fx"]), float(camera["fy"]),
            float(camera["cx"]), float(camera["cy"]),
        )
        extrinsic = np.eye(4, dtype=np.float64)
        extrinsic[:3, :3] = rotation
        extrinsic[:3, 3] = -rotation @ center
        volume.integrate(rgbd, intrinsic, extrinsic)
        integrated += 1
        if ordinal % 50 == 0 or ordinal == len(cameras):
            print(f"tsdf views={ordinal}/{len(cameras)}", flush=True)

    cloud = volume.extract_point_cloud()
    points = np.asarray(cloud.points)
    colors = np.asarray(cloud.colors)
    extents = alignment.get("extents_m", {})
    room_height = float(extents.get("z", [0.0, alignment["scale"]["target_height_m"]])[1])
    keep = np.isfinite(points).all(axis=1) & (points[:, 2] < room_height - remove_ceiling_margin_m)
    if all(axis in extents for axis in ("x", "y", "z")):
        lower = np.asarray([extents["x"][0] - 0.20, extents["y"][0] - 0.20, -0.12])
        upper = np.asarray([extents["x"][1] + 0.20, extents["y"][1] + 0.20, room_height])
        keep &= np.all((points >= lower) & (points <= upper), axis=1)
    cloud.points = o3d.utility.Vector3dVector(points[keep])
    cloud.colors = o3d.utility.Vector3dVector(colors[keep])

    output = post / "scene_preview_fused_open_top.ply"
    # Legacy writer stores RGB as uchar without the tensor writer's extra
    # 1/255 colour scaling.  The web parser already supports double XYZ.
    o3d.io.write_point_cloud(str(output), cloud, write_ascii=False, compressed=False)
    diagnostics = {
        "method": "registered_pointmaps_pose_constrained_tsdf",
        "source_views": len(cameras),
        "integrated_views": integrated,
        "voxel_m": voxel_m,
        "trunc_m": trunc_m,
        "min_confidence": min_confidence,
        "ceiling_removed_above_m": room_height - remove_ceiling_margin_m,
        "output_points": len(cloud.points),
        "output": output.name,
    }
    (post / "tsdf_fusion_diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return diagnostics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("--voxel", type=float, default=0.01)
    parser.add_argument("--trunc", type=float, default=0.04)
    parser.add_argument("--min-confidence", type=float, default=12.0)
    parser.add_argument("--max-views", type=int, default=0)
    args = parser.parse_args()
    print(json.dumps(fuse(
        args.work, voxel_m=args.voxel, trunc_m=args.trunc,
        min_confidence=args.min_confidence, max_views=args.max_views,
    ), ensure_ascii=False, indent=2))
