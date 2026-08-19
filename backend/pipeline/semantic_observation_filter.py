"""阶段 7：在 instance association 前净化每个 SAM observation 的 3D 投影点。

净化只使用 observation 自身、相机深度、多视角点支持和弱 SpatialLM 冲突证据，
不使用类别尺寸先验，也不读取后续 Geometry 结果。
"""
from __future__ import annotations

import math
import time
from collections import Counter, defaultdict

import numpy as np
import open3d as o3d


MIN_COMPONENT_POINTS = 8
MIN_CROSS_VIEW_RATIO = 0.20
STABLE_CROSS_VIEW_RATIO = 0.35


def _robust_stats(values: np.ndarray) -> dict:
    values = np.asarray(values, dtype=float)
    if len(values) == 0:
        return {"min": None, "q02": None, "q10": None, "median": None, "q90": None, "q98": None, "max": None}
    quantiles = np.quantile(values, [0.02, 0.10, 0.50, 0.90, 0.98])
    return {
        "min": float(np.min(values)), "q02": float(quantiles[0]), "q10": float(quantiles[1]),
        "median": float(quantiles[2]), "q90": float(quantiles[3]), "q98": float(quantiles[4]),
        "max": float(np.max(values)),
    }


def _extent(points: np.ndarray) -> list[float]:
    if len(points) == 0:
        return [0.0, 0.0, 0.0]
    lo, hi = np.quantile(points, [0.02, 0.98], axis=0)
    return (hi - lo).astype(float).tolist()


def _cluster_components(point_ids: np.ndarray, points: np.ndarray) -> tuple[list[np.ndarray], np.ndarray, dict]:
    if len(point_ids) < MIN_COMPONENT_POINTS:
        labels = np.zeros(len(point_ids), dtype=int)
        return ([point_ids] if len(point_ids) else []), labels, {
            "eps": None, "nearest_neighbor_median": None,
            "component_count": 1 if len(point_ids) else 0, "noise_points": 0,
        }
    xyz = points[point_ids]
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(xyz))
    distances = np.asarray(cloud.compute_nearest_neighbor_distance(), dtype=float)
    positive = distances[np.isfinite(distances) & (distances > 1e-6)]
    spacing = float(np.median(positive)) if len(positive) else 0.03
    eps = float(np.clip(spacing * 4.0, 0.06, 0.18))
    labels = np.asarray(cloud.cluster_dbscan(eps=eps, min_points=MIN_COMPONENT_POINTS, print_progress=False))
    components = [
        point_ids[labels == label]
        for label in sorted(set(labels.tolist())) if label >= 0
    ]
    if not components:
        labels = np.zeros(len(point_ids), dtype=int)
        components = [point_ids]
    return components, labels, {
        "eps": eps, "nearest_neighbor_median": spacing,
        "component_count": len(components), "noise_points": int((labels < 0).sum()),
    }


def _candidate_evidence(
    component: np.ndarray,
    canonical_group: str,
    candidate_sets: dict[str, np.ndarray],
    candidate_groups: dict[str, str | None],
) -> dict:
    if len(component) == 0:
        return {"compatible_coverage": 0.0, "conflicting_coverage": 0.0, "candidates": []}
    rows: list[dict] = []
    compatible = 0.0
    conflicting = 0.0
    for candidate_id, candidate_ids in candidate_sets.items():
        overlap = len(np.intersect1d(component, candidate_ids, assume_unique=False))
        coverage = overlap / len(component)
        if overlap < 3 or coverage < 0.05:
            continue
        group = candidate_groups.get(candidate_id)
        is_compatible = group == canonical_group
        rows.append({
            "candidate_id": candidate_id, "candidate_group": group,
            "overlap_points": int(overlap), "component_coverage": float(coverage),
            "semantic_compatible": is_compatible,
        })
        if is_compatible:
            compatible = max(compatible, coverage)
        elif group is not None:
            conflicting = max(conflicting, coverage)
    rows.sort(key=lambda item: (-item["component_coverage"], item["candidate_id"]))
    return {
        "compatible_coverage": float(compatible),
        "conflicting_coverage": float(conflicting),
        "candidates": rows,
    }


