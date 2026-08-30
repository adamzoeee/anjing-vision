"""Formal spatial-metric records derived from existing structured artifacts.

This module deliberately has no image, point-cloud, or model dependencies.  It
defines the stable contract shared by metric extraction, risk evaluation,
reports, and clients.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


METRIC_STATUSES = frozenset({"measured", "derived", "not_evaluable"})

METRIC_DEFINITIONS = (
    ("mobility", "main_passage_width", "主要通道净宽", "m"),
    ("mobility", "minimum_passage_width", "最小通道净宽", "m"),
    ("mobility", "door_width", "门净宽", "m"),
    ("mobility", "entrance_space", "入口可用空间", "m²"),
    ("mobility", "path_length", "通行路径长度", "m"),
    ("mobility", "path_continuity", "路径连续性", "boolean"),
    ("mobility", "path_obstruction", "路径障碍", "boolean"),
    ("layout", "furniture_spacing", "家具间距", "m"),
    ("layout", "wall_furniture_clearance", "家具离墙间距", "m"),
    ("layout", "bed_wall_distance", "床离墙距离", "m"),
    ("layout", "bedside_clearance", "床侧净空", "m"),
    ("layout", "activity_area", "活动区域面积", "m²"),
    ("layout", "crowding", "空间拥挤度", "ratio"),
    ("usage_safety", "bed_surrounding_space", "床周边可用空间", "m²"),
    ("usage_safety", "main_activity_area_safety", "主要活动区安全状态", "boolean"),
)

METRIC_DEFINITION_BY_CODE = {
    code: {"category": category, "name": name, "unit": unit}
    for category, code, name, unit in METRIC_DEFINITIONS
}

_CONFIDENCE_LEVELS = {"low": 0.4, "medium": 0.7, "high": 0.9}


def confidence_value(value: Any, *, default: float | None = None) -> float | None:
    """Normalize structured confidence values without inventing missing evidence."""
    if value is None:
        return default
    if isinstance(value, bool):
        return 1.0 if value else 0.0
    if isinstance(value, (int, float)):
        return round(max(0.0, min(1.0, float(value))), 4)
    return _CONFIDENCE_LEVELS.get(str(value).strip().lower(), default)


@dataclass(frozen=True)
class SpatialMetric:
    """One traceable spatial observation used by the formal risk evaluator."""

    metric_code: str
    name: str
    value: Any
    unit: str
    status: str
    confidence: float | None
    position: dict | list | None
    source: dict | str
    reason: str | None = None

    def __post_init__(self) -> None:
        if not self.metric_code.strip():
            raise ValueError("metric_code must not be empty")
        if self.status not in METRIC_STATUSES:
            raise ValueError(f"unsupported metric status: {self.status}")
        if self.confidence is not None and not 0.0 <= self.confidence <= 1.0:
            raise ValueError("confidence must be between 0 and 1")
        if self.status == "not_evaluable":
            if self.value is not None:
                raise ValueError("not_evaluable metrics must not carry a value")
            if not self.reason:
                raise ValueError("not_evaluable metrics require a reason")
        elif self.value is None:
            raise ValueError("evaluable metrics require a value")

    def to_dict(self) -> dict:
        """Return a JSON-compatible record without dropping explicit nulls."""
        return asdict(self)


def metric_record(
    metric_code: str,
    name: str,
    *,
    value: Any = None,
    unit: str = "",
    status: str = "not_evaluable",
    confidence: float | None = None,
    position: dict | list | None = None,
    source: dict | str = "structured_artifacts",
    reason: str | None = None,
) -> dict:
    """Build and validate one serialized :class:`SpatialMetric`."""
    return SpatialMetric(
        metric_code=metric_code,
        name=name,
        value=value,
        unit=unit,
        status=status,
        confidence=confidence,
        position=position,
        source=source,
        reason=reason,
    ).to_dict()


def build_metric(
    metric_code: str,
    *,
    value: Any = None,
    status: str = "not_evaluable",
    confidence: float | None = None,
    position: dict | list | None = None,
    source: dict | str = "structured_artifacts",
    reason: str | None = None,
) -> dict:
    """Build a catalog-backed metric and include its assessment category."""
    try:
        definition = METRIC_DEFINITION_BY_CODE[metric_code]
    except KeyError as exc:
        raise ValueError(f"unknown formal metric code: {metric_code}") from exc
    record = metric_record(
        metric_code,
        definition["name"],
        value=value,
        unit=definition["unit"],
        status=status,
        confidence=confidence,
        position=position,
        source=source,
        reason=reason,
    )
    return {"category": definition["category"], **record}


def unavailable_metric(metric_code: str, reason: str, *, source: dict | str) -> dict:
    """Create an explicit missing-data record; absence never implies safety."""
    return build_metric(metric_code, reason=reason, source=source)


def build_metric_payload(metrics: list[dict]) -> dict:
    """Validate and group one complete formal metric set for JSON output."""
    codes = [item.get("metric_code") for item in metrics]
    duplicates = sorted({code for code in codes if codes.count(code) > 1})
    if duplicates:
        raise ValueError(f"duplicate metric codes: {', '.join(duplicates)}")
    expected = set(METRIC_DEFINITION_BY_CODE)
    missing = sorted(expected - set(codes))
    unknown = sorted(set(codes) - expected)
    if missing:
        raise ValueError(f"missing formal metric codes: {', '.join(missing)}")
    if unknown:
        raise ValueError(f"unknown formal metric codes: {', '.join(unknown)}")
    by_category = {
        category: [item for item in metrics if item["category"] == category]
        for category in ("mobility", "layout", "usage_safety")
    }
    evaluable = sum(item["status"] != "not_evaluable" for item in metrics)
    return {
        "schema_version": "1.0",
        "metrics": metrics,
        "by_category": by_category,
        "coverage": {
            "evaluable_count": evaluable,
            "not_evaluable_count": len(metrics) - evaluable,
            "total_count": len(metrics),
            "percent": round(evaluable / len(metrics) * 100, 1) if metrics else 0.0,
        },
    }


def _path_source(field: str) -> dict:
    return {"artifact": "passage_analysis.json", "field": field}


def extract_passage_width_metrics(passage: dict, foundation: dict | None = None) -> list[dict]:
    """Extract formal passage widths from precomputed structured routes."""
    route = passage.get("primary_route") or {}
    width = route.get("minimum_clear_width_m")
    route_id = route.get("id")
    position = {
        "path_id": route_id,
        "point_xy": route.get("narrowest_point_xy"),
    }
    reason = passage.get("reason") or route.get("reason") or "passage_width_unavailable"
    if width is None or passage.get("status") not in {"ok", "complete"}:
        return [
            unavailable_metric(
                "main_passage_width", reason,
                source=_path_source("primary_route.minimum_clear_width_m"),
            ),
            unavailable_metric(
                "minimum_passage_width", reason,
                source=_path_source("primary_route.minimum_clear_width_m"),
            ),
        ]

    route_widths = [width]
    for item in (foundation or {}).get("passages", []):
        candidate = item.get("minimum_clear_width_m")
        if candidate is not None and item.get("path_exists") is not False:
            route_widths.append(candidate)
    minimum_width = min(float(value) for value in route_widths)
    confidence = confidence_value(route.get("confidence"))
    common = {
        "status": "derived",
        "confidence": confidence,
        "position": position,
    }
    return [
        build_metric(
            "main_passage_width", value=round(float(width), 3),
            source=_path_source("primary_route.minimum_clear_width_m"), **common,
        ),
        build_metric(
            "minimum_passage_width", value=round(minimum_width, 3),
            source={
                "artifact": "spatial_foundation.json",
                "field": "passages[*].minimum_clear_width_m",
            },
            **common,
        ),
    ]
