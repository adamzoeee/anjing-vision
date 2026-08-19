"""阶段 6：将逐视角语义 mask 关联为可解释的 3D instance tracks。

本模块只组织阶段 2 已产生的语义证据。它不改变语义投票阈值，也不拟合
几何尺寸。所有关联都保留证据和拒绝原因，供 instance diagnostics 使用。
"""
from __future__ import annotations

import math
from collections import defaultdict
from typing import Callable

import numpy as np
from scipy.spatial import cKDTree

from pipeline.semantic import SemanticFusion


CANONICAL_GROUPS = {
    "床": "bed",
    "书桌": "table_group", "桌子": "table_group",
    "书架": "storage_group", "柜子": "storage_group", "衣柜": "storage_group",
    "椅子": "chair", "凳子": "stool", "纸箱": "box", "收纳箱": "box",
    "杂物": "box", "沙发": "sofa", "行李箱": "suitcase",
}

NORMALIZED_LABELS = {
    "床": "bed", "书桌": "desk", "桌子": "table", "书架": "bookshelf",
    "柜子": "cabinet", "衣柜": "wardrobe", "椅子": "chair", "凳子": "stool",
    "纸箱": "box", "收纳箱": "box", "杂物": "box", "沙发": "sofa",
    "行李箱": "suitcase",
}


def canonical_semantic_group(label: str) -> str | None:
    return CANONICAL_GROUPS.get(str(label).strip())


