"""把通过旧帧锚定的补拍 SLAM3R 点图保守融合到已验收点云。

原则：旧点一律不替换；候选坐标先由完全相同的旧视频帧恢复相似变换；补拍点
必须通过米制配准、房间边界、多帧共识和半径邻域检查。脚本只生成候选和诊断，
不会修改 preview_selection.json。
"""
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
from scripts.refuse_registered_points import _empty, _reduce_records


def fit_similarity(source: np.ndarray, target: np.ndarray) -> tuple[float, np.ndarray, np.ndarray]:
    """最小二乘求 target = scale * source @ rotation.T + translation。"""
    source = np.asarray(source, dtype=np.float64)
    target = np.asarray(target, dtype=np.float64)
    source_mean, target_mean = source.mean(axis=0), target.mean(axis=0)
    source_zero, target_zero = source - source_mean, target - target_mean
    covariance = source_zero.T @ target_zero / max(1, len(source))
    left, singular, right_t = np.linalg.svd(covariance)
    correction = np.eye(3)
    if np.linalg.det(right_t.T @ left.T) < 0:
        correction[-1, -1] = -1.0
    rotation = right_t.T @ correction @ left.T
    variance = float(np.mean(np.sum(source_zero * source_zero, axis=1)))
    scale = float(np.sum(singular * np.diag(correction)) / max(variance, 1e-12))
    translation = target_mean - scale * (source_mean @ rotation.T)
    return scale, rotation, translation


def robust_similarity(
    source: np.ndarray, target: np.ndarray, *, iterations: int = 5,
) -> tuple[float, np.ndarray, np.ndarray, np.ndarray]:
    keep = np.ones(len(source), dtype=bool)
    for _ in range(iterations):
        scale, rotation, translation = fit_similarity(source[keep], target[keep])
        predicted = scale * (source @ rotation.T) + translation
        residual = np.linalg.norm(predicted - target, axis=1)
        cutoff = max(float(np.quantile(residual[keep], 0.72)), 1e-8)
        keep = residual <= cutoff
    scale, rotation, translation = fit_similarity(source[keep], target[keep])
    residual = np.linalg.norm(scale * (source @ rotation.T) + translation - target, axis=1)
    return scale, rotation, translation, residual


def _metric(points: np.ndarray, alignment: dict) -> np.ndarray:
    rotation = np.asarray(alignment["alignment"]["rotation"], dtype=np.float64)
    floor_z = float(alignment["alignment"]["floor_z_raw_aligned"])
    scale = float(alignment["scale"]["applied"])
    result = np.asarray(points, dtype=np.float64) @ rotation.T
    result[:, 2] -= floor_z
    return result * scale


