"""用参考尺寸恢复米制尺度，并对正式测量执行完整置信度门控。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


TYPE_LABELS = {
    "bed": "bed", "door": "door", "table": "desk", "desk": "desk",
    "cabinet": "cabinet", "bookshelf": "bookshelf", "sofa": "sofa",
}
METRIC_SCALE_STATUS = "metric_references"


def _dims_from_size(item: dict, *, opening: bool = False, scale: float = 1.0) -> dict:
    size = [float(value) * scale for value in item.get("size", [0, 0, 0])]
    if len(size) != 3:
        return {"length_m": None, "width_m": None, "height_m": None}
    if opening:
        return {"width_m": max(size[0], size[1]), "height_m": size[2]}
    horizontal = sorted(size[:2], reverse=True)
    return {"length_m": horizontal[0], "width_m": horizontal[1], "height_m": size[2]}


def _empty_dimensions(*, opening: bool = False) -> dict:
    if opening:
        return {"width_m": None, "height_m": None}
    return {"length_m": None, "width_m": None, "height_m": None}


def _reference_key(item: dict) -> tuple[str, str]:
    return str(item.get("object_type", "")), str(item.get("dimension", ""))


def _matching_object(structure: dict, object_type: str) -> dict | None:
    if object_type == "door":
        return next(iter(structure.get("doors", [])), None)
    wanted = TYPE_LABELS.get(object_type, object_type)
    equivalents = {wanted, object_type}
    if object_type in {"table", "desk"}:
        equivalents.update({"table", "desk", "small_table"})
    candidates = (
        list(structure.get("semantic_instances", []))
        + list(structure.get("objects", []))
        + list(structure.get("rejected_objects", []))
    )
    return next((
        item for item in candidates
        if str(item.get("normalized_label") or item.get("label") or item.get("category")) in equivalents
        and item.get("geometry_status", "verified") == "verified"
        and (
            item in structure.get("semantic_instances", [])
            or str(item.get("semantic_confidence", "")).lower() in {"high", "medium"}
        )
    ), None)


def _measured_dimension(item: dict, object_type: str, dimension: str) -> float | None:
    dims = _dims_from_size(item, opening=object_type == "door")
    value = dims.get(f"{dimension}_m")
    return float(value) if value is not None and float(value) > 1e-6 else None


def estimate_reference_scale(
    structure: dict, references: Iterable[dict], *, tolerance: float = 0.15,
) -> tuple[float | None, dict]:
    """真实米数 / 当前结构单位；正常标定失败以状态返回，不抛异常。"""
    candidates: list[float] = []
    details: list[dict] = []
    for reference in references:
        object_type, dimension = _reference_key(reference)
        item = _matching_object(structure, object_type)
        measured = _measured_dimension(item, object_type, dimension) if item else None
        if measured is None:
            details.append({**reference, "status": "not_detected"})
            continue
        scale = float(reference["meters"]) / measured
        candidates.append(scale)
        details.append({**reference, "status": "used", "model_units": measured, "scale": scale})
    if len(candidates) == 1 and len(details) >= 2:
        # 用户确实提供了多个参考值，但其中一个因为门被遮挡/语义漏检而无法
        # 取得模型单位时，不能让已经成功测得的可靠参考也失效。单参考在数学
        # 上足以完成单位换算；这里只把它降级为低置信度估算，禁止进入风险评分。
        scale = float(candidates[0])
        used = next(item for item in details if item.get("status") == "used")
        return scale, {
            "status": METRIC_SCALE_STATUS,
            "scale": scale,
            "max_relative_disagreement": None,
            "references": details,
            "forced_estimate": True,
            "confidence": "low",
            "method": "single_detected_reference_fallback",
            "reason": (
                "已提供多个参考尺寸，但仅一个在点云中成功定位；"
                "已恢复米制换算，结果标记为低置信度估算"
            ),
            "reference": {
                "object_type": used.get("object_type"),
                "dimension": used.get("dimension"),
                "meters": used.get("meters"),
                "model_units": used.get("model_units"),
            },
        }
    if len(candidates) < 2:
        return None, {
            "status": "failed", "reason": "至少需要两个成功测得的参考尺寸",
            "references": details,
        }
    # 大型水平家具的长边通常比门洞/桌深的视觉边界稳定。床长是全局尺度锚点；
    # 其他参考只做一致性审计，不能再分别拉伸各个物体。
    bed_anchor = next((
        detail for detail in details
        if detail.get("status") == "used"
        and detail.get("object_type") == "bed" and detail.get("dimension") == "length"
    ), None)
    ordered = sorted(candidates)
    midpoint = len(ordered) // 2
    median = float(ordered[midpoint]) if len(ordered) % 2 else float(
        sum(ordered[midpoint - 1:midpoint + 1]) / 2
    )
    if bed_anchor is not None:
        median = float(bed_anchor["scale"])
    disagreement = max(abs(value - median) / median for value in candidates)
    if disagreement > tolerance and bed_anchor is None:
        return None, {
            "status": "failed", "reason": "参考尺寸换算比例不一致",
            "max_relative_disagreement": disagreement, "references": details,
        }
    for detail in details:
        if detail.get("status") == "used":
            predicted = float(detail["model_units"]) * median
            actual = float(detail["meters"])
            detail.update(
                predicted_m=predicted,
                absolute_error_m=abs(predicted - actual),
                relative_error=abs(predicted - actual) / actual,
            )
    return median, {
        "status": METRIC_SCALE_STATUS, "scale": median,
        "max_relative_disagreement": disagreement, "references": details,
        "method": "trusted_bed_anchor_with_reference_consistency_audit" if bed_anchor is not None else "robust_median",
    }


def _estimate_forced_reference_scale(
    structure: dict, references: Iterable[dict], *, original_failure: dict,
) -> tuple[float | None, dict]:
    """兼容旧版输出：仅从已接受的结构对象中选择一个参考恢复近似尺度。"""
    references = [dict(item) for item in references]
    # 门的结构几何通常比语义家具片段稳定；没有门时才退到已接受家具。
    ordered = sorted(references, key=lambda item: 0 if item.get("object_type") == "door" else 1)
    details: list[dict] = []
    for reference in ordered:
        object_type, dimension = _reference_key(reference)
        if object_type == "door":
            item = next(iter(structure.get("doors", [])), None)
        else:
            wanted = TYPE_LABELS.get(object_type, object_type)
            item = next((
                candidate for candidate in structure.get("objects", [])
                if candidate.get("label") == wanted
            ), None)
        measured = _measured_dimension(item, object_type, dimension) if item else None
        if measured is None:
            details.append({**reference, "status": "not_detected"})
            continue
        scale = float(reference["meters"]) / measured
        details.append({
            **reference, "status": "used_forced", "model_units": measured, "scale": scale,
        })
        return scale, {
            "status": METRIC_SCALE_STATUS,
            "scale": scale,
            "max_relative_disagreement": None,
            "references": details,
            "forced_estimate": True,
            "confidence": "low",
            "method": "single_accepted_reference_compatibility",
            "reason": "强制兼容模式：仅使用一个已接受参考尺寸，结果仅供估算",
            "original_failure": original_failure,
        }
    return None, original_failure


def _scale_item(item: dict, scale: float) -> None:
    for key in ("center", "size"):
        if isinstance(item.get(key), list):
            item[key] = [float(value) * scale for value in item[key]]
    if isinstance(item.get("height_range_m"), list):
        item["height_range_m"] = [float(value) * scale for value in item["height_range_m"]]
    bbox = item.get("bbox")
    if isinstance(bbox, dict):
        for key in ("center", "size"):
            if isinstance(bbox.get(key), list):
                bbox[key] = [float(value) * scale for value in bbox[key]]
    dimensions = item.get("dimensions")
    if isinstance(dimensions, dict):
        for key in ("length", "width", "height"):
            if dimensions.get(key) is not None:
                dimensions[key] = float(dimensions[key]) * scale


def _scaled_structure(structure: dict, scale: float) -> dict:
    result = json.loads(json.dumps(structure))
    for item in result.get("room", {}).get("floor_polygon", []):
        for index in range(min(3, len(item))):
            item[index] *= scale
    bounds = result.get("room", {}).get("bounds_xy", {})
    for key in ("min", "max"):
        if key in bounds:
            bounds[key] = [float(value) * scale for value in bounds[key]]
    if result.get("room", {}).get("height_m") is not None:
        result["room"]["height_m"] *= scale
    for collection in (
        "walls", "doors", "windows", "objects", "rejected_objects",
        "geometric_obstacles", "semantic_instances",
    ):
        for item in result.get(collection, []):
            _scale_item(item, scale)
    result["measurement_scale"] = {"applied": scale, "method": METRIC_SCALE_STATUS}
    return result


def _apply_reference_constraints(structure: dict, references: Iterable[dict]) -> list[dict]:
    """记录参考值验证结果，禁止逐对象改轴破坏统一尺度。"""
    applied: list[dict] = []
    for reference in references:
        object_type, dimension = _reference_key(reference)
        item = _matching_object(structure, object_type)
        if item is None or not isinstance(item.get("size"), list) or len(item["size"]) != 3:
            continue
        measured = _measured_dimension(item, object_type, dimension)
        if measured is None:
            continue
        value = float(reference["meters"])
        applied.append({
            "object_type": object_type, "dimension": dimension, "meters": value,
            "measured_m": measured,
            "relative_error": abs(measured - value) / max(value, 1e-6),
            "instance_id": item.get("instance_id"), "action": "validation_only",
        })
    structure["reference_validation"] = applied
    return applied


def _apply_opening_reference_aspect(structure: dict, references: Iterable[dict]) -> list[dict]:
    """门高实测 + 已观测门框宽高比，恢复门洞宽高；不改变全局尺度。"""
    applied: list[dict] = []
    for reference in references:
        object_type, dimension = _reference_key(reference)
        if object_type != "door" or dimension != "height":
            continue
        for item in structure.get("doors", []):
            size = [float(value) for value in item.get("size", [])]
            if len(size) != 3 or size[0] <= 0 or size[2] <= 0:
                continue
            known_height = float(reference["meters"])
            width = size[0] / size[2] * known_height
            item["size"] = [width, size[1], known_height]
            item["center"][2] = known_height / 2
            item["reference_geometry"] = {
                "method": "known_height_times_observed_aspect",
                "height_m": known_height, "width_m": width,
            }
            applied.append({"object_type": "door", "height_m": known_height, "width_m": width})
    return applied


def _semantic_confidence(instance: dict) -> str:
    explicit = str(instance.get("semantic_confidence") or "").lower()
    if explicit in {"high", "medium", "low", "unknown"}:
        return explicit
    views = int(instance.get("support_views") or 0)
    return "high" if views >= 3 else "medium" if views >= 2 else "low"


def _measurement_gate(instance: dict, metric_available: bool) -> tuple[str, str]:
    if not metric_available:
        return "unavailable", "scale_unavailable"
    if _semantic_confidence(instance) not in {"high", "medium"}:
        return "unavailable", "semantic_evidence_insufficient"
    if instance.get("status") != "stable":
        return "unavailable", "instance_not_stable"
    if instance.get("geometry_status") != "verified":
        return "unavailable", "geometry_not_verified"
    if not bool(instance.get("measurement_ready")):
        return "unavailable", "incomplete_instance_geometry"
    if not isinstance(instance.get("bbox"), dict) or not isinstance(instance.get("size"), list):
        return "unavailable", "geometry_bbox_unavailable"
    return "verified", "confidence_chain_verified"


def _formal_object_measurement(
    instance: dict, scale: float | None, *, force_legacy_measurements: bool = False,
) -> tuple[dict, dict]:
    metric_available = scale is not None
    status, reason = _measurement_gate(instance, metric_available)
    semantic_confidence = _semantic_confidence(instance)
    geometry_source = instance
    forced_estimate = False
    if (
        force_legacy_measurements and metric_available and status != "verified"
        and instance.get("status") == "stable"
        and isinstance(instance.get("estimated_size"), list)
    ):
        geometry_source = {**instance, "size": instance["estimated_size"]}
        status, reason = "verified", "forced_low_confidence_geometry_estimate"
        forced_estimate = True
    dimensions = (
        _dims_from_size(geometry_source, scale=float(scale))
        if status == "verified" else _empty_dimensions()
    )
    center_source = geometry_source.get("center") or geometry_source.get("estimated_center") or []
    result = {
        "id": instance.get("instance_id"), "instance_id": instance.get("instance_id"),
        "type": instance.get("normalized_label") or instance.get("label"), **dimensions,
        "center": [float(value) * float(scale) for value in center_source]
        if status == "verified" else None,
        "rotation_z_deg": geometry_source.get("rotation_z_deg", geometry_source.get("estimated_rotation_z_deg")),
        "confidence": "low" if forced_estimate else "high" if status == "verified" else "low",
        "measurement_status": status, "measurement_reason": reason,
        "semantic_status": "reliable" if semantic_confidence in {"high", "medium"} else "insufficient",
        "semantic_confidence": semantic_confidence,
        "instance_status": instance.get("status", "unknown"),
        "instance_confidence": instance.get("instance_confidence"),
        "geometry_status": instance.get("geometry_status", "unknown"),
        "geometry_confidence": instance.get("geometry_confidence"),
        "scale_status": METRIC_SCALE_STATUS if metric_available else "failed",
        "measurement_ready": bool(instance.get("measurement_ready")),
        "risk_eligibility": "not_evaluable" if forced_estimate else "eligible" if status == "verified" else "not_evaluable",
        "source": "forced_legacy_geometry_estimate" if forced_estimate else "semantic_instance_confidence_chain",
        "forced_estimate": forced_estimate,
    }
    diagnostic = {
        **{key: result.get(key) for key in (
            "instance_id", "semantic_confidence", "instance_status", "instance_confidence",
            "semantic_status",
            "geometry_status", "geometry_confidence", "scale_status", "measurement_status",
            "measurement_reason", "measurement_ready", "risk_eligibility",
        )},
        "observed_geometry_scene_units": {
            "bbox": instance.get("bbox"), "dimensions": instance.get("dimensions"),
        },
    }
    return result, diagnostic


def _attach_geometry_estimates(instances: list[dict], geometry_diagnostics: dict | None) -> list[dict]:
    """把阶段4诊断中的临时几何作为估算候选，不改变正式 bbox/size。"""
    diagnostics = (geometry_diagnostics or {}).get("instances", [])
    by_id = {str(item.get("instance_id")): item for item in diagnostics}
    enriched: list[dict] = []
    for source in instances:
        instance = dict(source)
        diagnostic = by_id.get(str(instance.get("instance_id")), {})
        values = [diagnostic.get(key) for key in ("length", "width", "height")]
        if all(isinstance(value, (int, float)) and float(value) > 0 for value in values):
            instance["estimated_size"] = [float(value) for value in values]
            rectangle = diagnostic.get("xy_rectangle") or {}
            z_range = diagnostic.get("z_range") or {}
            center_xy = rectangle.get("center_xy")
            z_bottom, z_top = z_range.get("z_bottom"), z_range.get("z_top")
            if (
                isinstance(center_xy, list) and len(center_xy) >= 2
                and isinstance(z_bottom, (int, float)) and isinstance(z_top, (int, float))
            ):
                instance["estimated_center"] = [
                    float(center_xy[0]), float(center_xy[1]), float(z_bottom + z_top) / 2,
                ]
            instance["estimated_rotation_z_deg"] = diagnostic.get("rotation")
            instance["estimated_geometry_reason"] = diagnostic.get("reason")
        enriched.append(instance)
    return enriched


def _select_forced_measurement_instances(instances: list[dict]) -> list[dict]:
    """兼容模式下同类别只保留最强临时几何，避免碎片重复显示。"""
    verified = [item for item in instances if isinstance(item.get("size"), list)]
    best_estimates: dict[str, dict] = {}
    for item in instances:
        if isinstance(item.get("size"), list) or not isinstance(item.get("estimated_size"), list):
            continue
        label = str(item.get("normalized_label") or item.get("label") or "unknown")
        score = (
            float(item.get("geometry_confidence") or 0.0),
            int(item.get("point_count") or 0),
        )
        current = best_estimates.get(label)
        current_score = (
            float(current.get("geometry_confidence") or 0.0),
            int(current.get("point_count") or 0),
        ) if current else (-1.0, -1)
        if score > current_score:
            best_estimates[label] = item
    return verified + list(best_estimates.values())


def _materialize_forced_instance_geometry(instances: list[dict]) -> list[dict]:
    """仅为兼容测量副本补出 center/size；正式 semantic structure 不受影响。"""
    materialized: list[dict] = []
    for source in _select_forced_measurement_instances(instances):
        item = dict(source)
        if not isinstance(item.get("size"), list) and isinstance(item.get("estimated_size"), list):
            item["size"] = list(item["estimated_size"])
            item["center"] = list(item.get("estimated_center") or item.get("center") or [0.0, 0.0, 0.0])
            item["rotation_z_deg"] = item.get("estimated_rotation_z_deg") or 0.0
            item["forced_estimate"] = True
        materialized.append(item)
    return materialized


def _forced_legacy_object_measurement(source: dict, scale: float) -> dict:
    """输出旧 SpatialLM 已接受对象的近似尺寸，但永不放入风险规则。

    measurement_status 用 "estimated" 而非 "verified"：纯 SpatialLM 候选
    没有多视角语义/实例证据，其尺寸只能作展示参考，不得进入验收对比
    （验收只接受 verified 的正式测量）。"""
    return {
        "id": source.get("instance_id"), "instance_id": source.get("instance_id"),
        "type": source.get("label"), **_dims_from_size(source, scale=scale),
        "center": [float(value) * scale for value in source.get("center", [])],
        "rotation_z_deg": source.get("rotation_z_deg", 0),
        "confidence": "low", "measurement_status": "estimated",
        "measurement_reason": "forced_legacy_spatiallm_estimate",
        "semantic_status": "legacy_candidate", "semantic_confidence": "low",
        "instance_status": "legacy", "instance_confidence": source.get("support_ratio"),
        "geometry_status": source.get("geometry_status", "legacy"),
        "geometry_confidence": source.get("geometry_confidence"),
        "scale_status": METRIC_SCALE_STATUS, "measurement_ready": False,
        "risk_eligibility": "not_evaluable", "source": "forced_legacy_spatiallm_estimate",
        "forced_estimate": True,
    }


def _coverage(items: list[dict]) -> dict:
    verified = sum(
        item.get("measurement_status") == "verified"
        and item.get("risk_eligibility") == "eligible"
        for item in items
    )
    estimated = sum(bool(item.get("forced_estimate") or item.get("forced_scale")) for item in items)
    total = len(items)
    return {
        "verified_count": verified, "estimated_count": estimated,
        "unavailable_count": total - verified - estimated,
        "total_count": total, "percent": round(verified / total * 100, 1) if total else 0.0,
    }


def build_measurements(
    structure: dict,
    references: Iterable[dict],
    *,
    validation_keys: set[tuple[str, str]] | None = None,
    force_legacy_measurements: bool = False,
    geometry_diagnostics: dict | None = None,
) -> dict:
    """恢复尺度并生成只包含可信正式米制值的 measurements 数据。"""
    references = [dict(item) for item in references]
    validation_keys = validation_keys or set()
    calibration: list[dict] = []
    validation: list[dict] = []
    for item in references:
        (validation if _reference_key(item) in validation_keys else calibration).append(item)

    scale, scale_quality = estimate_reference_scale(structure, calibration)
    if scale is None and force_legacy_measurements:
        scale, scale_quality = _estimate_forced_reference_scale(
            structure, calibration, original_failure=scale_quality,
        )
    metric_available = scale is not None and scale_quality.get("status") == METRIC_SCALE_STATUS
    forced_scale = bool(scale_quality.get("forced_estimate"))
    metric_structure = _scaled_structure(structure, float(scale)) if metric_available else None
    if metric_structure is not None:
        scale_quality["constraints_applied"] = _apply_reference_constraints(
            metric_structure, [*calibration, *validation],
        )

    room = (metric_structure or {}).get("room", {})
    bounds = room.get("bounds_xy", {})
    lo, hi = bounds.get("min"), bounds.get("max")
    if metric_available and isinstance(lo, list) and isinstance(hi, list) and len(lo) >= 2 and len(hi) >= 2:
        horizontal = sorted([float(hi[0]) - float(lo[0]), float(hi[1]) - float(lo[1])], reverse=True)
        room_result = {
            "length_m": horizontal[0], "width_m": horizontal[1],
            "height_m": float(room.get("height_m", 0)) or None,
            "confidence": "low" if forced_scale else "medium", "measurement_status": "verified",
            "measurement_reason": "forced_single_reference_scale" if forced_scale else "metric_scale_available",
            "source": "aligned_pointcloud_bounds", "forced_scale": forced_scale,
        }
    else:
        room_result = {
            **_empty_dimensions(), "confidence": "unknown",
            "measurement_status": "unavailable", "measurement_reason": "scale_unavailable",
            "source": "formal_metric_measurement_unavailable",
        }

    openings: list[dict] = []
    reference_by_key = {_reference_key(item): item for item in references}
    for kind in ("doors", "windows"):
        singular = "door" if kind == "doors" else "window"
        raw_items = structure.get(kind, [])
        scaled_items = (metric_structure or {}).get(kind, [])
        for index, raw_source in enumerate(raw_items, 1):
            support_views = int(raw_source.get("semantic_support_views") or 0)
            geometry_verified = raw_source.get("geometry_status") == "verified"
            geometry_supported = geometry_verified or (
                raw_source.get("geometry_status") == "semantic_supported" and support_views >= 3
            )
            ready = bool(metric_available and geometry_supported)
            scaled_source = scaled_items[index - 1] if ready else {}
            dimensions = _dims_from_size(scaled_source, opening=True) if ready else _empty_dimensions(opening=True)
            reference_height = reference_by_key.get((singular, "height"))
            reference_aspect = False
            if ready and reference_height is not None:
                raw_size = [float(value) for value in raw_source.get("size", [])]
                if len(raw_size) == 3 and raw_size[0] > 0 and raw_size[2] > 0:
                    known_height = float(reference_height["meters"])
                    dimensions = {
                        "width_m": raw_size[0] / raw_size[2] * known_height,
                        "height_m": known_height,
                    }
                    reference_aspect = True
            openings.append({
                "id": f"{singular}_{index:02d}", "type": singular,
                **dimensions,
                "center": scaled_source.get("center") if ready else None,
                "confidence": "low" if ready and forced_scale else "medium" if ready else "unknown",
                "measurement_status": "verified" if ready else "unavailable",
                "measurement_reason": "reference_height_and_observed_aspect" if reference_aspect
                else "structural_geometry_and_scale_verified" if ready
                else "scale_unavailable" if not metric_available else "geometry_not_verified",
                "semantic_confidence": "structural_verified" if geometry_verified else "multiview_supported" if geometry_supported else "unknown",
                "geometry_status": raw_source.get("geometry_status", "unknown"),
                "geometry_confidence": raw_source.get("geometry_confidence"),
                "scale_status": scale_quality.get("status", "failed"), "measurement_ready": ready,
                "risk_eligibility": "eligible" if ready and not forced_scale else "not_evaluable",
                "source": "reference_calibrated_opening_aspect" if reference_aspect else "verified_opening_geometry",
                "forced_scale": forced_scale,
            })

    objects: list[dict] = []
    diagnostics: list[dict] = []
    semantic_instances = structure.get("semantic_instances", [])
    if isinstance(semantic_instances, list):
        semantic_instances = _attach_geometry_estimates(semantic_instances, geometry_diagnostics)
        if force_legacy_measurements:
            semantic_instances = _select_forced_measurement_instances(semantic_instances)
        for instance in semantic_instances:
            result, diagnostic = _formal_object_measurement(
                instance, float(scale) if metric_available else None,
                force_legacy_measurements=force_legacy_measurements,
            )
            result["scale_status"] = scale_quality.get("status", "failed")
            diagnostic["scale_status"] = scale_quality.get("status", "failed")
            if forced_scale and result.get("measurement_status") == "verified":
                result.update(
                    confidence="low", risk_eligibility="not_evaluable", forced_scale=True,
                )
                diagnostic.update(risk_eligibility="not_evaluable", forced_scale=True)
            objects.append(result)
            diagnostics.append(diagnostic)
    if force_legacy_measurements and metric_available:
        existing_ids = {str(item.get("instance_id")) for item in objects}
        for source in structure.get("objects", []):
            if not isinstance(source.get("size"), list):
                continue
            if str(source.get("instance_id")) in existing_ids:
                continue
            objects.append(_forced_legacy_object_measurement(source, float(scale)))

    checks: list[dict] = []
    for truth in validation:
        object_type, dimension = str(truth["object_type"]), str(truth["dimension"])
        actual = float(truth["meters"])
        if object_type == "door":
            candidate = next((item for item in openings if item["type"] == "door" and item["measurement_status"] == "verified"), None)
        else:
            wanted = TYPE_LABELS.get(object_type, object_type)
            candidate = next((
                item for item in objects
                if item["type"] in {wanted, object_type}
                and item["measurement_status"] == "verified"
            ), None)
        predicted = candidate.get(f"{dimension}_m") if candidate else None
        checks.append({
            **truth, "predicted_m": predicted,
            "absolute_error_m": abs(predicted - actual) if predicted is not None else None,
            "relative_error": abs(predicted - actual) / actual if predicted is not None else None,
            "status": "compared" if predicted is not None else "not_evaluable",
            "reason": None if predicted is not None else "formal_measurement_unavailable",
        })

    coverage = _coverage(objects)
    geometry_failures = [item for item in diagnostics if item["measurement_status"] != "verified"]
    final_errors = [item for item in checks if item["status"] != "compared"]
    return {
        "schema_version": 2, "coordinate_unit": "meters" if metric_available else "scene_units",
        "metric_scale_available": metric_available,
        "force_legacy_measurements": bool(force_legacy_measurements),
        "scale": {
            **scale_quality, "scale_factor": float(scale) if metric_available else None,
            "global_rescale_applied": metric_available,
            "calibration_sources": calibration, "calibration_references": calibration,
            "validation_references": validation,
            "calibration_consistency": scale_quality.get("max_relative_disagreement"),
        },
        "room": room_result, "openings": openings, "objects": objects,
        "measurement_coverage": coverage,
        "passage": {
            "status": "pending" if metric_available else "not_evaluable",
            "assessment_status": "not_evaluable",
            "reason": "awaiting_trusted_object_geometry" if metric_available else "scale_unavailable",
        },
        "quality": {
            "validation": checks,
            "scale_error": {
                "status": "none" if metric_available else "present",
                "reason": None if metric_available else scale_quality.get("reason", "scale_unavailable"),
                "calibration_consistency": scale_quality.get("max_relative_disagreement"),
            },
            "instance_geometry_error": {
                "status": "present" if geometry_failures else "none", "count": len(geometry_failures),
                "instances": [item["instance_id"] for item in geometry_failures],
            },
            "final_measurement_error": {
                "status": "present" if final_errors else "none",
                "not_evaluable_count": len(final_errors),
                "validation_error": [item.get("relative_error") for item in checks if item.get("relative_error") is not None],
            },
        },
        "diagnostics": {"objects": diagnostics},
    }


def build_risk_inputs(measurements: dict) -> dict:
    """把 formal measurements 转为带资格状态的规则输入。"""
    door = next((
        item for item in measurements.get("openings", [])
        if item.get("type") == "door"
        and item.get("measurement_status") == "verified"
        and item.get("risk_eligibility") == "eligible"
    ), None)
    passage = measurements.get("passage") or {}
    passage_eligible = (
        passage.get("status") == "ok" and passage.get("risk_eligibility") == "eligible"
    )
    eligibility = {
        "door_width": {
            "status": "eligible" if door else "not_evaluable",
            "reason": None if door else "insufficient_measurement_confidence",
        },
        "passage_width": {
            "status": "eligible" if passage_eligible else "not_evaluable",
            "reason": None if passage_eligible else passage.get("reason", "insufficient_measurement_confidence"),
        },
        "threshold": {
            "status": "eligible" if passage_eligible else "not_evaluable",
            "reason": None if passage_eligible else passage.get("reason", "insufficient_measurement_confidence"),
        },
        "stairs": {
            "status": "eligible" if passage_eligible else "not_evaluable",
            "reason": None if passage_eligible else passage.get("reason", "insufficient_measurement_confidence"),
        },
        "slope": {
            "status": "eligible" if passage_eligible else "not_evaluable",
            "reason": None if passage_eligible else passage.get("reason", "insufficient_measurement_confidence"),
        },
        "uneven": {"status": "not_evaluable", "reason": "measurement_unavailable"},
        "obstacle": {"status": "not_evaluable", "reason": "spatial_obstacle_validation_unavailable"},
        "bathroom_door": {"status": "not_evaluable", "reason": "bathroom_door_not_identified"},
    }
    return {
        "door_width_m": door.get("width_m") if door else None,
        "passage_width_m": passage.get("passage_width_m") if passage_eligible else None,
        "threshold_m": passage.get("threshold_m") if passage_eligible else None,
        "stairs_exist": passage.get("stairs_exist") if passage_eligible else None,
        "slope": passage.get("slope") if passage_eligible else None,
        "uneven_m": None, "obstacles_in_passage": None, "bathroom_door_m": None,
        "risk_eligibility": eligibility,
    }


def build_measurements_file(
    structure_json: Path,
    output_json: Path,
    references: Iterable[dict],
    *,
    validation_keys=None,
    calibrated_structure_json: Path | None = None,
    diagnostics_json: Path | None = None,
    geometry_diagnostics_json: Path | None = None,
    force_legacy_measurements: bool = False,
) -> dict:
    structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
    references = [dict(item) for item in references]
    geometry_diagnostics = None
    if geometry_diagnostics_json is not None and Path(geometry_diagnostics_json).is_file():
        geometry_diagnostics = json.loads(Path(geometry_diagnostics_json).read_text(encoding="utf-8"))
    measurement_structure = structure
    if force_legacy_measurements and geometry_diagnostics is not None:
        measurement_structure = json.loads(json.dumps(structure))
        enriched = _attach_geometry_estimates(
            list(measurement_structure.get("semantic_instances", [])), geometry_diagnostics,
        )
        measurement_structure["semantic_instances"] = _materialize_forced_instance_geometry(enriched)
    result = build_measurements(
        measurement_structure, references, validation_keys=validation_keys,
        force_legacy_measurements=force_legacy_measurements,
        geometry_diagnostics=geometry_diagnostics,
    )
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    scale = result.get("scale", {}).get("scale_factor")
    if calibrated_structure_json is not None and result.get("metric_scale_available") and scale:
        calibrated = _scaled_structure(measurement_structure, float(scale))
        _apply_reference_constraints(calibrated, references)
        calibrated["opening_reference_geometry"] = _apply_opening_reference_aspect(calibrated, references)
        Path(calibrated_structure_json).write_text(
            json.dumps(calibrated, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    elif calibrated_structure_json is not None and Path(calibrated_structure_json).is_file():
        # 重试后标定失败时不能继续暴露上一次运行留下的米制结构。
        Path(calibrated_structure_json).unlink()
    if diagnostics_json is not None:
        Path(diagnostics_json).parent.mkdir(parents=True, exist_ok=True)
        Path(diagnostics_json).write_text(
            json.dumps({
                "schema_version": 1,
                "scale_status": result.get("scale", {}).get("status"),
                "metric_scale_available": result.get("metric_scale_available"),
                "measurement_coverage": result.get("measurement_coverage"),
                "objects": result.get("diagnostics", {}).get("objects", []),
            }, ensure_ascii=False, indent=2), encoding="utf-8",
        )
    return result
