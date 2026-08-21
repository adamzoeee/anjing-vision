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
    segment_detections,
)

logger = logging.getLogger("anjing.pipeline.semantic_evidence")

MIN_KEYFRAMES = 48
MAX_KEYFRAMES = 96
# 多视角投票使用 Python 的逐点证据表，复杂度随“点数×视角×检测数”增长。
# 3 万个覆盖全房间的均匀代表点足以做语义归属，同时把普通工作站内存稳定
# 控制在可接受范围；原始点云仍保留给几何/通道测量，不会被覆盖。
MAX_SEMANTIC_POINTS = 30000

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


def _video_topology(records: list[dict]) -> dict:
    """从整段视频的时间顺序记录家具相邻关系，供框架布局优先使用。"""
    canonical = {
        "书桌": "desk", "桌子": "desk", "床": "bed", "书架": "bookshelf",
        "柜子": "cabinet", "衣柜": "cabinet", "门": "door", "窗": "window",
        "纸箱": "box", "收纳箱": "storage_rack", "行李箱": "storage_rack",
        "凳子": "stool", "椅子": "chair",
    }
    ordered_views = []
    adjacency: dict[tuple[str, str], int] = {}
    previous: set[str] = set()
    for record in sorted(records, key=lambda item: int(item.get("view_id", 0))):
        labels = {
            canonical[str(item.get("label"))]
            for item in record.get("detections", [])
            if str(item.get("label")) in canonical and float(item.get("score") or 0.0) >= 0.28
        }
        if not labels:
            continue
        ordered_views.append({"view_id": int(record.get("view_id", 0)), "labels": sorted(labels)})
        for left in previous:
            for right in labels:
                if left == right:
                    continue
                pair = tuple(sorted((left, right)))
                adjacency[pair] = adjacency.get(pair, 0) + 1
        previous = labels
    return {
        "status": "available" if len(ordered_views) >= 8 else "insufficient",
        "source": "uniform_full_video_keyframes",
        "views": ordered_views,
        "adjacency": [
            {"objects": list(pair), "support": support}
            for pair, support in sorted(adjacency.items(), key=lambda item: -item[1])
        ],
    }


def prepare_semantic_cloud(aligned_ply: Path, output_ply: Path, max_points: int = MAX_SEMANTIC_POINTS) -> Path:
    """为2D→3D投影生成同坐标系代表点云，避免重复密点耗尽系统内存。"""
    cloud = o3d.io.read_point_cloud(str(aligned_ply))
    count = len(cloud.points)
    if count == 0:
        raise RuntimeError("semantic source point cloud is empty")
    if count > max_points:
        keep = np.linspace(0, count - 1, max_points, dtype=np.int64)
        cloud = cloud.select_by_index(keep.tolist())
    output_ply = Path(output_ply)
    output_ply.parent.mkdir(parents=True, exist_ok=True)
    o3d.t.io.write_point_cloud(str(output_ply), o3d.t.geometry.PointCloud.from_legacy(cloud))
    return output_ply


def select_keyframe_indices(count: int) -> list[int]:
    """从整段视频均匀选取 24～60 帧，保留后半段补拍视角。"""
    if count <= 0:
        return []
    # 五分钟视频不能只抽约 30 帧：家具组合关系往往只在局部慢扫中出现。
    # 仍逐帧串行推理并及时释放中间张量，所以增加时间覆盖不会把全部图像
    # 同时压入 8GB 显存。
    target = min(count, int(np.clip(round(count / 10), MIN_KEYFRAMES, MAX_KEYFRAMES)))
    if target == count:
        return list(range(count))
    return sorted({int(round(value)) for value in np.linspace(0, count - 1, target)})


