"""以既有预览点云为不可变基线，用多视角视频点图增量补充低覆盖区域。"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree


ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from scripts.build_open_top_preview import (
    _read_binary_vertices,
    build as build_open_top_preview,
)
from scripts.refuse_registered_points import _empty, _reduce_records


def _write_float_rgb_ply(path: Path, xyz: np.ndarray, rgb: np.ndarray) -> None:
    """写出与原始预览一致的 float RGB，避免 Viewer 中颜色变黑或过曝。"""
    dtype = np.dtype([
        ("x", "<f4"), ("y", "<f4"), ("z", "<f4"),
        ("red", "<f4"), ("green", "<f4"), ("blue", "<f4"),
    ])
    records = np.empty(len(xyz), dtype=dtype)
    records["x"], records["y"], records["z"] = xyz[:, 0], xyz[:, 1], xyz[:, 2]
    rgb = np.clip(rgb, 0.0, 1.0)
    records["red"], records["green"], records["blue"] = rgb[:, 0], rgb[:, 1], rgb[:, 2]
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(records)}\n"
        "property float x\nproperty float y\nproperty float z\n"
        "property float red\nproperty float green\nproperty float blue\nend_header\n"
    ).encode("ascii")
    with path.open("wb") as handle:
        handle.write(header)
        handle.write(records.tobytes())


def refine(
    source_work: Path, target_work: Path, *, voxel: float = 0.01,
    confidence: float = 12.0, frame_stride: int = 1,
) -> dict:
    source_work, target_work = source_work.resolve(), target_work.resolve()
    post = target_work / "postprocess"
    post.mkdir(parents=True, exist_ok=True)
    preds = source_work / "slam3r/scene/preds"
    point_maps = np.load(preds / "registered_pcds.npy", mmap_mode="r")
    confidences = np.load(preds / "registered_confs.npy", mmap_mode="r")
    images = np.load(preds / "input_imgs.npy", mmap_mode="r")

    alignment = json.loads((source_work / "postprocess/alignment.json").read_text(encoding="utf-8"))
    rotation = np.asarray(alignment["alignment"]["rotation"], dtype=np.float64)
    floor_z = float(alignment["alignment"]["floor_z_raw_aligned"])
    scale = float(alignment["scale"]["applied"])
    extents = alignment["extents_m"]
    lo = np.asarray([extents["x"][0], extents["y"][0], -0.03], dtype=float)
    hi = np.asarray([extents["x"][1], extents["y"][1], extents["z"][1] - 0.12], dtype=float)

    base_path = source_work / "postprocess/scene_preview.ply"
    base = o3d.io.read_point_cloud(str(base_path))
    base_xyz = np.asarray(base.points, dtype=np.float64)
    # Open3D 会把 float RGB 当成 0..255 再除一次；直接读取原始字段，
    # 保持旧预览实际使用的 0..1 浮点颜色。
    _, base_records, _ = _read_binary_vertices(base_path)
    base_rgb = np.column_stack([
        base_records["red"], base_records["green"], base_records["blue"],
    ]).astype(np.float64)
    base_tree = cKDTree(base_xyz)
    base_registration = base.voxel_down_sample(0.025)

    origin = lo - np.asarray([0.03, 0.03, 0.03])
    shape = np.ceil((hi - origin) / voxel).astype(np.int64) + 1
    nx, ny = int(shape[0]), int(shape[1])
    global_records = _empty()
    accepted, rejected, candidate_votes = 0, 0, 0
    frame_diagnostics: list[dict] = []
    rng = np.random.default_rng(46)
    block_size = 20

    for block_start in range(0, len(point_maps), block_size):
        block = _empty()
        for frame_id in range(block_start, min(block_start + block_size, len(point_maps)), frame_stride):
            xyz = np.asarray(point_maps[frame_id], dtype=np.float64).reshape(-1, 3)
            conf = np.asarray(confidences[frame_id], dtype=np.float32).reshape(-1)
            rgb = np.asarray(images[frame_id], dtype=np.float32).reshape(-1, 3)
            good = np.isfinite(xyz).all(axis=1) & np.isfinite(conf) & (conf >= confidence)
            xyz, conf, rgb = xyz[good], conf[good], rgb[good]
            if len(xyz) < 1000:
                rejected += 1
                continue
            xyz = xyz @ rotation.T
            xyz[:, 2] -= floor_z
            xyz *= scale
            inside = np.all((xyz >= lo) & (xyz <= hi), axis=1)
            xyz, conf, rgb = xyz[inside], conf[inside], rgb[inside]
            if len(xyz) < 1000:
                rejected += 1
                continue

            # 快速筛查：该帧必须有足够部分与45基线重合，才允许做ICP补洞。
            sample_ids = rng.choice(len(xyz), min(4000, len(xyz)), replace=False)
            screen_dist, _ = base_tree.query(xyz[sample_ids], k=1, workers=-1)
            screen_overlap = float(np.mean(screen_dist <= 0.06))
            if screen_overlap < 0.18:
                rejected += 1
                frame_diagnostics.append({"frame": frame_id, "accepted": False, "overlap": screen_overlap})
                continue

            source = o3d.geometry.PointCloud()
            source.points = o3d.utility.Vector3dVector(xyz)
            source = source.voxel_down_sample(0.025)
            icp = o3d.pipelines.registration.registration_icp(
                source, base_registration, 0.065, np.eye(4),
                o3d.pipelines.registration.TransformationEstimationPointToPoint(),
                o3d.pipelines.registration.ICPConvergenceCriteria(max_iteration=18),
            )
            if float(icp.fitness) < 0.22 or float(icp.inlier_rmse) > 0.042:
                rejected += 1
                frame_diagnostics.append({
                    "frame": frame_id, "accepted": False, "overlap": screen_overlap,
                    "fitness": float(icp.fitness), "rmse": float(icp.inlier_rmse),
                })
                continue
            transform = np.asarray(icp.transformation)
            xyz = xyz @ transform[:3, :3].T + transform[:3, 3]
            inside = np.all((xyz >= lo) & (xyz <= hi), axis=1)
            xyz, conf, rgb = xyz[inside], conf[inside], rgb[inside]
            if not len(xyz):
                rejected += 1
                continue

            # 45已有点不替换；只对缺失/低覆盖区投票。
            existing_dist, _ = base_tree.query(xyz, k=1, workers=-1)
            missing = existing_dist >= 0.018
            xyz, conf, rgb = xyz[missing], conf[missing], rgb[missing]
            if len(xyz) < 50:
                accepted += 1
                continue
            ijk = np.floor((xyz - origin) / voxel).astype(np.int64)
            hashes = ijk[:, 0] + nx * (ijk[:, 1] + ny * ijk[:, 2])
            order = np.lexsort((-conf, hashes))
            sorted_hash = hashes[order]
            first = np.r_[0, np.flatnonzero(sorted_hash[1:] != sorted_hash[:-1]) + 1]
            chosen = order[first]
            hashes, xyz, rgb, conf = hashes[chosen], xyz[chosen], rgb[chosen], conf[chosen]
            candidate_votes += len(hashes)
            weight = np.log1p(np.maximum(conf, 0.0)).astype(np.float64)
            records = (
                hashes.astype(np.int64), xyz * weight[:, None], rgb.astype(np.float64) * weight[:, None],
                weight, np.ones(len(hashes), np.int32), np.zeros(len(hashes), np.int16), conf,
            )
            block = _reduce_records(*[np.concatenate([left, right]) for left, right in zip(block, records)])
            accepted += 1
            frame_diagnostics.append({
                "frame": frame_id, "accepted": True, "overlap": screen_overlap,
                "fitness": float(icp.fitness), "rmse": float(icp.inlier_rmse),
                "new_voxels": int(len(hashes)),
            })
        if len(block[0]):
            block = (*block[:5], np.ones(len(block[0]), np.int16), block[6])
            global_records = _reduce_records(
                *[np.concatenate([left, right]) for left, right in zip(global_records, block)]
            )
        print(f"incremental frames={min(block_start + block_size, len(point_maps))}/{len(point_maps)} accepted={accepted} voxels={len(global_records[0])}", flush=True)

    hashes, xyz_sum, rgb_sum, weight_sum, frame_count, block_count, max_conf = global_records
    # 新点必须由至少3帧且至少2个时间块共同确认；单帧再清晰也不能补入。
    keep = (frame_count >= 3) & (block_count >= 2)
    additions = xyz_sum[keep] / weight_sum[keep, None]
    addition_rgb = np.clip(rgb_sum[keep] / weight_sum[keep, None], 0, 255) / 255.0
    raw_output = post / "scene_preview_incremental_raw.ply"
    _write_float_rgb_ply(
        raw_output,
        np.vstack([base_xyz, additions]),
        np.vstack([base_rgb, addition_rgb]),
    )
    final_output = post / "scene_preview_incremental_candidate.ply"
    open_top = build_open_top_preview(raw_output, final_output)
    diagnostics = {
        "status": "candidate_ready", "method": "scan45_locked_multiview_incremental",
        "base_scan": source_work.name, "base_points_preserved": int(len(base_xyz)),
        "source_frames": int(len(point_maps)), "accepted_frames": accepted, "rejected_frames": rejected,
        "candidate_votes": candidate_votes, "consensus_additions": int(len(additions)),
        "voxel_m": voxel, "min_confidence": confidence,
        "gates": {"min_overlap": 0.18, "min_icp_fitness": 0.22, "max_icp_rmse_m": 0.042,
                  "min_frames": 3, "min_temporal_blocks": 2},
        "open_top": open_top, "promotion_required": True,
        "frame_diagnostics": frame_diagnostics,
    }
    (post / "incremental_diagnostics.json").write_text(
        json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    return diagnostics


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("source_work", type=Path)
    parser.add_argument("target_work", type=Path)
    parser.add_argument("--voxel", type=float, default=0.01)
    parser.add_argument("--confidence", type=float, default=12.0)
    args = parser.parse_args()
    result = refine(args.source_work, args.target_work, voxel=args.voxel, confidence=args.confidence)
    print(json.dumps({key: value for key, value in result.items() if key != "frame_diagnostics"}, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
