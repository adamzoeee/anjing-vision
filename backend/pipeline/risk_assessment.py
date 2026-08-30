"""Formal risk result schema for structured spatial assessment."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


RISK_LEVELS = frozenset({"low", "medium", "high"})
ASSESSMENT_STATUSES = frozenset({"evaluated", "not_evaluable"})


@dataclass(frozen=True)
class RiskResult:
    risk_code: str
    risk_type: str
    risk_name: str
    metric_code: str
    measured_value: Any
    unit: str
    threshold: dict | None
    position: dict | list | None
    risk_level: str | None
    confidence: float | None
    reason: str | None
    advice: str | None
    assessment_status: str
    related_object_ids: list[str]
    related_path_id: str | None

    def __post_init__(self) -> None:
        if not self.risk_code or not self.metric_code:
            raise ValueError("risk_code and metric_code are required")
        if self.assessment_status not in ASSESSMENT_STATUSES:
            raise ValueError(f"unsupported assessment status: {self.assessment_status}")
        if self.risk_level is not None and self.risk_level not in RISK_LEVELS:
            raise ValueError(f"unsupported risk level: {self.risk_level}")
        if self.confidence is not None and not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be between 0 and 1")
        if self.assessment_status == "not_evaluable":
            if self.risk_level is not None:
                raise ValueError("not_evaluable risks must not have a risk level")
            if not self.reason:
                raise ValueError("not_evaluable risks require a reason")
        elif self.risk_level is None:
            raise ValueError("evaluated risks require a risk level")

    def to_dict(self) -> dict:
        return asdict(self)


def risk_result(**values) -> dict:
    """Validate and serialize a formal risk result."""
    return RiskResult(**values).to_dict()
