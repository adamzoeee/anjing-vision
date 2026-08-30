"""Formal spatial-metric records derived from existing structured artifacts.

This module deliberately has no image, point-cloud, or model dependencies.  It
defines the stable contract shared by metric extraction, risk evaluation,
reports, and clients.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


METRIC_STATUSES = frozenset({"measured", "derived", "not_evaluable"})


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
