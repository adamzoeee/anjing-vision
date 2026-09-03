"""Re-fuse existing SLAM3R registered point maps without rerunning the model.

The stock exporter globally samples ``num_points_save`` points from all frames.
For a long scan this can discard most observations and leave large spatial holes.
This script keeps one confident observation per spatial voxel per frame, then
retains voxels supported by multiple frames/temporal blocks.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import open3d as o3d

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def _floor_z(raw_ply: Path, rotation: np.ndarray) -> float:
    cloud = o3d.io.read_point_cloud(str(raw_ply))
    points = np.asarray(cloud.points, dtype=np.float64) @ rotation.T
    low = points[points[:, 2] < np.percentile(points[:, 2], 35)]
    design = np.column_stack([low[:, :2], np.ones(len(low))])
    target = -low[:, 2]
    coef, *_ = np.linalg.lstsq(design, target, rcond=None)
    residual = np.abs(design @ coef - target)
    keep = residual < np.percentile(residual, 90)
    coef, *_ = np.linalg.lstsq(design[keep], target[keep], rcond=None)
    return float(-coef[2])


def _reduce_records(
    hashes: np.ndarray,
    xyz_sum: np.ndarray,
    rgb_sum: np.ndarray,
    weight_sum: np.ndarray,
    frame_count: np.ndarray,
    block_count: np.ndarray,
    max_conf: np.ndarray,
) -> tuple[np.ndarray, ...]:
    if len(hashes) == 0:
        return hashes, xyz_sum, rgb_sum, weight_sum, frame_count, block_count, max_conf
    order = np.argsort(hashes, kind="mergesort")
    hashes = hashes[order]
    starts = np.r_[0, np.flatnonzero(hashes[1:] != hashes[:-1]) + 1]
    return (
        hashes[starts],
        np.add.reduceat(xyz_sum[order], starts, axis=0),
        np.add.reduceat(rgb_sum[order], starts, axis=0),
        np.add.reduceat(weight_sum[order], starts),
        np.add.reduceat(frame_count[order], starts),
        np.add.reduceat(block_count[order], starts),
        np.maximum.reduceat(max_conf[order], starts),
    )


def _empty() -> tuple[np.ndarray, ...]:
    return (
        np.empty(0, np.int64), np.empty((0, 3), np.float64),
        np.empty((0, 3), np.float64), np.empty(0, np.float64),
        np.empty(0, np.int32), np.empty(0, np.int16), np.empty(0, np.float32),
    )


def refuse(
    work: Path, *, voxel: float = 0.0075, min_conf: float = 5.0,
    high_conf: float = 12.0, block_size: int = 15,
) -> dict:
    preds = work / "slam3r/scene/preds"
    point_maps = np.load(preds / "registered_pcds.npy", mmap_mode="r")
    confidences = np.load(preds / "registered_confs.npy", mmap_mode="r")
    images = np.load(preds / "input_imgs.npy", mmap_mode="r")
    alignment_path = work / "postprocess/alignment.json"
    alignment = json.loads(alignment_path.read_text(encoding="utf-8"))
    rotation = np.asarray(alignment["alignment"]["rotation"], dtype=np.float64)
    scale = float(alignment["scale"]["applied"])
    raw_ply = sorted(work.rglob("*_recon.ply"))[-1]
    floor_z = float(alignment["alignment"].get("floor_z_raw_aligned", _floor_z(raw_ply, rotation)))
    # 融合脚本只能读取坐标契约，绝不能回写 alignment.json。旧实现会用一次
    # 近似平面拟合覆盖训练时的精确地板基准，导致后续点云整体高度缩短。

    extents = alignment["extents_m"]
    room_lo = np.asarray([extents["x"][0], extents["y"][0], extents["z"][0]], dtype=float)
    room_hi = np.asarray([extents["x"][1], extents["y"][1], extents["z"][1]], dtype=float)
    origin = room_lo - np.asarray([0.25, 0.25, 0.15])
    limit = room_hi + np.asarray([0.25, 0.25, 0.20])
    grid_shape = np.ceil((limit - origin) / voxel).astype(np.int64) + 1
    nx, ny = int(grid_shape[0]), int(grid_shape[1])

    global_records = _empty()
    input_valid = 0
    per_frame_unique = 0
    for block_start in range(0, len(point_maps), block_size):
        block = _empty()
        for frame_id in range(block_start, min(block_start + block_size, len(point_maps))):
            xyz = np.asarray(point_maps[frame_id], dtype=np.float64).reshape(-1, 3)
            conf = np.asarray(confidences[frame_id], dtype=np.float32).reshape(-1)
            rgb = np.asarray(images[frame_id], dtype=np.float32).reshape(-1, 3)
            good = np.isfinite(xyz).all(axis=1) & np.isfinite(conf) & (conf >= min_conf)
            if not np.any(good):
                continue
            xyz, conf, rgb = xyz[good], conf[good], rgb[good]
            xyz = xyz @ rotation.T
            xyz[:, 2] -= floor_z
            xyz *= scale
            inside = np.all((xyz >= origin) & (xyz <= limit), axis=1)
            xyz, conf, rgb = xyz[inside], conf[inside], rgb[inside]
            input_valid += len(xyz)
            if not len(xyz):
                continue
            ijk = np.floor((xyz - origin) / voxel).astype(np.int64)
            hashes = ijk[:, 0] + nx * (ijk[:, 1] + ny * ijk[:, 2])
            # One vote per frame/voxel: keep the highest-confidence observation.
            order = np.lexsort((-conf, hashes))
            sorted_hash = hashes[order]
            first = np.r_[0, np.flatnonzero(sorted_hash[1:] != sorted_hash[:-1]) + 1]
            chosen = order[first]
            hashes, xyz, rgb, conf = hashes[chosen], xyz[chosen], rgb[chosen], conf[chosen]
            per_frame_unique += len(hashes)
            weight = np.log1p(np.maximum(conf, 0.0)).astype(np.float64)
            frame_records = (
                hashes.astype(np.int64), xyz * weight[:, None], rgb.astype(np.float64) * weight[:, None],
                weight, np.ones(len(hashes), np.int32), np.zeros(len(hashes), np.int16), conf,
            )
            block = _reduce_records(*[np.concatenate([left, right]) for left, right in zip(block, frame_records)])
        if len(block[0]):
            # Every voxel present in this temporal block contributes one diversity vote.
            block = (*block[:5], np.ones(len(block[0]), np.int16), block[6])
            global_records = _reduce_records(
                *[np.concatenate([left, right]) for left, right in zip(global_records, block)]
            )
        print(f"refuse frames={min(block_start + block_size, len(point_maps))}/{len(point_maps)} voxels={len(global_records[0])}", flush=True)

    hashes, xyz_sum, rgb_sum, weight_sum, frame_count, block_count, max_conf = global_records
    # 官方最终导出会从所有置信点中全局抽样到 num_points_save，长视频里即使
    # 是高置信真实点也可能被随机丢掉。这里保留所有高置信体素；较低置信点
    # 只有跨时间块重复观测才可补入，避免为了补洞引入单帧飞点。
    keep = (max_conf >= high_conf) | (block_count >= 2) | (frame_count >= 4)
    xyz = xyz_sum[keep] / weight_sum[keep, None]
    rgb = np.clip(rgb_sum[keep] / weight_sum[keep, None], 0, 255) / 255.0
    frame_count, block_count, max_conf = frame_count[keep], block_count[keep], max_conf[keep]

    cloud = o3d.geometry.PointCloud()
    cloud.points = o3d.utility.Vector3dVector(xyz)
    cloud.colors = o3d.utility.Vector3dVector(rgb)
    output = work / "postprocess/scene_observation_fused.ply"
    preview = work / "postprocess/scene_observation_fused.ply"
    o3d.t.io.write_point_cloud(str(output), o3d.t.geometry.PointCloud.from_legacy(cloud))
    diagnostics = {
        "method": "registered_maps_spatial_voxel_multiview_consensus",
        "source_frames": int(len(point_maps)), "source_pixels": int(np.prod(point_maps.shape[:3])),
        "valid_observations": int(input_valid), "per_frame_unique_votes": int(per_frame_unique),
        "voxel_m": voxel, "min_input_confidence": min_conf,
        "high_confidence_keep": high_conf,
        "output_points": int(len(xyz)),
        "support": {
            "median_frames": float(np.median(frame_count)) if len(frame_count) else 0.0,
            "median_temporal_blocks": float(np.median(block_count)) if len(block_count) else 0.0,
            "median_max_confidence": float(np.median(max_conf)) if len(max_conf) else 0.0,
        },
        "outputs": {"aligned": output.name, "preview": preview.name},
    }
    (work / "postprocess/refusion_diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return diagnostics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("work", type=Path)
    parser.add_argument("--voxel", type=float, default=0.0075)
    parser.add_argument("--min-conf", type=float, default=5.0)
    parser.add_argument("--high-conf", type=float, default=12.0)
    args = parser.parse_args()
    print(json.dumps(refuse(
        args.work, voxel=args.voxel, min_conf=args.min_conf, high_conf=args.high_conf,
    ), ensure_ascii=False, indent=2))