def _robust_bounds(xyz: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if len(xyz) == 0:
        return np.zeros(3), np.zeros(3)
    return np.quantile(xyz, 0.02, axis=0), np.quantile(xyz, 0.98, axis=0)


def _sample(ids: np.ndarray, limit: int = 1200) -> np.ndarray:
    if len(ids) <= limit:
        return ids
    return ids[np.linspace(0, len(ids) - 1, limit, dtype=int)]


def _point_gap(left: np.ndarray, right: np.ndarray, points: np.ndarray) -> float:
    left, right = _sample(left), _sample(right)
    if len(left) == 0 or len(right) == 0:
        return float("inf")
    if len(left) > len(right):
        left, right = right, left
    distances, _ = cKDTree(points[right]).query(points[left], k=1)
    return float(np.min(distances))


def build_view_observations(
    points: np.ndarray,
    fusion: SemanticFusion,
    structural_filter: Callable[[np.ndarray], tuple[np.ndarray, dict]],
) -> list[dict]:
    """把每个有效 DINO/SAM 投影保存为一个 view-level observation。"""
    points = np.asarray(points, dtype=float)
    observations: list[dict] = []
    per_view_mask: dict[int, int] = defaultdict(int)
    for index, detection in enumerate(fusion.detection_support):
        label = str(detection.get("label", "")).strip()
        group = canonical_semantic_group(label)
        if group is None:
            continue
        view_id = int(detection.get("view_id", index))
        mask_id = int(detection.get("mask_id", per_view_mask[view_id]))
        per_view_mask[view_id] += 1
        raw_ids = np.unique(np.asarray(detection.get("point_ids", []), dtype=np.int64))
        raw_ids = raw_ids[(raw_ids >= 0) & (raw_ids < len(points))]
        point_ids, filtering = structural_filter(raw_ids)
        xyz = points[point_ids] if len(point_ids) else np.empty((0, 3), dtype=float)
        lo, hi = _robust_bounds(xyz)
        camera_position = detection.get("camera_position")
        camera_direction = detection.get("camera_direction")
        observation_id = str(detection.get("observation_id") or f"obs_{view_id:05d}_{mask_id:03d}")
        observations.append({
            "observation_id": observation_id,
            "frame_id": view_id,
            "frame_order": int(detection.get("frame_order", view_id)),
            "camera_id": int(detection.get("camera_id", view_id)),
            "semantic_label": label,
            "normalized_label": NORMALIZED_LABELS[label],
            "canonical_group": group,
            "dino_confidence": float(detection.get("score", 0.0)),
            "sam_confidence": float(detection.get("mask_score", 0.0)),
            "sam_mask_id": mask_id,
            "mask_area": int(detection.get("mask_area_px", 0)),
            "raw_projected_point_ids": raw_ids,
            "projected_point_ids": point_ids,
            "projected_point_count": int(len(point_ids)),
            "raw_projected_point_count": int(len(raw_ids)),
            "projection_filtering": filtering,
            "centroid_3d": np.median(xyz, axis=0) if len(xyz) else np.zeros(3),
            "bbox_3d_coarse": {"min": lo, "max": hi},
            "camera_position": None if camera_position is None else np.asarray(camera_position, dtype=float),
            "camera_direction": None if camera_direction is None else np.asarray(camera_direction, dtype=float),
            "image_name": detection.get("image_name"),
        })
    return observations


def _pair_evidence(left: dict, right: dict, points: np.ndarray, view_count: int) -> dict:
    left_ids, right_ids = left["projected_point_ids"], right["projected_point_ids"]
    common = np.intersect1d(left_ids, right_ids, assume_unique=True)
    overlap_min = len(common) / max(min(len(left_ids), len(right_ids)), 1)
    overlap_union = len(common) / max(len(np.union1d(left_ids, right_ids)), 1)
    gap = _point_gap(left_ids, right_ids, points)
    left_lo, left_hi = left["bbox_3d_coarse"]["min"], left["bbox_3d_coarse"]["max"]
    right_lo, right_hi = right["bbox_3d_coarse"]["min"], right["bbox_3d_coarse"]["max"]
    left_scale = float(np.linalg.norm(left_hi - left_lo))
    right_scale = float(np.linalg.norm(right_hi - right_lo))
    centroid_distance = float(np.linalg.norm(left["centroid_3d"] - right["centroid_3d"]))
    frame_gap = abs(int(left["frame_order"]) - int(right["frame_order"]))
    continuity_limit = max(3, int(math.ceil(max(view_count, 1) * 0.15)))
    adjacency_limit = max(0.08, min(0.30, 0.18 * (left_scale + right_scale)))
    same_view = left["frame_id"] == right["frame_id"]
    same_view_conflict = bool(same_view and overlap_min < 0.02 and gap > adjacency_limit)
    strong_overlap = len(common) >= 3 and overlap_min >= 0.03
    spatial_continuity = (
        not same_view
        and frame_gap <= continuity_limit
        and gap <= adjacency_limit
        and centroid_distance <= left_scale + right_scale + adjacency_limit
    )
    compatible_alias = left["canonical_group"] == right["canonical_group"]
    accepted = compatible_alias and not same_view and not same_view_conflict and (strong_overlap or spatial_continuity)
    if same_view_conflict:
        reason = "same_view_separate_masks"
    elif same_view:
        reason = "same_view_masks_are_not_cross_view_evidence"
    elif strong_overlap:
        reason = "shared_projected_3d_points"
    elif spatial_continuity:
        reason = "spatially_adjacent_with_view_continuity"
    elif frame_gap > continuity_limit and not strong_overlap:
        reason = "no_overlap_and_view_gap_too_large"
    else:
        reason = "insufficient_cross_view_relation"
    score = 0.55 * min(overlap_min / 0.25, 1.0)
    if np.isfinite(gap):
        score += 0.25 * max(0.0, 1.0 - gap / max(adjacency_limit, 1e-6))
    score += 0.20 * max(0.0, 1.0 - frame_gap / max(continuity_limit, 1))
    return {
        "left": left["observation_id"], "right": right["observation_id"],
        "accepted": bool(accepted), "reason": reason,
        "shared_projected_points": int(len(common)),
        "projected_point_overlap": float(overlap_min),
        "projected_point_iou": float(overlap_union),
        "minimum_3d_gap": gap, "centroid_distance": centroid_distance,
        "frame_gap": frame_gap, "same_view_conflict": same_view_conflict,
        "association_score": float(np.clip(score, 0.0, 1.0)),
    }


def associate_observations(observations: list[dict], points: np.ndarray) -> tuple[list[dict], list[dict]]:
    """以保守的组件级校验关联 observations，避免无限传递式误并。"""
    if not observations:
        return [], []
    by_group: dict[str, list[int]] = defaultdict(list)
    for index, observation in enumerate(observations):
        by_group[observation["canonical_group"]].append(index)
    all_edges: list[dict] = []
    tracks: list[dict] = []
    for group, indices in sorted(by_group.items()):
        parent = {index: index for index in indices}
        members = {index: {index} for index in indices}

        def find(value: int) -> int:
            while parent[value] != value:
                parent[value] = parent[parent[value]]
                value = parent[value]
            return value

        candidates: list[tuple[float, int, int, dict]] = []
        for offset, left_index in enumerate(indices):
            for right_index in indices[offset + 1:]:
                evidence = _pair_evidence(
                    observations[left_index], observations[right_index], points, len(indices),
                )
                all_edges.append(evidence)
                if evidence["accepted"]:
                    candidates.append((-evidence["association_score"], left_index, right_index, evidence))
        for _negative_score, left_index, right_index, evidence in sorted(candidates):
            left_root, right_root = find(left_index), find(right_index)
            if left_root == right_root:
                continue
            conflicts: list[str] = []
            for a in members[left_root]:
                for b in members[right_root]:
                    if observations[a]["frame_id"] != observations[b]["frame_id"]:
                        continue
                    pair = _pair_evidence(observations[a], observations[b], points, len(indices))
                    if pair["same_view_conflict"]:
                        conflicts.append(f"{observations[a]['observation_id']}|{observations[b]['observation_id']}")
            if conflicts:
                evidence["accepted"] = False
                evidence["reason"] = "component_merge_blocked_by_same_view_conflict"
                evidence["component_conflicts"] = conflicts
                continue
            parent[right_root] = left_root
            members[left_root].update(members.pop(right_root))
        components: dict[int, list[int]] = defaultdict(list)
        for index in indices:
            components[find(index)].append(index)
        for sequence, component in enumerate(
            sorted(components.values(), key=lambda values: min(observations[i]["frame_order"] for i in values)), 1
        ):
            observation_ids = {observations[i]["observation_id"] for i in component}
            accepted_edges = [
                edge for edge in all_edges
                if edge["accepted"] and edge["left"] in observation_ids and edge["right"] in observation_ids
            ]
            tracks.append({
                "track_id": f"{group}_track_{sequence:03d}",
                "canonical_group": group,
                "observation_indices": component,
                "accepted_edges": accepted_edges,
            })
    return tracks, all_edges


def resolve_track_label(track_observations: list[dict]) -> dict:
    weighted: dict[str, float] = defaultdict(float)
    for observation in track_observations:
        weight = (
            max(observation["dino_confidence"], 0.0)
            * max(observation["sam_confidence"], 0.0)
            * math.sqrt(max(observation["projected_point_count"], 1))
        )
        weighted[observation["semantic_label"]] += weight
    ranked = sorted(weighted.items(), key=lambda item: (-item[1], item[0]))
    winner = ranked[0][0]
    total = sum(weighted.values())
    share = weighted[winner] / max(total, 1e-12)
    group = track_observations[0]["canonical_group"]
    ambiguous = share < 0.60 and len(ranked) > 1
    if ambiguous and group == "storage_group":
        normalized = "storage"
    elif ambiguous and group == "table_group":
        normalized = "table"
    else:
        normalized = NORMALIZED_LABELS[winner]
    return {
        "semantic_label": winner,
        "normalized_label": normalized,
        "canonical_group": group,
        "semantic_votes": {key: float(value) for key, value in sorted(weighted.items())},
        "dominant_vote_share": float(share),
        "label_resolution": "generic_group_due_to_ambiguity" if ambiguous else "dominant_multiview_vote",
    }


def _view_diversity(observations: list[dict], centroid: np.ndarray, extent: np.ndarray) -> tuple[float, dict]:
    positions = [item["camera_position"] for item in observations if item["camera_position"] is not None]
    if len(positions) < 2:
        fallback = min(len({item["frame_id"] for item in observations}) / 4.0, 1.0) * 0.75
        return float(fallback), {"mode": "support_view_fallback", "angular_span_deg": None, "baseline": None}
    positions_array = np.asarray(positions, dtype=float)
    rays = positions_array - centroid
    norms = np.linalg.norm(rays, axis=1)
    valid = norms > 1e-6
    rays = rays[valid] / norms[valid, None]
    max_angle = 0.0
    for left in range(len(rays)):
        for right in range(left + 1, len(rays)):
            max_angle = max(max_angle, math.acos(float(np.clip(np.dot(rays[left], rays[right]), -1.0, 1.0))))
    baseline = float(np.max(np.linalg.norm(positions_array[:, None] - positions_array[None, :], axis=2)))
    object_scale = max(float(np.linalg.norm(extent)), 0.15)
    angular_factor = min(max_angle / math.radians(90.0), 1.0)
    baseline_factor = min(baseline / object_scale, 1.0)
    score = 0.70 * angular_factor + 0.30 * baseline_factor
    return float(score), {
        "mode": "camera_geometry", "angular_span_deg": math.degrees(max_angle),
        "baseline": baseline, "angular_factor": angular_factor, "baseline_factor": baseline_factor,
    }


def _boundary_stability(observations: list[dict], cluster: np.ndarray, points: np.ndarray) -> tuple[float, dict]:
    cluster_set = set(int(value) for value in cluster)
    cumulative: set[int] = set()
    extents: list[np.ndarray] = []
    steps: list[dict] = []
    by_frame: dict[int, list[dict]] = defaultdict(list)
    for observation in observations:
        by_frame[int(observation["frame_id"])].append(observation)
    ordered_frames = sorted(
        by_frame, key=lambda frame: min(int(item["frame_order"]) for item in by_frame[frame]),
    )
    for frame_id in ordered_frames:
        frame_observations = by_frame[frame_id]
        contributed: set[int] = set()
        for observation in frame_observations:
            contributed.update(
                cluster_set.intersection(int(value) for value in observation["projected_point_ids"])
            )
        if not contributed:
            continue
        cumulative.update(contributed)
        ids = np.asarray(sorted(cumulative), dtype=np.int64)
        lo, hi = _robust_bounds(points[ids])
        extent = hi - lo
        extents.append(extent)
        steps.append({
            "frame_id": frame_id,
            "observation_ids": [item["observation_id"] for item in frame_observations],
            "point_count": int(len(ids)),
            "extent": extent.tolist(),
        })
    if len(extents) <= 1:
        return 0.25, {"score": 0.25, "changes": [], "steps": steps, "reason": "single_boundary_observation"}
    final = np.maximum(extents[-1], 0.03)
    changes = [float(np.linalg.norm((extents[index] - extents[index - 1]) / final) / math.sqrt(3.0))
               for index in range(1, len(extents))]
    tail = changes[-min(3, len(changes)):]
    score = float(math.exp(-3.0 * float(np.mean(tail))))
    return score, {"score": score, "changes": changes, "tail_mean_change": float(np.mean(tail)), "steps": steps}


def completeness_metrics(
    observations: list[dict], cluster: np.ndarray, points: np.ndarray,
    accepted_edges: list[dict], fragment_count: int, candidate_consistency: float,
) -> dict:
    xyz = points[cluster]
    lo, hi = _robust_bounds(xyz)
    extent = hi - lo
    centroid = np.median(xyz, axis=0)
    support_views = len({item["frame_id"] for item in observations})
    support_factor = min(support_views / 4.0, 1.0)
    diversity, diversity_detail = _view_diversity(observations, centroid, extent)
    boundary, boundary_detail = _boundary_stability(observations, cluster, points)
    # observation 已按 canonical group 隔离；desk/table 等近义标签之间的分歧
    # 是类别精细度不确定，不是实例归属冲突。
    group_weights: dict[str, float] = defaultdict(float)
    for item in observations:
        group_weights[item["canonical_group"]] += (
            item["dino_confidence"] * item["sam_confidence"] * math.sqrt(max(item["projected_point_count"], 1))
        )
    semantic_consistency = max(group_weights.values()) / max(sum(group_weights.values()), 1e-12)
    cross_view = float(np.mean([edge["association_score"] for edge in accepted_edges])) if accepted_edges else 0.0
    point_factor = min(len(cluster) / 300.0, 1.0)
    fragment_consistency = 1.0 / max(fragment_count, 1)
    areas = np.asarray([max(item["mask_area"], 1) for item in observations], dtype=float)
    area_diversity = float(min(np.ptp(np.log(areas)) / 1.5, 1.0)) if len(areas) > 1 else 0.0
    factors = {
        "support_views": float(support_factor), "view_diversity": float(diversity),
        "projected_area_diversity": area_diversity, "boundary_stability": float(boundary),
        "semantic_consistency": float(semantic_consistency), "cross_view_consistency": cross_view,
        "point_support": float(point_factor), "fragment_consistency": float(fragment_consistency),
        "candidate_consistency": float(candidate_consistency),
    }
    score = (
        0.13 * support_factor + 0.15 * diversity + 0.04 * area_diversity
        + 0.22 * boundary + 0.13 * semantic_consistency + 0.12 * cross_view
        + 0.08 * point_factor + 0.05 * fragment_consistency + 0.08 * candidate_consistency
    )
    return {
        "score": float(np.clip(score, 0.0, 1.0)), "factors": factors,
        "view_diversity_detail": diversity_detail, "boundary_stability_detail": boundary_detail,
        "robust_extent": extent.tolist(),
    }


def serialize_observation(observation: dict, instance_ids: list[str]) -> dict:
    return {
        "observation_id": observation["observation_id"], "frame_id": observation["frame_id"],
        "camera_id": observation["camera_id"], "semantic_label": observation["semantic_label"],
        "normalized_label": observation["normalized_label"],
        "canonical_group": observation["canonical_group"],
        "dino_confidence": observation["dino_confidence"], "sam_mask_id": observation["sam_mask_id"],
        "sam_confidence": observation["sam_confidence"], "mask_area": observation["mask_area"],
        "projected_point_ids": [int(value) for value in observation["projected_point_ids"]],
        "projected_point_count": observation["projected_point_count"],
        "raw_projected_point_count": observation["raw_projected_point_count"],
        "projection_filtering": observation["projection_filtering"],
        "centroid_3d": observation["centroid_3d"].tolist(),
        "bbox_3d_coarse": {
            "min": observation["bbox_3d_coarse"]["min"].tolist(),
            "max": observation["bbox_3d_coarse"]["max"].tolist(),
        },
        "camera_position": None if observation["camera_position"] is None else observation["camera_position"].tolist(),
        "camera_direction": None if observation["camera_direction"] is None else observation["camera_direction"].tolist(),
        "assigned_instances": instance_ids,
    }
