"""把现有 GroundingDINO + SAM 结果以非破坏方式接入 2.5D 结构链。"""
from __future__ import annotations

import json
import logging
from pathlib import Path

import numpy as np
import open3d as o3d

from pipeline.semantic import (
    SemanticFusion,
    analyze_image,
    fuse_multiview_semantics,
    model_runtime_info,
    preflight_semantic_models,
    release_semantic_models,
)

logger = logging.getLogger("anjing.pipeline.semantic_evidence")

MIN_KEYFRAMES = 12
MAX_KEYFRAMES = 30

SEMANTIC_TO_STRUCTURE = {
    "床": "bed", "书桌": "desk", "桌子": "table", "椅子": "chair",
    "凳子": "stool", "书架": "bookshelf", "柜子": "cabinet",
    "衣柜": "wardrobe", "纸箱": "box", "收纳箱": "box", "杂物": "box",
    "沙发": "sofa", "门": "door", "窗": "window", "行李箱": "suitcase",
}
STRUCTURE_EQUIVALENTS = {
    "desk": {"desk", "table"},
    "table": {"table", "desk"},
    "small_table": {"table", "desk"},
    "cabinet": {"cabinet", "wardrobe", "bookshelf"},
    "wardrobe": {"wardrobe", "cabinet"},
    "bookshelf": {"bookshelf", "cabinet"},
}


def select_keyframe_indices(count: int) -> list[int]:
    """均匀选取 12～30 帧；短序列不重复补帧。"""
    if count <= 0:
        return []
    target = min(count, int(np.clip(round(count / 20), MIN_KEYFRAMES, MAX_KEYFRAMES)))
    if target == count:
        return list(range(count))
    return sorted({int(round(value)) for value in np.linspace(0, count - 1, target)})


def _semantic_camera(camera: dict) -> dict:
    rotation = np.asarray(camera["rotation"], dtype=float)
    position = np.asarray(camera["position"], dtype=float)
    return {
        "K": [
            [float(camera["fx"]), 0.0, float(camera["cx"])],
            [0.0, float(camera["fy"]), float(camera["cy"])],
            [0.0, 0.0, 1.0],
        ],
        "R": rotation.tolist(),
        "t": (-rotation @ position).tolist(),
        "camera_model": "PINHOLE",
        "radial_distortion": [],
        "image_size": [int(camera["width"]), int(camera["height"])],
    }


def collect_semantic_views(
    cameras_json: Path,
    images_dir: Path,
    *,
    analyzer=analyze_image,
) -> tuple[list[dict], list[dict]]:
    """对均匀关键帧运行 DINO→SAM，返回内存投票记录和可序列化摘要。"""
    import cv2

    cameras = json.loads(Path(cameras_json).read_text(encoding="utf-8"))
    indices = select_keyframe_indices(len(cameras))
    records: list[dict] = []
    summaries: list[dict] = []
    for index in indices:
        camera = cameras[index]
        view_id = int(camera.get("id", index))
        image_path = Path(images_dir) / f"{view_id:05d}.jpg"
        bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
        if bgr is None:
            summaries.append({"view_id": view_id, "image_name": image_path.name, "status": "image_missing"})
            continue
        rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        detections = analyzer(rgb)
        records.append({
            "view_id": view_id,
            "image_name": image_path.name,
            "camera": _semantic_camera(camera),
            "image_shape": rgb.shape[:2],
            "detections": detections,
        })
        summaries.append({
            "view_id": view_id,
            "image_name": image_path.name,
            "status": "processed",
            "detections": [
                {
                    "label": item.get("label"), "score": item.get("score"),
                    "mask_score": item.get("mask_score"),
                    "mask_area_px": item.get("mask_area_px"),
                    "bbox": item.get("bbox"),
                }
                for item in detections
            ],
        })
    return records, summaries


def _attach_observation_metadata(fusion: SemanticFusion, records: list[dict]) -> None:
    """给 mask 投影补充轨迹所需相机元数据，不改变阶段 2 的投票结果。"""
    record_by_view = {int(record["view_id"]): (order, record) for order, record in enumerate(records)}
    mask_counters: dict[int, int] = {}
    for detection in fusion.detection_support:
        view_id = int(detection.get("view_id", 0))
        mask_id = mask_counters.get(view_id, 0)
        mask_counters[view_id] = mask_id + 1
        order, record = record_by_view.get(view_id, (view_id, {}))
        camera = record.get("camera", {})
        rotation = np.asarray(camera.get("R", []), dtype=float)
        translation = np.asarray(camera.get("t", []), dtype=float)
        if rotation.shape == (3, 3) and translation.shape == (3,):
            position = -rotation.T @ translation
            direction = rotation.T @ np.asarray([0.0, 0.0, 1.0])
            detection["camera_position"] = position.tolist()
            detection["camera_direction"] = direction.tolist()
        detection.update(
            observation_id=f"obs_{view_id:05d}_{mask_id:03d}",
            mask_id=mask_id, frame_order=order, camera_id=view_id,
        )


