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