def _semantic_camera(camera: dict) -> dict:
    # recover_poses.py 直接保存由 solvePnP 得到并经米制坐标变换后的 R_wc，
    # 即 world-to-camera；虽然Gaussian训练侧变量曾误命名为 R_cw，这里不能转置。
    rotation_wc = np.asarray(camera["rotation"], dtype=float)
    position = np.asarray(camera["position"], dtype=float)
    return {
        "K": [
            [float(camera["fx"]), 0.0, float(camera["cx"])],
            [0.0, float(camera["fy"]), float(camera["cy"])],
            [0.0, 0.0, 1.0],
        ],
        "R": rotation_wc.tolist(),
        "t": (-rotation_wc @ position).tolist(),
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
    global_support: dict[str, set[int]] = {}
    for detection in fusion.detection_support:
        global_support.setdefault(str(detection.get("label")), set()).add(int(detection.get("view_id", -1)))
    for item in structure.get("objects", []):
        evidence = evidence_by_instance.get(item.get("instance_id"), {})
        item["semantic_status"] = evidence.get("status", "insufficient_evidence")
        item["semantic_confidence"] = evidence.get("semantic_confidence", "unknown")
        item["semantic_label"] = evidence.get("dominant_label")
        item["semantic_support_views"] = evidence.get("support_views", 0)
        # 单床房间中，点云局部错位可能让床mask无法与候选框逐点重合；若整段
        # 视频对“床”有稳定多视角证据，允许保留已做几何拟合的唯一床候选，
        # 但只给 medium，不能冒充逐点高置信度。
        if item.get("label") == "bed" and item["semantic_confidence"] not in {"high", "medium"}:
            views = global_support.get("床", set())
            if len(views) >= 3:
                item.update(
                    semantic_status="global_multiview_supported",
                    semantic_confidence="medium", semantic_label="床",
                    semantic_support_views=len(views),
                )
    _merge_semantic_openings(points, structure, fusion)
    structure["semantic_pipeline_status"] = "applied"
    diagnostics["semantic_pipeline_status"] = "applied"
    diagnostics["semantic_fusion"] = fusion.diagnostics
    return structure, diagnostics


def _merge_semantic_openings(points: np.ndarray, structure: dict, fusion: SemanticFusion) -> None:
    """用多视角门窗 mask 的3D支持点补齐 SpatialLM 漏掉的墙面开口。"""
    walls = structure.get("walls", [])
    if not walls:
        return
    for semantic_label, collection, bottom_floor in (("窗", "windows", False), ("门", "doors", True)):
        # 点云布局已有门洞时，语义仅用于核验，不能再补出重复门；窗户玻璃
        # 常缺点，但已有窗时同样避免重复。
        if structure.get(collection):
            # 已有布局开口时，2D 多视角只负责增加语义证据，绝不以 mask
            # 支持点的分位数覆盖原尺寸。门框附近的墙/门板点会把这种分位数
            # 拉到整面墙，扫描45的门高 1.76 被错误扩成了 2.60m。
            label_rows = [
                row for row in fusion.detection_support
                if row.get("label") == semantic_label and len(row.get("point_ids", [])) >= 3
            ]
            support_views = {int(row.get("view_id", -1)) for row in label_rows}
            if len(support_views) >= 2:
                for opening in structure.get(collection, []):
                    opening["semantic_support_views"] = len(support_views)
                    opening["semantic_confidence"] = "high" if len(support_views) >= 4 else "medium"
                    opening["verification_method"] = (
                        str(opening.get("verification_method") or "layout_geometry")
                        + "+multiview_semantic_confirmation"
                    )
                    if opening.get("geometry_status") == "semantic_supported":
                        opening["geometry_confidence"] = max(
                            float(opening.get("geometry_confidence") or 0.0), 0.80,
                        )
            continue
        rows = [
            row for row in fusion.detection_support
            if row.get("label") == semantic_label and len(row.get("point_ids", [])) >= 3
        ]
        support_views = {int(row.get("view_id", -1)) for row in rows}
        if len(support_views) < 2:
            continue
        # 开口表面常为玻璃/空洞，跨视角不一定投到完全相同的点。使用各视角
        # 前景深度层的并集，再做稳健分位数与墙面吸附。
        point_ids = np.unique(np.concatenate([
            np.asarray(row.get("point_ids", []), dtype=np.int64) for row in rows
        ])) if rows else np.empty(0, dtype=np.int64)
        if len(point_ids) < 30:
            continue
        selected = np.asarray(points, dtype=float)[point_ids]
        selected = selected[np.isfinite(selected).all(axis=1)]
        if len(selected) < 30:
            continue
        median = np.median(selected, axis=0)

        def wall_distance(wall: dict) -> float:
            theta = np.deg2rad(float(wall.get("rotation_z_deg", 0.0)))
            normal = np.asarray([-np.sin(theta), np.cos(theta), 0.0])
            return abs(float((median - np.asarray(wall["center"], dtype=float)) @ normal))

        wall_id, wall = min(enumerate(walls), key=lambda pair: wall_distance(pair[1]))
        theta = np.deg2rad(float(wall.get("rotation_z_deg", 0.0)))
        tangent = np.asarray([np.cos(theta), np.sin(theta), 0.0])
        wall_center = np.asarray(wall["center"], dtype=float)
        along = (selected - wall_center) @ tangent
        lo, hi = np.percentile(along, [5, 95])
        z_lo, z_hi = np.percentile(selected[:, 2], [5, 95])
        wall_length = float(wall.get("size", [0.0])[0])
        width = float(np.clip(hi - lo, 0.30, max(wall_length, 0.30)))
        room_height = float(structure.get("room", {}).get("height_m") or 2.6)
        if bottom_floor:
            z_lo = 0.0
        if collection == "windows":
            z_lo = max(float(z_lo), 0.35)
            z_hi = min(float(z_hi), room_height - 0.15)
            height = float(np.clip(z_hi - z_lo, 0.35, min(1.8, room_height - 0.5)))
        else:
            height = float(np.clip(z_hi - z_lo, 0.35, room_height))
        center = wall_center + tangent * float((lo + hi) / 2.0)
        center[2] = float(z_lo + height / 2.0)
        candidate = {
            "kind": "door" if collection == "doors" else "window",
            "center": center.tolist(), "size": [width, 0.10, height],
            "rotation_z_deg": float(wall.get("rotation_z_deg", 0.0)),
            "wall_id": int(wall.get("id", wall_id)),
            "geometry_status": "semantic_supported",
            "geometry_confidence": float(min(0.95, 0.60 + 0.05 * len(support_views))),
            "verification_method": "multiview_groundingdino_sam_wall_snap",
            "semantic_support_views": len(support_views),
            "support_points": int(len(selected)),
        }
        existing = structure.setdefault(collection, [])
        duplicate = any(
            int(item.get("wall_id", -1)) == candidate["wall_id"]
            and np.linalg.norm(np.asarray(item.get("center", [999, 999, 999])) - center) < 0.45
            for item in existing
        )
        if not duplicate:
            existing.append(candidate)
    counts = structure.setdefault("counts", {})
    counts["doors"] = len(structure.get("doors", []))
    counts["windows"] = len(structure.get("windows", []))


def _merge_openings_from_view_rays(structure: dict, records: list[dict]) -> None:
    """点云在玻璃处为空时，用2D框角点射线与墙平面求交恢复门窗。"""
    walls = structure.get("walls", [])
    if not walls:
        return
    for semantic_label, collection, floor_bottom in (("窗", "windows", False), ("门", "doors", True)):
        # 门洞的点云几何通常比2D框射线稳定；已有门时只用语义证据核验，
        # 禁止再凭射线补出第二扇门并把家具区误判成门口。窗户玻璃常无点，
        # 因而仍允许在缺窗时使用射线恢复。
        if collection == "doors" and structure.get("doors"):
            continue
        if collection == "windows" and structure.get("windows"):
            continue
        wall_hits: dict[int, list[tuple[int, np.ndarray]]] = {}
        for record in records:
            camera = record.get("camera", {})
            K = np.asarray(camera.get("K", []), dtype=float)
            R = np.asarray(camera.get("R", []), dtype=float)
            tvec = np.asarray(camera.get("t", []), dtype=float)
            if K.shape != (3, 3) or R.shape != (3, 3) or tvec.shape != (3,):
                continue
            camera_center = -R.T @ tvec
            for detection in record.get("detections", []):
                if detection.get("label") != semantic_label:
                    continue
                bbox = detection.get("bbox")
                if not isinstance(bbox, list) or len(bbox) != 4:
                    continue
                x1, y1, x2, y2 = map(float, bbox)
                pixels = np.asarray([
                    [(x1 + x2) / 2, (y1 + y2) / 2, 1.0],
                    [x1, y1, 1.0], [x2, y1, 1.0], [x2, y2, 1.0], [x1, y2, 1.0],
                ])
                rays = (R.T @ (np.linalg.inv(K) @ pixels.T)).T
                rays /= np.linalg.norm(rays, axis=1, keepdims=True) + 1e-12
                best = None
                for wall_index, wall in enumerate(walls):
                    theta = np.deg2rad(float(wall.get("rotation_z_deg", 0.0)))
                    normal = np.asarray([-np.sin(theta), np.cos(theta), 0.0])
                    tangent = np.asarray([np.cos(theta), np.sin(theta), 0.0])
                    wall_center = np.asarray(wall["center"], dtype=float)
                    denom = rays @ normal
                    valid = np.abs(denom) > 1e-5
                    distance = np.full(len(rays), np.nan)
                    distance[valid] = ((wall_center - camera_center) @ normal) / denom[valid]
                    intersections = camera_center + rays * distance[:, None]
                    half_length = float(wall.get("size", [0.0])[0]) / 2 + 0.20
                    along = (intersections - wall_center) @ tangent
                    inside = (
                        np.isfinite(distance) & (distance > 0.05)
                        & (np.abs(along) <= half_length)
                        & (intersections[:, 2] >= -0.15)
                        & (intersections[:, 2] <= float(wall.get("size", [0, 0, 2.6])[2]) + 0.15)
                    )
                    # 画面边缘的门窗框经透视后，角点射线常落到相邻墙或墙外；
                    # 中心射线命中当前有限墙面即可建立墙归属。尺寸由多个视角
                    # 的有效角点/中心交点稳健汇总，不能要求单帧至少3个角点命中。
                    if not inside[0]:
                        continue
                    score = (int(inside.sum()), -float(distance[0]))
                    if best is None or score > best[0]:
                        best = (score, wall_index, intersections[inside])
                if best is not None:
                    wall_hits.setdefault(best[1], []).append((int(record["view_id"]), best[2]))
        if not wall_hits:
            continue
        wall_index, hits = max(wall_hits.items(), key=lambda item: len({row[0] for row in item[1]}))
        support_views = {row[0] for row in hits}
        if len(support_views) < 2:
            continue
        wall = walls[wall_index]
        theta = np.deg2rad(float(wall.get("rotation_z_deg", 0.0)))
        tangent = np.asarray([np.cos(theta), np.sin(theta), 0.0])
        wall_center = np.asarray(wall["center"], dtype=float)
        hits_xyz = np.concatenate([row[1] for row in hits], axis=0)
        along = (hits_xyz - wall_center) @ tangent
        lo, hi = np.percentile(along, [15, 85])
        z_lo, z_hi = np.percentile(hits_xyz[:, 2], [15, 85])
        if floor_bottom:
            z_lo = 0.0
        room_height = float(structure.get("room", {}).get("height_m") or 2.6)
        width = float(np.clip(hi - lo, 0.30, float(wall.get("size", [0.3])[0])))
        height = float(np.clip(z_hi - z_lo, 0.35, room_height))
        center = wall_center + tangent * float((lo + hi) / 2)
        center[2] = float(z_lo + height / 2)
        candidate = {
            "kind": "door" if collection == "doors" else "window",
            "center": center.tolist(), "size": [width, 0.10, height],
            "rotation_z_deg": float(wall.get("rotation_z_deg", 0.0)),
            "wall_id": int(wall.get("id", wall_index)),
            "geometry_status": "semantic_supported", "geometry_confidence": 0.75,
            "verification_method": "multiview_bbox_ray_wall_intersection",
            "semantic_support_views": len(support_views),
        }
        existing = structure.setdefault(collection, [])
        duplicate = any(
            int(item.get("wall_id", -1)) == candidate["wall_id"]
            and np.linalg.norm(np.asarray(item.get("center", [999, 999, 999])) - center) < 0.45
            for item in existing
        )
        if not duplicate:
            existing.append(candidate)
    counts = structure.setdefault("counts", {})
    counts["doors"] = len(structure.get("doors", []))
    counts["windows"] = len(structure.get("windows", []))


def _reconcile_openings(structure: dict) -> None:
    """移除与已确认窗洞大面积重叠的旧门候选，优先保留多视角语义门。"""
    doors = list(structure.get("doors", []))
    windows = list(structure.get("windows", []))
    if len(doors) <= 1 or not windows:
        return

    def interval(item: dict) -> tuple[float, float]:
        wall_id = int(item.get("wall_id", -1))
        wall = next((row for row in structure.get("walls", []) if int(row.get("id", -2)) == wall_id), None)
        if wall is None:
            return (0.0, 0.0)
        theta = np.deg2rad(float(wall.get("rotation_z_deg", 0.0)))
        tangent = np.asarray([np.cos(theta), np.sin(theta), 0.0])
        offset = float((np.asarray(item["center"], dtype=float) - np.asarray(wall["center"], dtype=float)) @ tangent)
        half = float(item.get("size", [0.0])[0]) / 2.0
        return offset - half, offset + half

    def conflicts(door: dict) -> bool:
        dlo, dhi = interval(door)
        for window in windows:
            if int(window.get("wall_id", -1)) != int(door.get("wall_id", -2)):
                continue
            wlo, whi = interval(window)
            overlap = max(0.0, min(dhi, whi) - max(dlo, wlo))
            if overlap / max(dhi - dlo, 1e-6) >= 0.25:
                return True
        return False

    non_conflicting = [door for door in doors if not conflicts(door)]
    if non_conflicting:
        structure["doors"] = sorted(
            non_conflicting,
            key=lambda item: (
                str(item.get("geometry_status")) == "semantic_supported",
                int(item.get("semantic_support_views", 0)),
                float(item.get("geometry_confidence", 0.0)),
            ), reverse=True,
        )[:1]
    structure.setdefault("counts", {})["doors"] = len(structure.get("doors", []))


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
    saved_detections_json: Path | None = None,
) -> dict:
    """运行关键帧语义并把证据回填到结构/诊断文件。"""
    problems = preflight_semantic_models() if analyzer is analyze_image else []
    if problems:
        raise RuntimeError("；".join(problems))
    try:
        if saved_detections_json is not None and Path(saved_detections_json).is_file():
            import cv2

            saved = json.loads(Path(saved_detections_json).read_text(encoding="utf-8"))
            cameras = json.loads(Path(cameras_json).read_text(encoding="utf-8"))
            camera_by_id = {int(item.get("id", index)): item for index, item in enumerate(cameras)}
            records, view_summaries = [], []
            for view in saved.get("views", []):
                view_id = int(view.get("view_id", -1))
                camera = camera_by_id.get(view_id)
                image_path = Path(images_dir) / f"{view_id:05d}.jpg"
                bgr = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
                if camera is None or bgr is None:
                    continue
                rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
                detections = segment_detections(rgb, view.get("detections", []))
                records.append({
                    "view_id": view_id, "image_name": image_path.name,
                    "camera": _semantic_camera(camera), "image_shape": rgb.shape[:2],
                    "detections": detections,
                })
                view_summaries.append({
                    "view_id": view_id, "image_name": image_path.name, "status": "processed",
                    "detections": [{
                        "label": item.get("label"), "score": item.get("score"),
                        "mask_score": item.get("mask_score"), "mask_area_px": item.get("mask_area_px"),
                        "bbox": item.get("bbox"),
                    } for item in detections],
                })
        else:
            records, view_summaries = collect_semantic_views(cameras_json, images_dir, analyzer=analyzer)
        if not records:
            raise RuntimeError("没有可用于语义识别的关键帧")
        cloud = o3d.io.read_point_cloud(str(aligned_ply))
        points = np.asarray(cloud.points, dtype=float)
        fusion = fuse_multiview_semantics(points, records)
        _attach_observation_metadata(fusion, records)
        structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
        diagnostics = json.loads(Path(diagnostics_json).read_text(encoding="utf-8"))
        structure["video_topology"] = _video_topology(records)
        structure["layout_source"] = "video_multiview_primary"
        structure, diagnostics = annotate_structure_semantics(points, structure, diagnostics, fusion)
        _merge_openings_from_view_rays(structure, records)
        _reconcile_openings(structure)
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