def _points_in_bbox(
    points: np.ndarray, bbox: dict | None, *, rotation_is_radians: bool = False,
) -> np.ndarray:
    if not bbox or bbox.get("center") is None or bbox.get("size") is None:
        return np.empty(0, dtype=np.int64)
    center = np.asarray(bbox["center"], dtype=float)
    size = np.asarray(bbox["size"], dtype=float)
    if center.shape != (3,) or size.shape != (3,) or np.any(size <= 0):
        return np.empty(0, dtype=np.int64)
    theta = float(bbox.get("rotation_z_deg", 0.0))
    # structure_builder 的拟合结果历史上写入弧度，而 SpatialLM 原始候选是度；
    # 由数据来源显式区分，不能用数值大小猜测（1° 与 1 rad 都是合法值）。
    angle = theta if rotation_is_radians else np.deg2rad(theta)
    delta = points - center
    lx = delta[:, 0] * np.cos(angle) + delta[:, 1] * np.sin(angle)
    ly = -delta[:, 0] * np.sin(angle) + delta[:, 1] * np.cos(angle)
    inside = (
        (np.abs(lx) <= size[0] / 2 + 0.08)
        & (np.abs(ly) <= size[1] / 2 + 0.08)
        & (np.abs(delta[:, 2]) <= size[2] / 2 + 0.08)
    )
    return np.flatnonzero(inside)


def _object_semantic_evidence(
    points: np.ndarray,
    record: dict,
    fusion: SemanticFusion,
) -> dict:
    bbox = record.get("geometry", {}).get("bbox")
    rotation_is_radians = bbox is not None
    if bbox is None:
        source = record.get("spatiallm_candidate", {})
        bbox = {"center": source.get("center"), "size": source.get("size"),
                "rotation_z_deg": source.get("rotation_z_deg", 0.0)}
    point_ids = _points_in_bbox(points, bbox, rotation_is_radians=rotation_is_radians)
    point_mask = np.zeros(len(points), dtype=bool)
    point_mask[point_ids] = True
    detection_rows: list[dict] = []
    labels_by_frame: dict[str, set[int]] = {}
    label_projected_points: dict[str, set[int]] = {}
    for detection in fusion.detection_support:
        ids = np.asarray(detection.get("point_ids", []), dtype=np.int64)
        if len(ids) == 0:
            continue
        overlap = ids[point_mask[ids]]
        if len(overlap) < 3:
            continue
        label = str(detection["label"])
        view_id = int(detection["view_id"])
        labels_by_frame.setdefault(label, set()).add(view_id)
        label_projected_points.setdefault(label, set()).update(int(value) for value in overlap)
        detection_rows.append({
            "view_id": view_id,
            "image_name": detection.get("image_name"),
            "label": label,
            "score": detection.get("score"),
            "mask_score": detection.get("mask_score"),
            "mask_area_px": detection.get("mask_area_px"),
            "projected_point_count": int(len(overlap)),
        })

    semantic_point_labels: dict[str, int] = {}
    weighted_votes: dict[str, float] = {}
    for point_id in point_ids.tolist():
        label = fusion.point_labels.get(point_id)
        if label:
            semantic_point_labels[label] = semantic_point_labels.get(label, 0) + 1
        for voted_label, value in fusion.votes.get(point_id, {}).items():
            weighted_votes[voted_label] = weighted_votes.get(voted_label, 0.0) + float(value)

    source_label = record.get("spatiallm_candidate", {}).get("normalized_label", "unknown")
    equivalents = STRUCTURE_EQUIVALENTS.get(source_label, {source_label})
    target_labels = {
        semantic_label for semantic_label, structure_label in SEMANTIC_TO_STRUCTURE.items()
        if structure_label in equivalents
    }
    target_frames = sorted({
        view for label in target_labels for view in labels_by_frame.get(label, set())
    })
    target_points = sum(semantic_point_labels.get(label, 0) for label in target_labels)
    top_label = None
    if semantic_point_labels:
        top_label = max(semantic_point_labels.items(), key=lambda item: (item[1], item[0]))[0]
    if len(target_frames) >= 3 and target_points >= 10:
        status, confidence = "supported", "high"
    elif len(target_frames) >= 2 and target_points > 0:
        status, confidence = "supported", "medium"
    elif detection_rows or semantic_point_labels:
        status, confidence = "conflicting_or_insufficient", "low"
    else:
        status, confidence = "insufficient_evidence", "unknown"
    return {
        "status": status,
        "semantic_confidence": confidence,
        "support_views": len(target_frames),
        "support_view_ids": target_frames,
        "groundingdino_detections": len(detection_rows),
        "sam_masks": len(detection_rows),
        "semantic_votes": {key: round(value, 6) for key, value in sorted(weighted_votes.items())},
        "semantic_point_count": int(sum(semantic_point_labels.values())),
        "semantic_point_labels": semantic_point_labels,
        "dominant_label": top_label,
        "candidate_point_count": int(len(point_ids)),
        "detections": detection_rows,
    }