def _depth_cluster_count(medians: list[float], raw_depth_span: float) -> int:
    if not medians:
        return 0
    ordered = sorted(medians)
    threshold = max(raw_depth_span * 0.12, 1e-6)
    return 1 + sum((right - left) > threshold for left, right in zip(ordered, ordered[1:]))


def purify_observations(
    observations: list[dict],
    points: np.ndarray,
    candidate_sets: dict[str, np.ndarray] | None = None,
    candidate_groups: dict[str, str | None] | None = None,
) -> tuple[list[dict], dict, dict[str, np.ndarray]]:
    """返回净化 observation、轻量 JSON diagnostic 和压缩点集内容。"""
    started = time.perf_counter()
    points = np.asarray(points, dtype=float)
    candidate_sets = candidate_sets or {}
    candidate_groups = candidate_groups or {}

    group_point_frames: dict[str, dict[int, set[int]]] = defaultdict(lambda: defaultdict(set))
    point_group_frames: dict[int, dict[str, set[int]]] = defaultdict(lambda: defaultdict(set))
    for observation in observations:
        group = observation["canonical_group"]
        frame_id = int(observation["frame_id"])
        for point_id in observation["projected_point_ids"]:
            point_id = int(point_id)
            group_point_frames[group][point_id].add(frame_id)
            point_group_frames[point_id][group].add(frame_id)

    purified: list[dict] = []
    diagnostic_rows: list[dict] = []
    point_sets: dict[str, np.ndarray] = {}
    total_raw = total_filtered = total_rejected = 0
    status_counts: Counter[str] = Counter()

    for observation in observations:
        observation_id = observation["observation_id"]
        group = observation["canonical_group"]
        raw_ids = np.unique(np.asarray(observation["projected_point_ids"], dtype=np.int64))
        raw_ids = raw_ids[(raw_ids >= 0) & (raw_ids < len(points))]
        raw_xyz = points[raw_ids] if len(raw_ids) else np.empty((0, 3), dtype=float)
        camera_position = observation.get("camera_position")
        camera_direction = observation.get("camera_direction")
        if camera_position is not None and camera_direction is not None:
            camera_position = np.asarray(camera_position, dtype=float)
            camera_direction = np.asarray(camera_direction, dtype=float)
            norm = float(np.linalg.norm(camera_direction))
            if norm > 1e-9:
                camera_direction = camera_direction / norm
                raw_depth = (raw_xyz - camera_position) @ camera_direction
            else:
                raw_depth = np.linalg.norm(raw_xyz - camera_position, axis=1)
        else:
            raw_depth = np.linalg.norm(raw_xyz - np.median(raw_xyz, axis=0), axis=1) if len(raw_xyz) else np.empty(0)
        raw_depth_stats = _robust_stats(raw_depth)
        raw_depth_span = max(
            float((raw_depth_stats["q90"] or 0.0) - (raw_depth_stats["q10"] or 0.0)), 1e-6,
        )
        components, labels, clustering = _cluster_components(raw_ids, points)

        component_rows: list[dict] = []
        substantial_depths: list[float] = []
        for component_index, component in enumerate(components):
            xyz = points[component]
            if camera_position is not None and camera_direction is not None:
                depth = (xyz - camera_position) @ camera_direction
            else:
                depth = np.linalg.norm(xyz - np.median(raw_xyz, axis=0), axis=1)
            depth_stats = _robust_stats(depth)
            ratio = len(component) / max(len(raw_ids), 1)
            if len(component) >= max(30, int(len(raw_ids) * 0.005)):
                substantial_depths.append(float(depth_stats["median"]))
            same_support = np.asarray([
                len(group_point_frames[group].get(int(point_id), set()))
                for point_id in component
            ], dtype=float)
            other_conflicts = np.asarray([
                max(
                    (len(frames) for other_group, frames in point_group_frames[int(point_id)].items()
                     if other_group != group),
                    default=0,
                )
                for point_id in component
            ], dtype=float)
            cross_ratio = float(np.mean(same_support >= 2)) if len(component) else 0.0
            conflict_ratio = float(np.mean((other_conflicts > 0) & (other_conflicts >= same_support))) if len(component) else 0.0
            candidate = _candidate_evidence(component, group, candidate_sets, candidate_groups)
            component_rows.append({
                "component_index": component_index, "point_ids": component,
                "point_count": int(len(component)), "point_ratio": float(ratio),
                "spatial_extent": _extent(xyz), "centroid_3d": np.median(xyz, axis=0).tolist(),
                "depth_statistics": depth_stats,
                "cross_view_support_ratio": cross_ratio,
                "mean_support_frames": float(np.mean(same_support)) if len(component) else 0.0,
                "semantic_conflict_ratio": conflict_ratio,
                "candidate_evidence": candidate,
            })

        nearest_depth = min(substantial_depths) if substantial_depths else (
            float(raw_depth_stats["median"]) if raw_depth_stats["median"] is not None else 0.0
        )
        compatible_exists = any(
            row["candidate_evidence"]["compatible_coverage"] >= 0.20 for row in component_rows
        )
        dominant_index = max(
            range(len(component_rows)), key=lambda index: component_rows[index]["point_count"],
            default=None,
        )
        kept_components: list[int] = []
        reject_reasons: Counter[str] = Counter()
        for row in component_rows:
            depth_gap = max(float(row["depth_statistics"]["median"]) - nearest_depth, 0.0)
            depth_gap_normalized = depth_gap / raw_depth_span
            row["depth_gap_from_nearest"] = depth_gap
            row["depth_gap_normalized"] = depth_gap_normalized
            cross_ratio = row["cross_view_support_ratio"]
            conflict_ratio = row["semantic_conflict_ratio"]
            compatible = row["candidate_evidence"]["compatible_coverage"]
            conflicting = row["candidate_evidence"]["conflicting_coverage"]
            strong_cross = cross_ratio >= MIN_CROSS_VIEW_RATIO
            stable_cross = cross_ratio >= STABLE_CROSS_VIEW_RATIO
            strong_candidate_conflict = conflicting >= 0.30 and compatible < 0.10
            far_layer = depth_gap_normalized >= 0.45
            is_dominant = row["component_index"] == dominant_index
            reasons: list[str] = []
            # SpatialLM 不能单独删除；必须同时存在深度分层或另一个兼容组件。
            if strong_candidate_conflict and (far_layer or compatible_exists):
                reasons.append("spatial_semantic_conflict_with_depth_or_alternative_support")
            if conflict_ratio >= 0.35 and not stable_cross:
                reasons.append("cross_group_semantic_conflict")
            if cross_ratio < 0.05 and not is_dominant and compatible < 0.20:
                reasons.append("single_view_unsupported_fragment")
            if far_layer and not stable_cross and compatible < 0.20:
                reasons.append("far_depth_layer_without_stable_cross_view_support")
            if row["point_ratio"] < 0.005 and not strong_cross:
                reasons.append("tiny_unstable_component")
            depth_factor = math.exp(-max(depth_gap_normalized, 0.0))
            candidate_factor = float(np.clip(0.5 + 0.35 * compatible - 0.25 * conflicting, 0.0, 1.0))
            quality = (
                0.38 * min(cross_ratio / STABLE_CROSS_VIEW_RATIO, 1.0)
                + 0.20 * depth_factor
                + 0.12 * min(math.sqrt(max(row["point_ratio"], 0.0)) * 2.0, 1.0)
                + 0.15 * (1.0 - conflict_ratio)
                + 0.15 * candidate_factor
            )
            row["component_quality"] = float(np.clip(quality, 0.0, 1.0))
            row["status"] = "rejected" if reasons else "kept"
            row["reject_reasons"] = reasons
            if reasons:
                reject_reasons.update(reasons)
            else:
                kept_components.append(row["component_index"])

        # 不因组件分解失败轻易整张删除；至少保留一个有跨视角/主组件证据的候选。
        if not kept_components and component_rows:
            fallback = max(
                component_rows,
                key=lambda row: (
                    row["component_quality"], row["cross_view_support_ratio"], row["point_count"],
                ),
            )
            if fallback["cross_view_support_ratio"] >= 0.10 or fallback["component_index"] == dominant_index:
                fallback["status"] = "kept_fallback_ambiguous"
                fallback["reject_reasons"] = []
                kept_components.append(fallback["component_index"])

        kept_arrays = [
            row["point_ids"] for row in component_rows if row["component_index"] in kept_components
        ]
        filtered_ids = np.unique(np.concatenate(kept_arrays)) if kept_arrays else np.empty(0, dtype=np.int64)
        # DBSCAN noise中仍有跨帧一致支持的点时保留，避免侵蚀高质量表面边缘。
        clustered_ids = np.unique(np.concatenate(components)) if components else np.empty(0, dtype=np.int64)
        noise_ids = np.setdiff1d(raw_ids, clustered_ids, assume_unique=False)
        supported_noise = np.asarray([
            int(point_id) for point_id in noise_ids
            if len(group_point_frames[group].get(int(point_id), set())) >= 2
            and max(
                (len(frames) for other_group, frames in point_group_frames[int(point_id)].items()
                 if other_group != group),
                default=0,
            ) <= len(group_point_frames[group].get(int(point_id), set()))
        ], dtype=np.int64)
        if len(supported_noise):
            filtered_ids = np.union1d(filtered_ids, supported_noise)
        rejected_ids = np.setdiff1d(raw_ids, filtered_ids, assume_unique=False)
        filtered_xyz = points[filtered_ids] if len(filtered_ids) else np.empty((0, 3), dtype=float)
        if camera_position is not None and camera_direction is not None:
            filtered_depth = (filtered_xyz - camera_position) @ camera_direction
        else:
            filtered_depth = np.linalg.norm(filtered_xyz - np.median(raw_xyz, axis=0), axis=1) if len(filtered_xyz) else np.empty(0)

        filtered_cross = float(np.mean([
            len(group_point_frames[group].get(int(point_id), set())) >= 2 for point_id in filtered_ids
        ])) if len(filtered_ids) else 0.0
        filtered_conflict = float(np.mean([
            max(
                (len(frames) for other_group, frames in point_group_frames[int(point_id)].items()
                 if other_group != group),
                default=0,
            ) > 0 and max(
                (len(frames) for other_group, frames in point_group_frames[int(point_id)].items()
                 if other_group != group),
                default=0,
            ) >= len(group_point_frames[group].get(int(point_id), set()))
            for point_id in filtered_ids
        ])) if len(filtered_ids) else 0.0
        kept_quality = [
            row["component_quality"] for row in component_rows if row["component_index"] in kept_components
        ]
        retained_ratio = len(filtered_ids) / max(len(raw_ids), 1)
        component_purity = float(np.mean(kept_quality)) if kept_quality else 0.0
        depth_consistency = float(np.mean([
            math.exp(-max(row["depth_gap_normalized"], 0.0))
            for row in component_rows if row["component_index"] in kept_components
        ])) if kept_components else 0.0
        quality_factors = {
            "dino_confidence": float(np.clip(observation.get("dino_confidence", 0.0), 0.0, 1.0)),
            "cross_view_agreement": filtered_cross,
            "component_purity": component_purity,
            "depth_consistency": depth_consistency,
            "semantic_consistency": 1.0 - filtered_conflict,
            "retained_ratio": retained_ratio,
        }
        observation_quality = (
            0.20 * quality_factors["dino_confidence"]
            + 0.25 * filtered_cross + 0.20 * component_purity
            + 0.15 * depth_consistency + 0.10 * (1.0 - filtered_conflict)
            + 0.10 * min(retained_ratio / 0.70, 1.0)
        )
        rejected_ratio = len(rejected_ids) / max(len(raw_ids), 1)
        if len(filtered_ids) == 0:
            status = "rejected"
        elif filtered_conflict >= 0.35:
            status = "ambiguous"
        elif observation_quality >= 0.78 and rejected_ratio < 0.10:
            status = "high_quality"
        elif observation_quality >= 0.60 and rejected_ratio < 0.45:
            status = "usable"
        elif observation_quality >= 0.40:
            status = "ambiguous"
        else:
            status = "contaminated"
        status_counts[status] += 1

        purified_observation = dict(observation)
        purified_observation["point_ids_before_purification"] = raw_ids
        purified_observation["projected_point_ids"] = filtered_ids
        purified_observation["projected_point_count"] = int(len(filtered_ids))
        purified_observation["observation_quality"] = float(observation_quality)
        purified_observation["observation_status"] = status
        purified.append(purified_observation)

        prefix = observation_id.replace("#", "_")
        pre_structural_ids = np.asarray(
            observation.get("raw_projected_point_ids", raw_ids), dtype=np.int64,
        )
        point_sets[f"sam_raw__{prefix}"] = pre_structural_ids
        point_sets[f"association_raw__{prefix}"] = raw_ids
        point_sets[f"purified__{prefix}"] = filtered_ids
        point_sets[f"rejected__{prefix}"] = rejected_ids
        for row in component_rows:
            point_sets[f"component_{row['component_index']:03d}__{prefix}"] = row["point_ids"]

        serializable_components = []
        for row in component_rows:
            serializable_components.append({
                key: value for key, value in row.items() if key != "point_ids"
            } | {"point_set_ref": f"purified_semantic_points.npz#component_{row['component_index']:03d}__{prefix}"})
        diagnostic_rows.append({
            "observation_id": observation_id, "frame_id": int(observation["frame_id"]),
            "semantic_label": observation["semantic_label"], "canonical_group": group,
            "dino_confidence": float(observation.get("dino_confidence", 0.0)),
            "sam_mask_id": int(observation.get("sam_mask_id", 0)),
            "mask_area_pixels": int(observation.get("mask_area", 0)),
            "projected_point_count_raw": int(len(raw_ids)),
            "projected_point_count_filtered": int(len(filtered_ids)),
            "camera_position": None if observation.get("camera_position") is None else np.asarray(observation["camera_position"]).tolist(),
            "camera_direction": None if observation.get("camera_direction") is None else np.asarray(observation["camera_direction"]).tolist(),
            "component_count": len(component_rows),
            "dominant_component_ratio": max((row["point_ratio"] for row in component_rows), default=0.0),
            "depth_cluster_count": _depth_cluster_count(
                [float(row["depth_statistics"]["median"]) for row in component_rows], raw_depth_span,
            ),
            "depth_statistics_raw": raw_depth_stats,
            "depth_statistics_filtered": _robust_stats(filtered_depth),
            "spatial_extent_raw": _extent(raw_xyz),
            "spatial_extent_filtered": _extent(filtered_xyz),
            "cross_view_support": filtered_cross,
            "cross_view_consistency": 1.0 - filtered_conflict,
            "components": serializable_components,
            "quality_components": quality_factors,
            "observation_quality": float(observation_quality),
            "observation_status": status,
            "rejected_point_count": int(len(rejected_ids)),
            "reject_reasons": dict(sorted(reject_reasons.items())),
            "point_sets": {
                "sam_raw": f"purified_semantic_points.npz#sam_raw__{prefix}",
                "association_raw": f"purified_semantic_points.npz#association_raw__{prefix}",
                "purified": f"purified_semantic_points.npz#purified__{prefix}",
                "rejected": f"purified_semantic_points.npz#rejected__{prefix}",
            },
            "raw_source": "pre_structural_projection" if "raw_projected_point_ids" in observation else "stage6_association_raw",
        })
        total_raw += len(raw_ids)
        total_filtered += len(filtered_ids)
        total_rejected += len(rejected_ids)

    elapsed = time.perf_counter() - started
    payload = {
        "schema_version": 1, "status": "applied",
        "method": "adaptive_3d_components+relative_camera_depth+cross_view_support+weak_spatial_conflict",
        "observation_count": len(observations),
        "counts": {
            "raw_points": int(total_raw), "purified_points": int(total_filtered),
            "rejected_points": int(total_rejected), "status": dict(sorted(status_counts.items())),
        },
        "thresholds": {
            "min_component_points": MIN_COMPONENT_POINTS,
            "min_cross_view_ratio": MIN_CROSS_VIEW_RATIO,
            "stable_cross_view_ratio": STABLE_CROSS_VIEW_RATIO,
            "note": "dimensionless evidence thresholds; no category size priors",
        },
        "performance": {"elapsed_seconds": elapsed, "device": "cpu"},
        "observations": diagnostic_rows,
    }
    return purified, payload, point_sets