def fuse(
    baseline_work: Path, candidate_root: Path, baseline_preview: Path, output: Path,
    *, voxel: float = 0.008, min_confidence: float = 12.0,
) -> dict:
    baseline_work, candidate_root = Path(baseline_work), Path(candidate_root)
    mapping_payload = json.loads((candidate_root / "supplement_mapping.json").read_text(encoding="utf-8"))
    mapping = mapping_payload["mapping"]
    baseline_preds = baseline_work / "slam3r/scene/preds"
    candidate_preds = candidate_root / "slam3r/scene/preds"
    base_points = np.load(baseline_preds / "registered_pcds.npy", mmap_mode="r")
    base_conf = np.load(baseline_preds / "registered_confs.npy", mmap_mode="r")
    new_points = np.load(candidate_preds / "registered_pcds.npy", mmap_mode="r")
    new_conf = np.load(candidate_preds / "registered_confs.npy", mmap_mode="r")
    new_images = np.load(candidate_preds / "input_imgs.npy", mmap_mode="r")
    if len(mapping) != len(new_points):
        raise RuntimeError(f"帧映射与候选点图数量不一致：{len(mapping)} != {len(new_points)}")

    rng = np.random.default_rng(460855)
    # 每段补拍视频都是独立的局部轨迹。把不同片段的锚点强行拟合成一个
    # 全局相似变换，会让某一段的漂移污染其余片段；必须逐 clip 恢复坐标。
    anchor_pairs: dict[int, tuple[list[np.ndarray], list[np.ndarray]]] = {}
    for item in mapping:
        if item["kind"] != "anchor":
            continue
        clip_id = int(item["clip_id"])
        new_id, base_id = int(item["output_index"]), int(item["baseline_index"])
        source = np.asarray(new_points[new_id], dtype=np.float64).reshape(-1, 3)
        target = np.asarray(base_points[base_id], dtype=np.float64).reshape(-1, 3)
        confidence = np.minimum(
            np.asarray(new_conf[new_id]).reshape(-1), np.asarray(base_conf[base_id]).reshape(-1),
        )
        valid = np.isfinite(source).all(axis=1) & np.isfinite(target).all(axis=1) & (confidence >= min_confidence)
        ids = np.flatnonzero(valid)
        if len(ids) > 1600:
            ids = rng.choice(ids, 1600, replace=False)
        pair = anchor_pairs.setdefault(clip_id, ([], []))
        pair[0].append(source[ids])
        pair[1].append(target[ids])

    alignment = json.loads((baseline_work / "postprocess/alignment.json").read_text(encoding="utf-8"))
    metric_scale = float(alignment["scale"]["applied"])
    clip_transforms: dict[int, tuple[float, np.ndarray, np.ndarray]] = {}
    anchor_metrics: dict[str, dict] = {}
    for clip_id, (source_parts, target_parts) in sorted(anchor_pairs.items()):
        source = np.concatenate(source_parts)
        target = np.concatenate(target_parts)
        scale, rotation, translation, raw_residual = robust_similarity(source, target)
        residual_m = raw_residual * metric_scale
        metrics = {
            "correspondences": int(len(source)),
            "inlier_fraction_3cm": float(np.mean(residual_m <= 0.03)),
            "median_error_m": float(np.median(residual_m)),
            "p90_error_m": float(np.quantile(residual_m, 0.90)),
            "similarity_scale": scale,
        }
        anchor_metrics[str(clip_id)] = metrics
        if not (0.45 <= scale <= 2.2 and metrics["median_error_m"] <= 0.035
                and metrics["p90_error_m"] <= 0.11):
            continue
        clip_transforms[clip_id] = (scale, rotation, translation)
    if not clip_transforms:
        raise RuntimeError(f"所有补拍片段均未通过独立旧帧锚点米制配准：{anchor_metrics}")

    base_cloud = o3d.io.read_point_cloud(str(baseline_preview))
    base_xyz = np.asarray(base_cloud.points, dtype=np.float64)
    base_rgb = np.asarray(base_cloud.colors, dtype=np.float64)
    base_tree = cKDTree(base_xyz)
    extents = alignment["extents_m"]
    lower = np.asarray([extents["x"][0] - 0.04, extents["y"][0] - 0.04, -0.04])
    upper = np.asarray([extents["x"][1] + 0.04, extents["y"][1] + 0.04, extents["z"][1] - 0.09])
    origin = lower - 0.02
    shape = np.ceil((upper - origin) / voxel).astype(np.int64) + 1
    nx, ny = int(shape[0]), int(shape[1])

    records = _empty()
    frame_diagnostics: list[dict] = []
    accepted_clips: set[int] = set()
    for item in mapping:
        if item["kind"] != "supplement":
            continue
        frame_id, clip_id = int(item["output_index"]), int(item["clip_id"])
        if clip_id not in clip_transforms:
            frame_diagnostics.append({
                "frame": frame_id, "clip": clip_id, "accepted": False,
                "reason": "clip_anchor_registration_failed",
            })
            continue
        raw = np.asarray(new_points[frame_id], dtype=np.float64).reshape(-1, 3)
        conf = np.asarray(new_conf[frame_id], dtype=np.float32).reshape(-1)
        rgb = np.asarray(new_images[frame_id], dtype=np.float32).reshape(-1, 3)
        valid = np.isfinite(raw).all(axis=1) & np.isfinite(conf) & (conf >= min_confidence)
        raw, conf, rgb = raw[valid], conf[valid], rgb[valid]
        similarity_scale, similarity_rotation, similarity_translation = clip_transforms[clip_id]
        raw = similarity_scale * (raw @ similarity_rotation.T) + similarity_translation
        xyz = _metric(raw, alignment)
        inside = np.all((xyz >= lower) & (xyz <= upper), axis=1)
        xyz, conf, rgb = xyz[inside], conf[inside], rgb[inside]
        if len(xyz) < 500:
            frame_diagnostics.append({"frame": frame_id, "clip": clip_id, "accepted": False, "reason": "too_few_points"})
            continue
        sample = rng.choice(len(xyz), min(3500, len(xyz)), replace=False)
        sample_distance, _ = base_tree.query(xyz[sample], workers=-1)
        overlap = float(np.mean(sample_distance <= 0.055))
        median_distance = float(np.median(sample_distance))
        if overlap < 0.16 or median_distance > 0.14:
            frame_diagnostics.append({
                "frame": frame_id, "clip": clip_id, "accepted": False, "reason": "low_overlap",
                "overlap_5_5cm": overlap, "median_distance_m": median_distance,
            })
            continue
        distance, _ = base_tree.query(xyz, workers=-1)
        # 不替换旧表面，只填邻近已知房间的低覆盖区域；远离旧场景的悬浮点拒绝。
        add = (distance >= 0.012) & (distance <= 0.28)
        xyz, conf, rgb = xyz[add], conf[add], rgb[add]
        if len(xyz) < 80:
            continue
        ijk = np.floor((xyz - origin) / voxel).astype(np.int64)
        hashes = ijk[:, 0] + nx * (ijk[:, 1] + ny * ijk[:, 2])
        order = np.lexsort((-conf, hashes))
        sorted_hash = hashes[order]
        first = np.r_[0, np.flatnonzero(sorted_hash[1:] != sorted_hash[:-1]) + 1]
        chosen = order[first]
        hashes, xyz, conf, rgb = hashes[chosen], xyz[chosen], conf[chosen], rgb[chosen]
        weight = np.log1p(np.maximum(conf, 0)).astype(np.float64)
        frame_records = (
            hashes, xyz * weight[:, None], rgb.astype(np.float64) * weight[:, None], weight,
            np.ones(len(hashes), np.int32), np.ones(len(hashes), np.int16), conf,
        )
        records = _reduce_records(*[
            np.concatenate([left, right]) for left, right in zip(records, frame_records)
        ])
        accepted_clips.add(clip_id)
        frame_diagnostics.append({
            "frame": frame_id, "clip": clip_id, "accepted": True,
            "overlap_5_5cm": overlap, "median_distance_m": median_distance,
            "candidate_voxels": int(len(hashes)),
        })

    _, xyz_sum, rgb_sum, weight_sum, frame_count, clip_votes, max_conf = records
    keep = (frame_count >= 4) & (max_conf >= min_confidence)
    additions = xyz_sum[keep] / weight_sum[keep, None]
    colors = np.clip(rgb_sum[keep] / weight_sum[keep, None], 0, 255) / 255.0
    support = frame_count[keep]
    if len(additions):
        addition_cloud = o3d.geometry.PointCloud()
        addition_cloud.points = o3d.utility.Vector3dVector(additions)
        addition_cloud.colors = o3d.utility.Vector3dVector(colors)
        _, indices = addition_cloud.remove_radius_outlier(nb_points=3, radius=0.026)
        indices = np.asarray(indices, dtype=np.int64)
        additions, colors, support = additions[indices], colors[indices], support[indices]
    maximum = int(len(base_xyz) * 0.30)
    if len(additions) > maximum:
        selected = np.argsort(support)[-maximum:]
        additions, colors = additions[selected], colors[selected]

    combined = o3d.geometry.PointCloud()
    combined.points = o3d.utility.Vector3dVector(np.vstack([base_xyz, additions]))
    combined.colors = o3d.utility.Vector3dVector(np.vstack([base_rgb, colors]))
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    o3d.io.write_point_cloud(str(output), combined, write_ascii=False, compressed=False)
    diagnostics = {
        "status": "candidate_ready", "method": "identical_frame_similarity_anchored_multiview_consensus",
        "baseline": str(Path(baseline_preview).resolve()), "base_points_preserved": int(len(base_xyz)),
        "anchor_metrics": anchor_metrics, "accepted_clips": sorted(accepted_clips),
        "accepted_frames": sum(item.get("accepted") is True for item in frame_diagnostics),
        "rejected_frames": sum(item.get("accepted") is False for item in frame_diagnostics),
        "consensus_additions": int(len(additions)), "addition_fraction": float(len(additions) / max(1, len(base_xyz))),
        "voxel_m": voxel, "min_confidence": min_confidence,
        "promotion_eligible": len(accepted_clips) >= 3 and len(additions) >= 500,
        "promotion_requires_visual_qa": True, "output": output.name,
        "frame_diagnostics": frame_diagnostics,
    }
    output.with_suffix(".json").write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
    return diagnostics


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("baseline_work", type=Path)
    parser.add_argument("candidate_root", type=Path)
    parser.add_argument("baseline_preview", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--voxel", type=float, default=0.008)
    parser.add_argument("--min-confidence", type=float, default=12.0)
    args = parser.parse_args()
    outcome = fuse(
        args.baseline_work, args.candidate_root, args.baseline_preview, args.output,
        voxel=args.voxel, min_confidence=args.min_confidence,
    )
    print(json.dumps({key: value for key, value in outcome.items() if key != "frame_diagnostics"}, ensure_ascii=False, indent=2))