def annotate_structure_semantics(
    points: np.ndarray,
    structure: dict,
    diagnostics: dict,
    fusion: SemanticFusion,
) -> tuple[dict, dict]:
    """仅附加语义证据与置信度；不删除对象、不改类别、不改 bbox/尺寸。"""
    evidence_by_instance: dict[str, dict] = {}
    for record in diagnostics.get("objects", []):
        evidence = _object_semantic_evidence(points, record, fusion)
        record["semantic_evidence"] = evidence
        if record.get("instance_id"):
            evidence_by_instance[record["instance_id"]] = evidence
    for item in structure.get("objects", []):
        evidence = evidence_by_instance.get(item.get("instance_id"), {})
        item["semantic_status"] = evidence.get("status", "insufficient_evidence")
        item["semantic_confidence"] = evidence.get("semantic_confidence", "unknown")
        item["semantic_label"] = evidence.get("dominant_label")
        item["semantic_support_views"] = evidence.get("support_views", 0)
    structure["semantic_pipeline_status"] = "applied"
    diagnostics["semantic_pipeline_status"] = "applied"
    diagnostics["semantic_fusion"] = fusion.diagnostics
    return structure, diagnostics


def mark_semantics_unavailable(
    structure_json: Path,
    diagnostics_json: Path,
    reason: str,
) -> None:
    """语义环境/位姿不可用时安全降级，保留全部 baseline 对象。"""
    structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
    diagnostics = json.loads(Path(diagnostics_json).read_text(encoding="utf-8"))
    for item in structure.get("objects", []):
        item.update(
            semantic_status="unavailable", semantic_confidence="unknown",
            semantic_label=None, semantic_support_views=0,
        )
    for record in diagnostics.get("objects", []):
        record["semantic_evidence"] = {
            "status": "unavailable", "reason": reason[:300], "support_views": 0,
            "support_view_ids": [], "groundingdino_detections": 0, "sam_masks": 0,
            "semantic_votes": {}, "semantic_point_count": 0,
        }
    structure["semantic_pipeline_status"] = "unavailable"
    diagnostics["semantic_pipeline_status"] = "unavailable"
    diagnostics["semantic_unavailable_reason"] = reason[:300]
    Path(structure_json).write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
    Path(diagnostics_json).write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")


def run_semantic_enrichment(
    aligned_ply: Path,
    cameras_json: Path,
    images_dir: Path,
    structure_json: Path,
    diagnostics_json: Path,
    output_json: Path,
    *,
    analyzer=analyze_image,
    instances_json: Path | None = None,
    instance_diagnostics_json: Path | None = None,
    instance_points_npz: Path | None = None,
    instance_observations_json: Path | None = None,
    observation_quality_json: Path | None = None,
    purified_points_npz: Path | None = None,
) -> dict:
    """运行关键帧语义并把证据回填到结构/诊断文件。"""
    problems = preflight_semantic_models() if analyzer is analyze_image else []
    if problems:
        raise RuntimeError("；".join(problems))
    try:
        records, view_summaries = collect_semantic_views(cameras_json, images_dir, analyzer=analyzer)
        if not records:
            raise RuntimeError("没有可用于语义识别的关键帧")
        cloud = o3d.io.read_point_cloud(str(aligned_ply))
        points = np.asarray(cloud.points, dtype=float)
        fusion = fuse_multiview_semantics(points, records)
        _attach_observation_metadata(fusion, records)
        structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
        diagnostics = json.loads(Path(diagnostics_json).read_text(encoding="utf-8"))
        structure, diagnostics = annotate_structure_semantics(points, structure, diagnostics, fusion)
        instance_summary = None
        if instances_json is not None and instance_diagnostics_json is not None and instance_points_npz is not None:
            from pipeline.instance_builder import build_semantic_instances, write_instance_outputs

            instance_payload, instance_diagnostics, point_sets = build_semantic_instances(
                points, fusion, structure, diagnostics,
            )
            write_instance_outputs(
                instance_payload, instance_diagnostics, point_sets,
                instances_json, instance_diagnostics_json, instance_points_npz,
                instance_observations_json,
                observation_quality_json, purified_points_npz,
            )
            stable_instances = [
                item for item in instance_payload["instances"] if item["status"] == "stable"
            ]
            structure["semantic_instance_pipeline_status"] = "applied"
            structure["semantic_instances"] = stable_instances
            diagnostics["instance_pipeline_status"] = "applied"
            instance_summary = instance_payload["counts"]
        Path(structure_json).write_text(json.dumps(structure, ensure_ascii=False, indent=2), encoding="utf-8")
        Path(diagnostics_json).write_text(json.dumps(diagnostics, ensure_ascii=False, indent=2), encoding="utf-8")
        payload = {
            "schema_version": 1,
            "status": "applied",
            "keyframes_requested": len(select_keyframe_indices(len(json.loads(Path(cameras_json).read_text(encoding="utf-8"))))),
            "keyframes_processed": len(records),
            "views": view_summaries,
            "fusion": fusion.diagnostics,
            "runtime": model_runtime_info(),
            "instances": instance_summary,
        }
        output_json = Path(output_json)
        output_json.parent.mkdir(parents=True, exist_ok=True)
        output_json.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        return payload
    finally:
        release_semantic_models()
