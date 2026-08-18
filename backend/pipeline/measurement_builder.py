"""用用户实测参考值标定已有结构坐标，并生成 measurements.json。"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable


TYPE_LABELS = {
    "bed": "bed", "door": "door", "table": "desk", "desk": "desk",
    "cabinet": "cabinet", "bookshelf": "bookshelf", "sofa": "sofa",
}


def _dims_from_size(item: dict, *, opening: bool = False) -> dict:
    size = [float(value) for value in item.get("size", [0, 0, 0])]
    if len(size) != 3:
        return {"length_m": None, "width_m": None, "height_m": None}
    if opening:
        return {"width_m": max(size[0], size[1]), "height_m": size[2]}
    horizontal = sorted(size[:2], reverse=True)
    label = str(item.get("label", ""))
    if label == "desk":
        # 书桌“宽”指贴墙/前沿方向的长边（用户卷尺量法）
        return {"length_m": horizontal[1], "width_m": horizontal[0], "height_m": size[2]}
    return {"length_m": horizontal[0], "width_m": horizontal[1], "height_m": size[2]}


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


def estimate_reference_scale(structure: dict, references: Iterable[dict], *, tolerance: float = 0.15) -> tuple[float | None, dict]:
    """真实米数 / 当前结构单位，使用至少两个一致参考求统一比例。"""
    candidates = []
    details = []
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
        return None, {"status": "failed", "reason": "至少需要两个成功测得的参考尺寸", "references": details}
    median = float(sorted(candidates)[len(candidates) // 2]) if len(candidates) % 2 else float(sum(sorted(candidates)[len(candidates)//2-1:len(candidates)//2+1]) / 2)
    disagreement = max(abs(value - median) / median for value in candidates)
    if disagreement > tolerance:
        return None, {"status": "failed", "reason": "参考尺寸换算比例不一致", "max_relative_disagreement": disagreement, "references": details}
    for detail in details:
        if detail.get("status") == "used":
            predicted = float(detail["model_units"]) * median
            actual = float(detail["meters"])
            detail.update({
                "predicted_m": predicted,
                "absolute_error_m": abs(predicted - actual),
                "relative_error": abs(predicted - actual) / actual,
            })
    return median, {"status": "metric_references", "scale": median, "max_relative_disagreement": disagreement, "references": details}


def _scaled_structure(structure: dict, scale: float) -> dict:
    result = json.loads(json.dumps(structure))
    for item in result.get("room", {}).get("floor_polygon", []):
        for index in range(min(3, len(item))): item[index] *= scale
    bounds = result.get("room", {}).get("bounds_xy", {})
    for key in ("min", "max"):
        if key in bounds: bounds[key] = [float(value) * scale for value in bounds[key]]
    if result.get("room", {}).get("height_m") is not None: result["room"]["height_m"] *= scale
    for collection in ("walls", "doors", "windows", "objects", "rejected_objects", "geometric_obstacles"):
        for item in result.get(collection, []):
            for key in ("center", "size"):
                if key in item: item[key] = [float(value) * scale for value in item[key]]
            if "height_range_m" in item: item["height_range_m"] = [float(value) * scale for value in item["height_range_m"]]
    result["measurement_scale"] = {"applied": scale, "method": "metric_references"}
    return result


def build_measurements(
    structure: dict,
    references: Iterable[dict],
    *,
    validation_keys: set[tuple[str, str]] | None = None,
) -> dict:
    """由至少两个实测参考值求统一米/当前单位比例，再换算全部结构坐标。"""
    references = [dict(item) for item in references]
    validation_keys = validation_keys or set()
    calibration = []
    validation = []
    for item in references:
        target = validation if _reference_key(item) in validation_keys else calibration
        target.append(item)

    scale, scale_quality = estimate_reference_scale(structure, calibration)
    metric_structure = _scaled_structure(structure, scale) if scale is not None else structure
    room = metric_structure.get("room", {})
    bounds = room.get("bounds_xy", {})
    lo, hi = bounds.get("min", [0, 0]), bounds.get("max", [0, 0])
    horizontal = sorted([float(hi[0]) - float(lo[0]), float(hi[1]) - float(lo[1])], reverse=True)
    room_result = {
        "length_m": horizontal[0], "width_m": horizontal[1],
        "height_m": float(room.get("height_m", 0)) or None,
        "confidence": "medium", "source": "aligned_pointcloud_bounds",
    }

    openings = []
    for kind in ("doors", "windows"):
        for index, source in enumerate(metric_structure.get(kind, []), 1):
            singular = "door" if kind == "doors" else "window"
            dims = _dims_from_size(source, opening=True)
            openings.append({
                "id": f"{singular}_{index:02d}", "type": singular,
                **dims, "center": source.get("center"),
                "confidence": "medium" if scale is not None else "unknown",
                "source": "metric_reference_scale" if scale is not None else "unscaled_geometry",
            })

    objects = []
    for source in metric_structure.get("objects", []):
        dims = _dims_from_size(source)
        objects.append({
            "id": source.get("instance_id"), "type": source.get("label"),
            **dims, "center": source.get("center"), "rotation_z_deg": source.get("rotation_z_deg", 0),
            "confidence": "medium" if scale is not None and source.get("geometry_status") == "verified" else "low",
            "source": "metric_reference_scale" if scale is not None else "unscaled_geometry",
        })

    checks = []
    for truth in validation:
        candidate = _matching_object(metric_structure, str(truth["object_type"]))
        dimension = str(truth["dimension"])
        predicted = None
        if candidate:
            predicted = _dims_from_size(candidate, opening=truth["object_type"] == "door").get(f"{dimension}_m")
        actual = float(truth["meters"])
        checks.append({
            **truth, "predicted_m": predicted,
            "absolute_error_m": abs(predicted - actual) if predicted is not None else None,
            "relative_error": abs(predicted - actual) / actual if predicted is not None else None,
            "status": "compared" if predicted is not None else "unknown",
        })

    return {
        "schema_version": 1, "coordinate_unit": "meters",
        "scale": {**scale_quality, "global_rescale_applied": scale is not None,
                  "calibration_references": calibration, "validation_references": validation},
        "room": room_result, "openings": openings, "objects": objects,
        "passage": {"status": "deferred_until_object_geometry_verified"},
        "quality": {"validation": checks},
    }


def build_measurements_file(structure_json: Path, output_json: Path, references: Iterable[dict], *, validation_keys=None, calibrated_structure_json: Path | None = None) -> dict:
    structure = json.loads(Path(structure_json).read_text(encoding="utf-8"))
    result = build_measurements(structure, references, validation_keys=validation_keys)
    Path(output_json).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    scale = result.get("scale", {}).get("scale")
    if calibrated_structure_json is not None and scale:
        Path(calibrated_structure_json).write_text(json.dumps(_scaled_structure(structure, float(scale)), ensure_ascii=False, indent=2), encoding="utf-8")
    return result
