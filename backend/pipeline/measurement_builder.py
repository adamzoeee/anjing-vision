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
    label = str(item.get("label") or item.get("normalized_label") or "")
    if label == "desk":
        return {"length_m": horizontal[1], "width_m": horizontal[0], "height_m": size[2]}
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
    candidates = list(structure.get("objects", [])) + list(structure.get("rejected_objects", []))
    return next((item for item in candidates if item.get("label") == wanted), None)


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
    if len(candidates) < 2:
        return None, {
            "status": "failed", "reason": "至少需要两个成功测得的参考尺寸",
            "references": details,
        }
    ordered = sorted(candidates)
    midpoint = len(ordered) // 2
    median = float(ordered[midpoint]) if len(ordered) % 2 else float(
        sum(ordered[midpoint - 1:midpoint + 1]) / 2
    )
    disagreement = max(abs(value - median) / median for value in candidates)
    if disagreement > tolerance:
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
    }


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


def _formal_object_measurement(instance: dict, scale: float | None) -> tuple[dict, dict]:
    metric_available = scale is not None
    status, reason = _measurement_gate(instance, metric_available)
    semantic_confidence = _semantic_confidence(instance)
    dimensions = _dims_from_size(instance, scale=float(scale)) if status == "verified" else _empty_dimensions()
    result = {
        "id": instance.get("instance_id"), "instance_id": instance.get("instance_id"),
        "type": instance.get("normalized_label") or instance.get("label"), **dimensions,
        "center": [float(value) * float(scale) for value in instance.get("center", [])]
        if status == "verified" else None,
        "rotation_z_deg": instance.get("rotation_z_deg"),
        "confidence": "high" if status == "verified" else "low",
        "measurement_status": status, "measurement_reason": reason,
        "semantic_status": "reliable" if semantic_confidence in {"high", "medium"} else "insufficient",
        "semantic_confidence": semantic_confidence,
        "instance_status": instance.get("status", "unknown"),
        "instance_confidence": instance.get("instance_confidence"),
        "geometry_status": instance.get("geometry_status", "unknown"),
        "geometry_confidence": instance.get("geometry_confidence"),
        "scale_status": METRIC_SCALE_STATUS if metric_available else "failed",
        "measurement_ready": bool(instance.get("measurement_ready")),
        "risk_eligibility": "eligible" if status == "verified" else "not_evaluable",
        "source": "semantic_instance_confidence_chain",
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


def _coverage(items: list[dict]) -> dict:
    verified = sum(item.get("measurement_status") == "verified" for item in items)
    total = len(items)
    return {
        "verified_count": verified, "unavailable_count": total - verified,
        "total_count": total, "percent": round(verified / total * 100, 1) if total else 0.0,
    }


def build_measurements(
    structure: dict,
    references: Iterable[dict],
    *,
    validation_keys: set[tuple[str, str]] | None = None,
) -> dict:
    """恢复尺度并生成只包含可信正式米制值的 measurements 数据。"""
    references = [dict(item) for item in references]
    validation_keys = validation_keys or set()
    calibration: list[dict] = []
    validation: list[dict] = []
    for item in references:
        (validation if _reference_key(item) in validation_keys else calibration).append(item)

    scale, scale_quality = estimate_reference_scale(structure, calibration)
    metric_available = scale is not None and scale_quality.get("status") == METRIC_SCALE_STATUS
    metric_structure = _scaled_structure(structure, float(scale)) if metric_available else None

    room = (metric_structure or {}).get("room", {})
    bounds = room.get("bounds_xy", {})
    lo, hi = bounds.get("min"), bounds.get("max")
    if metric_available and isinstance(lo, list) and isinstance(hi, list) and len(lo) >= 2 and len(hi) >= 2:
        horizontal = sorted([float(hi[0]) - float(lo[0]), float(hi[1]) - float(lo[1])], reverse=True)
        room_result = {
            "length_m": horizontal[0], "width_m": horizontal[1],
            "height_m": float(room.get("height_m", 0)) or None,
            "confidence": "medium", "measurement_status": "verified",
            "measurement_reason": "metric_scale_available", "source": "aligned_pointcloud_bounds",
        }
    else:
        room_result = {
            **_empty_dimensions(), "confidence": "unknown",
            "measurement_status": "unavailable", "measurement_reason": "scale_unavailable",
            "source": "formal_metric_measurement_unavailable",
        }

    openings: list[dict] = []
    for kind in ("doors", "windows"):
        singular = "door" if kind == "doors" else "window"
        raw_items = structure.get(kind, [])
        scaled_items = (metric_structure or {}).get(kind, [])
        for index, raw_source in enumerate(raw_items, 1):
            geometry_verified = raw_source.get("geometry_status") == "verified"
            ready = bool(metric_available and geometry_verified)
            scaled_source = scaled_items[index - 1] if ready else {}
            openings.append({
                "id": f"{singular}_{index:02d}", "type": singular,
                **(_dims_from_size(scaled_source, opening=True) if ready else _empty_dimensions(opening=True)),
                "center": scaled_source.get("center") if ready else None,
                "confidence": "medium" if ready else "unknown",
                "measurement_status": "verified" if ready else "unavailable",
                "measurement_reason": "structural_geometry_and_scale_verified" if ready
                else "scale_unavailable" if not metric_available else "geometry_not_verified",
                "semantic_confidence": "structural_verified" if geometry_verified else "unknown",
                "geometry_status": raw_source.get("geometry_status", "unknown"),
                "geometry_confidence": raw_source.get("geometry_confidence"),
                "scale_status": scale_quality.get("status", "failed"), "measurement_ready": ready,
                "risk_eligibility": "eligible" if ready else "not_evaluable",
                "source": "verified_opening_geometry",
            })

    objects: list[dict] = []
    diagnostics: list[dict] = []
    semantic_instances = structure.get("semantic_instances", [])
    if isinstance(semantic_instances, list):
        for instance in semantic_instances:
            result, diagnostic = _formal_object_measurement(instance, float(scale) if metric_available else None)
            result["scale_status"] = scale_quality.get("status", "failed")
            diagnostic["scale_status"] = scale_quality.get("status", "failed")
            objects.append(result)
            diagnostics.append(diagnostic)

    checks: list[dict] = []
    for truth in validation:
        object_type, dimension = str(truth["object_type"]), str(truth["dimension"])
        actual = float(truth["meters"])
        if object_type == "door":
            candidate = next((item for item in openings if item["type"] == "door" and item["measurement_status"] == "verified"), None)
        else:
            wanted = TYPE_LABELS.get(object_type, object_type)
            candidate = next((item for item in objects if item["type"] == wanted and item["measurement_status"] == "verified"), None)
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
        if item.get("type") == "door" and item.get("measurement_status") == "verified"
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
) -> dict:
    structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
    result = build_measurements(structure, references, validation_keys=validation_keys)
    Path(output_json).parent.mkdir(parents=True, exist_ok=True)
    Path(output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    scale = result.get("scale", {}).get("scale_factor")
    if calibrated_structure_json is not None and result.get("metric_scale_available") and scale:
        Path(calibrated_structure_json).write_text(
            json.dumps(_scaled_structure(structure, float(scale)), ensure_ascii=False, indent=2),
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
