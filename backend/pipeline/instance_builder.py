"""阶段 3：把多视角稳定 semantic points 组织成独立 3D instances。"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import open3d as o3d
from scipy.spatial import cKDTree

from pipeline.semantic import SemanticFusion
from pipeline.instance_association import (
    associate_observations,
    build_view_observations,
    completeness_metrics,
    resolve_track_label,
    serialize_observation,
)
from pipeline.semantic_observation_filter import purify_observations


SEMANTIC_INSTANCE_LABELS = {
    "床": "bed", "书桌": "desk", "桌子": "table", "椅子": "chair",
    "凳子": "stool", "书架": "bookshelf", "柜子": "cabinet",
    "衣柜": "wardrobe", "纸箱": "box", "收纳箱": "box", "杂物": "box",
    "沙发": "sofa", "行李箱": "suitcase",
}

MIN_INSTANCE_POINTS = 50
MIN_INSTANCE_VIEWS = 2
MIN_MASK_OVERLAP_POINTS = 3
MIN_COMPLETENESS = 0.62
MIN_BOUNDARY_STABILITY = 0.45
MIN_VIEW_DIVERSITY = 0.25
# 只剔除紧贴房间边界平面的墙层；贴墙家具的可见前表面通常在该距离之外。
# 过宽会把书柜/衣柜自身的语义表面一起删掉。
WALL_FILTER_DISTANCE = 0.04
FLOOR_FILTER_HEIGHT = 0.06


def _structural_filter(point_ids: np.ndarray, points: np.ndarray, structure: dict) -> tuple[np.ndarray, dict]:
    """移除矩形房间边界附近墙点和紧贴地面的点；只组织点，不拟合尺寸。"""
    if len(point_ids) == 0:
        return point_ids, {"input": 0, "floor_removed": 0, "wall_removed": 0, "kept": 0}
    selected = points[point_ids]
    keep = selected[:, 2] > FLOOR_FILTER_HEIGHT
    floor_removed = int((~keep).sum())
    wall_removed = 0
    bounds = structure.get("room", {}).get("bounds_xy", {})
    lo = np.asarray(bounds.get("min", []), dtype=float)
    hi = np.asarray(bounds.get("max", []), dtype=float)
    if lo.shape == (2,) and hi.shape == (2,):
        near_wall = (
            (np.abs(selected[:, 0] - lo[0]) <= WALL_FILTER_DISTANCE)
            | (np.abs(selected[:, 0] - hi[0]) <= WALL_FILTER_DISTANCE)
            | (np.abs(selected[:, 1] - lo[1]) <= WALL_FILTER_DISTANCE)
            | (np.abs(selected[:, 1] - hi[1]) <= WALL_FILTER_DISTANCE)
        )
        wall_removed = int((keep & near_wall).sum())
        keep &= ~near_wall
    return point_ids[keep], {
        "input": int(len(point_ids)), "floor_removed": floor_removed,
        "wall_removed": wall_removed, "kept": int(keep.sum()),
    }


def _adaptive_dbscan(point_ids: np.ndarray, points: np.ndarray) -> tuple[list[np.ndarray], dict]:
    if len(point_ids) < MIN_INSTANCE_POINTS:
        return [], {"eps": None, "cluster_count": 0, "noise_points": int(len(point_ids))}
    xyz = points[point_ids]
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(xyz))
    distances = np.asarray(cloud.compute_nearest_neighbor_distance(), dtype=float)
    positive = distances[np.isfinite(distances) & (distances > 1e-6)]
    spacing = float(np.median(positive)) if len(positive) else 0.03
    eps = float(np.clip(spacing * 4.0, 0.06, 0.18))
    labels = np.asarray(cloud.cluster_dbscan(eps=eps, min_points=8, print_progress=False))
    clusters = [point_ids[labels == label] for label in sorted(set(labels.tolist())) if label >= 0]
    return clusters, {
        "eps": eps, "nearest_neighbor_median": spacing,
        "cluster_count": len(clusters), "noise_points": int((labels < 0).sum()),
    }


def _mask_memberships(cluster: np.ndarray, fusion: SemanticFusion, label: str) -> tuple[set[int], set[int]]:
    cluster_set = set(int(value) for value in cluster)
    views: set[int] = set()
    masks: set[int] = set()
    for detection_index, detection in enumerate(fusion.detection_support):
        if detection.get("label") != label:
            continue
        overlap = sum(int(point_id) in cluster_set for point_id in detection.get("point_ids", []))
        if overlap >= MIN_MASK_OVERLAP_POINTS:
            views.add(int(detection.get("view_id", detection_index)))
            masks.add(detection_index)
    return views, masks


def _cluster_gap(a: np.ndarray, b: np.ndarray, points: np.ndarray) -> float:
    if len(a) > len(b):
        a, b = b, a
    distance, _index = cKDTree(points[b]).query(points[a], k=1)
    return float(np.min(distance)) if len(distance) else float("inf")


def _merge_mask_fragments(
    clusters: list[np.ndarray], points: np.ndarray, fusion: SemanticFusion, label: str, eps: float,
) -> tuple[list[np.ndarray], list[dict]]:
    """只有空间近邻且被多个相同 2D mask 同时覆盖的碎片才允许合并。"""
    if len(clusters) < 2:
        return clusters, []
    parent = list(range(len(clusters)))

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    def union(a: int, b: int) -> None:
        root_a, root_b = find(a), find(b)
        if root_a != root_b:
            parent[root_b] = root_a

    memberships = [_mask_memberships(cluster, fusion, label)[1] for cluster in clusters]
    events: list[dict] = []
    for left in range(len(clusters)):
        for right in range(left + 1, len(clusters)):
            shared_masks = memberships[left] & memberships[right]
            if len(shared_masks) < 2:
                continue
            gap = _cluster_gap(clusters[left], clusters[right], points)
            if gap <= max(0.12, eps * 2.5):
                union(left, right)
                events.append({
                    "source_clusters": [left, right], "shared_masks": len(shared_masks),
                    "minimum_3d_gap": gap, "reason": "spatially_close_and_multiview_mask_connected",
                })
    groups: dict[int, list[np.ndarray]] = {}
    for index, cluster in enumerate(clusters):
        groups.setdefault(find(index), []).append(cluster)
    return [np.unique(np.concatenate(group)) for group in groups.values()], events


def _candidate_point_ids(points: np.ndarray, record: dict) -> np.ndarray:
    bbox = record.get("geometry", {}).get("bbox")
    radians = bbox is not None
    if bbox is None:
        source = record.get("spatiallm_candidate", {})
        bbox = {"center": source.get("center"), "size": source.get("size"),
                "rotation_z_deg": source.get("rotation_z_deg", 0.0)}
    if not bbox or bbox.get("center") is None or bbox.get("size") is None:
        return np.empty(0, dtype=np.int64)
    center = np.asarray(bbox["center"], dtype=float)
    size = np.asarray(bbox["size"], dtype=float)
    if center.shape != (3,) or size.shape != (3,):
        return np.empty(0, dtype=np.int64)
    theta = float(bbox.get("rotation_z_deg", 0.0))
    angle = theta if radians else np.deg2rad(theta)
    delta = points - center
    lx = delta[:, 0] * np.cos(angle) + delta[:, 1] * np.sin(angle)
    ly = -delta[:, 0] * np.sin(angle) + delta[:, 1] * np.cos(angle)
    inside = (
        (np.abs(lx) <= size[0] / 2 + 0.08)
        & (np.abs(ly) <= size[1] / 2 + 0.08)
        & (np.abs(delta[:, 2]) <= size[2] / 2 + 0.08)
    )
    return np.flatnonzero(inside)


def _associate_candidates(
    cluster: np.ndarray, candidate_sets: dict[str, np.ndarray],
) -> list[dict]:
    cluster_set = set(int(value) for value in cluster)
    associations: list[dict] = []
    for candidate_id, point_ids in candidate_sets.items():
        overlap = sum(int(point_id) in cluster_set for point_id in point_ids)
        ratio = overlap / max(len(cluster), 1)
        if overlap >= MIN_MASK_OVERLAP_POINTS and ratio >= 0.05:
            associations.append({
                "candidate_id": candidate_id, "overlap_points": int(overlap),
                "instance_coverage": float(ratio),
            })
    return sorted(associations, key=lambda item: (-item["instance_coverage"], item["candidate_id"]))


def _candidate_group(record: dict) -> str | None:
    normalized = str(record.get("spatiallm_candidate", {}).get("normalized_label", ""))
    aliases = {
        "bed": "bed", "desk": "table_group", "table": "table_group",
        "small_table": "table_group", "bookshelf": "storage_group",
        "bookcase": "storage_group", "cabinet": "storage_group",
        "wardrobe": "storage_group", "chair": "chair", "stool": "stool",
        "box": "box", "sofa": "sofa", "suitcase": "suitcase",
    }
    return aliases.get(normalized)


def _candidate_consistency(
    associations: list[dict], candidate_records: list[dict], canonical_group: str,
) -> tuple[float, list[dict]]:
    records = {record.get("candidate_id"): record for record in candidate_records}
    if not associations:
        return 0.5, []
    total = sum(float(item["instance_coverage"]) for item in associations)
    compatible = sum(
        float(item["instance_coverage"]) for item in associations
        if _candidate_group(records.get(item["candidate_id"], {})) == canonical_group
    )
    enriched = []
    for item in associations:
        record = records.get(item["candidate_id"], {})
        enriched.append({
            **item, "candidate_group": _candidate_group(record),
            "semantic_compatible": _candidate_group(record) == canonical_group,
        })
    return float(compatible / max(total, 1e-12)), enriched


def _merge_track_fragments(
    clusters: list[np.ndarray], observations: list[dict], points: np.ndarray, eps: float,
    candidate_sets: dict[str, np.ndarray], candidate_records: list[dict], canonical_group: str,
) -> tuple[list[np.ndarray], list[dict], dict[int, int]]:
    """用重复的 observation-track 证据合并 DBSCAN 碎片，而不是仅看质心。"""
    if len(clusters) < 2:
        return clusters, [], {0: 1} if clusters else {}
    memberships: list[set[str]] = []
    for cluster in clusters:
        cluster_set = set(int(value) for value in cluster)
        memberships.append({
            item["observation_id"] for item in observations
            if sum(int(value) in cluster_set for value in item["projected_point_ids"]) >= MIN_MASK_OVERLAP_POINTS
        })
    parent = list(range(len(clusters)))
    records = {record.get("candidate_id"): record for record in candidate_records}

    def dominant_candidate_group(cluster: np.ndarray) -> str | None:
        associations = _associate_candidates(cluster, candidate_sets)
        if not associations or float(associations[0]["instance_coverage"]) < 0.30:
            return None
        return _candidate_group(records.get(associations[0]["candidate_id"], {}))

    fragment_candidate_groups = [dominant_candidate_group(cluster) for cluster in clusters]
    component_candidate_groups = {
        index: ({group} if group is not None else set())
        for index, group in enumerate(fragment_candidate_groups)
    }

    def find(value: int) -> int:
        while parent[value] != value:
            parent[value] = parent[parent[value]]
            value = parent[value]
        return value

    events: list[dict] = []
    for left in range(len(clusters)):
        for right in range(left + 1, len(clusters)):
            shared = memberships[left] & memberships[right]
            gap = _cluster_gap(clusters[left], clusters[right], points)
            # 两个以上独立视角的同一 track mask 同时覆盖两片点，是允许长距离合并
            # 的唯一通道；单个大 mask 不能把两个物体串起来。
            shared_views = {
                item["frame_id"] for item in observations if item["observation_id"] in shared
            }
            repeated_track = len(shared_views) >= 2
            adjacent_track = len(shared_views) >= 1 and gap <= max(0.12, eps * 2.5)
            if not (repeated_track or adjacent_track):
                continue
            root_left, root_right = find(left), find(right)
            groups_after_merge = component_candidate_groups[root_left] | component_candidate_groups[root_right]
            candidate_conflict = (
                canonical_group in groups_after_merge
                and any(group != canonical_group for group in groups_after_merge)
            )
            if candidate_conflict:
                events.append({
                    "source_fragments": [left, right], "shared_observations": sorted(shared),
                    "shared_support_views": sorted(shared_views), "minimum_3d_gap": gap,
                    "accepted": False, "reason": "spatial_candidate_semantic_conflict",
                    "fragment_candidate_groups": [
                        fragment_candidate_groups[left], fragment_candidate_groups[right],
                    ],
                })
                continue
            if root_left != root_right:
                parent[root_right] = root_left
                component_candidate_groups[root_left] = groups_after_merge
                component_candidate_groups.pop(root_right, None)
            events.append({
                "source_fragments": [left, right], "shared_observations": sorted(shared),
                "shared_support_views": sorted(shared_views), "minimum_3d_gap": gap,
                "accepted": True,
                "reason": "repeated_multiview_track_support" if repeated_track else "adjacent_track_fragment",
            })
    groups: dict[int, list[np.ndarray]] = {}
    source_counts: dict[int, int] = {}
    for index, cluster in enumerate(clusters):
        root = find(index)
        groups.setdefault(root, []).append(cluster)
        source_counts[root] = source_counts.get(root, 0) + 1
    merged = [np.unique(np.concatenate(group)) for _root, group in sorted(groups.items())]
    merged_counts = {
        output_index: source_counts[root] for output_index, root in enumerate(sorted(groups))
    }
    return merged, events, merged_counts


def build_semantic_instances(
    points: np.ndarray,
    fusion: SemanticFusion,
    structure: dict,
    object_diagnostics: dict,
) -> tuple[dict, dict, dict[str, np.ndarray]]:
    """生成跨视角关联后的实例点集；不计算或替换任何 bbox/尺寸。"""
    points = np.asarray(points, dtype=float)
    candidate_records = object_diagnostics.get("objects", [])
    candidate_sets = {
        record["candidate_id"]: _candidate_point_ids(points, record)
        for record in candidate_records
    }

    def filter_observation(point_ids: np.ndarray) -> tuple[np.ndarray, dict]:
        return _structural_filter(point_ids, points, structure)

    raw_observations = build_view_observations(points, fusion, filter_observation)
    candidate_groups = {
        record["candidate_id"]: _candidate_group(record) for record in candidate_records
    }
    observations, observation_quality, purified_point_sets = purify_observations(
        raw_observations, points, candidate_sets, candidate_groups,
    )
    tracks, association_edges = associate_observations(observations, points)
    # 保留阶段 3 的 label 级过滤统计接口，便于前后版本诊断对照；实例生成本身
    # 已改用 observation tracks，不再依赖这里的 exact-label 点集。
    label_diagnostics: list[dict] = []
    for semantic_label, normalized_label in SEMANTIC_INSTANCE_LABELS.items():
        raw_ids = np.asarray(fusion.label_point_ids(semantic_label), dtype=np.int64)
        if len(raw_ids) == 0:
            continue
        _filtered, filtering = _structural_filter(raw_ids, points, structure)
        label_diagnostics.append({
            "semantic_label": semantic_label, "normalized_label": normalized_label,
            "input_semantic_points": int(len(raw_ids)), "filtering": filtering,
            "generation_mode": "diagnostic_only_stage6_uses_observation_tracks",
        })
    all_instances: list[dict] = []
    point_sets: dict[str, np.ndarray] = {}
    track_diagnostics: list[dict] = []
    label_counters: dict[str, int] = {}
    observation_assignments: dict[str, list[str]] = {
        item["observation_id"]: [] for item in raw_observations
    }

    for track in tracks:
        track_observations = [observations[index] for index in track["observation_indices"]]
        nonempty = [item["projected_point_ids"] for item in track_observations if item["projected_point_count"] > 0]
        if not nonempty:
            continue
        union_ids = np.unique(np.concatenate(nonempty))
        support_frames: dict[int, set[int]] = {}
        for observation in track_observations:
            for point_id in observation["projected_point_ids"]:
                support_frames.setdefault(int(point_id), set()).add(int(observation["frame_id"]))
        unique_track_views = {int(item["frame_id"]) for item in track_observations}
        if len(unique_track_views) >= 2:
            track_ids = np.asarray(
                sorted(point_id for point_id, frames in support_frames.items() if len(frames) >= 2),
                dtype=np.int64,
            )
            multiview_core_fallback = len(track_ids) == 0
            if multiview_core_fallback:
                track_ids = union_ids
        else:
            track_ids = union_ids
            multiview_core_fallback = False
        filtered_ids, track_filtering = _structural_filter(track_ids, points, structure)
        clusters, clustering = _adaptive_dbscan(filtered_ids, points)
        # 小型孤立证据仍保留为 low-confidence instance，不能从诊断中静默消失。
        if not clusters and len(filtered_ids):
            clusters = [filtered_ids]
        merged, merge_events, _fragment_counts = _merge_track_fragments(
            clusters, track_observations, points, float(clustering.get("eps") or 0.06),
            candidate_sets, candidate_records, track["canonical_group"],
        )
        generated: list[str] = []
        split_reasons: list[dict] = []
        if len(merged) > 1:
            split_reasons.append({
                "reason": "spatial_fragments_lack_repeated_cross_view_track_support",
                "fragment_count": len(merged),
            })
        for cluster in sorted(merged, key=lambda ids: tuple(np.median(points[ids], axis=0))):
            cluster_set = set(int(value) for value in cluster)
            source_observations = [
                item for item in track_observations
                if sum(int(value) in cluster_set for value in item["projected_point_ids"]) >= MIN_MASK_OVERLAP_POINTS
            ]
            if not source_observations:
                continue
            label_resolution = resolve_track_label(source_observations)
            normalized_label = label_resolution["normalized_label"]
            label_counters[normalized_label] = label_counters.get(normalized_label, 0) + 1
            instance_id = f"{normalized_label}_{label_counters[normalized_label]:03d}"
            source_ids = {item["observation_id"] for item in source_observations}
            relevant_edges = [
                edge for edge in track["accepted_edges"]
                if edge["left"] in source_ids and edge["right"] in source_ids
            ]
            associations = _associate_candidates(cluster, candidate_sets)
            candidate_score, associations = _candidate_consistency(
                associations, candidate_records, label_resolution["canonical_group"],
            )
            fragment_count = max(sum(
                len(np.intersect1d(cluster, source_fragment, assume_unique=True)) >= MIN_MASK_OVERLAP_POINTS
                for source_fragment in clusters
            ), 1)
            completeness = completeness_metrics(
                source_observations, cluster, points, relevant_edges,
                fragment_count, candidate_score,
            )
            factors = completeness["factors"]
            support_views = sorted({int(item["frame_id"]) for item in source_observations})
            reasons: list[str] = []
            if len(cluster) < MIN_INSTANCE_POINTS:
                reasons.append("too_few_semantic_points")
            if len(support_views) < MIN_INSTANCE_VIEWS:
                reasons.append("too_few_support_views")
            if factors["view_diversity"] < MIN_VIEW_DIVERSITY:
                reasons.append("insufficient_view_diversity")
            if factors["boundary_stability"] < MIN_BOUNDARY_STABILITY:
                reasons.append("instance_boundary_unstable")
            if factors["semantic_consistency"] < 0.55:
                reasons.append("semantic_votes_conflicting")
            if len(source_observations) > 1 and factors["cross_view_consistency"] <= 0.0:
                reasons.append("no_verified_cross_view_association")
            if completeness["score"] < MIN_COMPLETENESS:
                reasons.append("instance_completeness_below_threshold")
            stable = not reasons
            status = "stable" if stable else ("incomplete" if len(support_views) >= 2 else "low_confidence")
            status_reason = "multiview_instance_complete" if stable else ";".join(reasons)
            votes: dict[str, float] = {}
            for point_id in cluster.tolist():
                for label, value in fusion.votes.get(int(point_id), {}).items():
                    votes[label] = votes.get(label, 0.0) + float(value)
            item = {
                "instance_id": instance_id,
                "track_id": track["track_id"],
                "semantic_label": label_resolution["semantic_label"],
                "normalized_label": normalized_label,
                "canonical_group": label_resolution["canonical_group"],
                "label_resolution": label_resolution["label_resolution"],
                "support_views": len(support_views), "support_view_ids": support_views,
                "support_masks": len(source_observations),
                "source_observations": [item["observation_id"] for item in source_observations],
                "semantic_votes": {key: round(value, 6) for key, value in sorted(votes.items())},
                "observation_semantic_votes": {
                    key: round(value, 6) for key, value in label_resolution["semantic_votes"].items()
                },
                "point_count": int(len(cluster)),
                "fragment_count": int(fragment_count),
                "merged_fragments": [
                    event for event in merge_events
                    if event.get("accepted") and source_ids.intersection(event.get("shared_observations", []))
                ],
                "merge_reasons": sorted({
                    event["reason"] for event in merge_events if event.get("accepted")
                }),
                "split_reasons": split_reasons,
                "view_diversity": round(factors["view_diversity"], 6),
                "boundary_stability": round(factors["boundary_stability"], 6),
                "instance_completeness": round(completeness["score"], 6),
                "completeness_factors": {key: round(value, 6) for key, value in factors.items()},
                "completeness_diagnostics": {
                    "view_diversity": completeness["view_diversity_detail"],
                    "boundary_stability": completeness["boundary_stability_detail"],
                    "robust_extent": completeness["robust_extent"],
                },
                "geometry_confidence": round(completeness["score"], 6),
                "status": status, "stability_reason": status_reason,
                "instance_status": status, "status_reason": status_reason,
                "bbox": None,
                "bbox_status": "pending_stage4" if stable else "not_generated_incomplete_instance",
                "source_candidates": associations,
                "point_set_ref": f"semantic_instance_points.npz#{instance_id}",
            }
            all_instances.append(item)
            point_sets[instance_id] = cluster
            generated.append(instance_id)
            for observation in source_observations:
                observation_assignments[observation["observation_id"]].append(instance_id)
        track_diagnostics.append({
            "track_id": track["track_id"], "canonical_group": track["canonical_group"],
            "source_observations": [item["observation_id"] for item in track_observations],
            "support_frames": sorted({item["frame_id"] for item in track_observations}),
            "track_point_count": int(len(track_ids)), "filtering": track_filtering,
            "union_projected_point_count": int(len(union_ids)),
            "multiview_core_point_count": int(len(track_ids)),
            "multiview_core_fallback": multiview_core_fallback,
            "dbscan": clustering, "fragment_count_before_merge": len(clusters),
            "fragment_count_after_merge": len(merged), "merge_events": merge_events,
            "split_reasons": split_reasons, "generated_instances": generated,
        })

    stable_instances = [item for item in all_instances if item["status"] == "stable"]
    candidate_results: list[dict] = []
    for record in candidate_records:
        candidate_id = record["candidate_id"]
        generated = [
            item["instance_id"] for item in all_instances
            if any(source["candidate_id"] == candidate_id for source in item["source_candidates"])
        ]
        if len(generated) > 1:
            decision = "split_into_multiple_instances"
        elif len(generated) == 1:
            decision = "associated_with_one_instance"
        else:
            decision = "no_stable_semantic_instance"
        candidate_results.append({
            "candidate_id": candidate_id,
            "spatiallm_candidate": record.get("spatiallm_candidate"),
            "generated_instance_count": len(generated),
            "generated_instances": generated,
            "decision": decision,
        })

    payload = {
        "schema_version": 2,
        "status": "applied",
        "bbox_policy": "stage6_only_complete_instances_enter_geometry",
        "counts": {
            "all_instances": len(all_instances), "stable_instances": len(stable_instances),
            "low_confidence_instances": sum(item["status"] == "low_confidence" for item in all_instances),
            "incomplete_instances": sum(item["status"] == "incomplete" for item in all_instances),
        },
        "instances": all_instances,
    }
    serialized_edges = [{
        key: (None if isinstance(value, float) and not np.isfinite(value) else value)
        for key, value in edge.items()
    } for edge in association_edges]
    observation_records = [
        serialize_observation(item, observation_assignments[item["observation_id"]])
        for item in raw_observations
    ]
    diagnostics = {
        "schema_version": 2,
        "status": "applied",
        "source": "stage2_view_observations_and_multiview_semantic_points",
        "candidate_results": candidate_results,
        "labels": label_diagnostics,
        "tracks": track_diagnostics,
        "association_edges": serialized_edges,
        "observation_count": len(observation_records),
        "observation_diagnostics_ref": "instance_observations.json",
        "instances": all_instances,
        # writer 会把完整点 ID 单独写入 instance_observations.json，避免主诊断重复膨胀。
        "_instance_observations": {
            "schema_version": 1, "status": "applied", "observations": observation_records,
        },
        "_semantic_observation_quality": observation_quality,
        "_purified_semantic_point_sets": purified_point_sets,
    }
    return payload, diagnostics, point_sets


def write_instance_outputs(
    payload: dict,
    diagnostics: dict,
    point_sets: dict[str, np.ndarray],
    instances_json: Path,
    diagnostics_json: Path,
    points_npz: Path,
    observations_json: Path | None = None,
    observation_quality_json: Path | None = None,
    purified_points_npz: Path | None = None,
) -> None:
    for path in (Path(instances_json), Path(diagnostics_json), Path(points_npz)):
        path.parent.mkdir(parents=True, exist_ok=True)
    Path(instances_json).write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    diagnostics_to_write = dict(diagnostics)
    observation_payload = diagnostics_to_write.pop("_instance_observations", None)
    observation_quality_payload = diagnostics_to_write.pop("_semantic_observation_quality", None)
    purified_point_sets = diagnostics_to_write.pop("_purified_semantic_point_sets", None)
    Path(diagnostics_json).write_text(
        json.dumps(diagnostics_to_write, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    if observations_json is not None and observation_payload is not None:
        Path(observations_json).parent.mkdir(parents=True, exist_ok=True)
        Path(observations_json).write_text(
            json.dumps(observation_payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    if observation_quality_json is not None and observation_quality_payload is not None:
        Path(observation_quality_json).parent.mkdir(parents=True, exist_ok=True)
        Path(observation_quality_json).write_text(
            json.dumps(observation_quality_payload, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    if purified_points_npz is not None and purified_point_sets is not None:
        Path(purified_points_npz).parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(Path(purified_points_npz), **purified_point_sets)
    np.savez_compressed(Path(points_npz), **point_sets)


def mark_instances_unavailable(
    structure_json: Path,
    instances_json: Path,
    diagnostics_json: Path,
    points_npz: Path,
    reason: str,
    observations_json: Path | None = None,
    observation_quality_json: Path | None = None,
    purified_points_npz: Path | None = None,
) -> None:
    structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
    structure["semantic_instance_pipeline_status"] = "unavailable"
    structure["semantic_instances"] = []
    Path(structure_json).write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
    payload = {"schema_version": 2, "status": "unavailable", "reason": reason[:300], "instances": []}
    diagnostics = {"schema_version": 2, "status": "unavailable", "reason": reason[:300], "instances": []}
    if observations_json is not None:
        diagnostics["_instance_observations"] = {
            "schema_version": 1, "status": "unavailable", "reason": reason[:300],
            "observations": [],
        }
    if observation_quality_json is not None:
        diagnostics["_semantic_observation_quality"] = {
            "schema_version": 1, "status": "unavailable", "reason": reason[:300],
            "observation_count": 0, "observations": [],
        }
    if purified_points_npz is not None:
        diagnostics["_purified_semantic_point_sets"] = {}
    write_instance_outputs(
        payload, diagnostics, {}, instances_json, diagnostics_json, points_npz, observations_json,
        observation_quality_json, purified_points_npz,
    )
