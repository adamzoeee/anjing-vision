"""Formal risk result schema for structured spatial assessment."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from pipeline.rules import FORMAL_RULES


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


def _matches(rule: dict, value: Any) -> bool:
    direction = rule["direction"]
    if direction == "below":
        return float(value) < float(rule["threshold"])
    if direction == "above":
        return float(value) > float(rule["threshold"])
    if direction == "equals":
        return value == rule["threshold"]
    raise ValueError(f"unsupported rule direction: {direction}")


def _threshold_summary(rules: list[dict]) -> dict:
    return {
        "direction": rules[0]["direction"],
        "levels": {
            rule["severity"]: rule["threshold"] for rule in rules
        },
        "reference": rules[0]["reference"],
        "version": rules[0]["version"],
    }


def _related_ids(position) -> tuple[list[str], str | None]:
    if not isinstance(position, dict):
        return [], None
    object_ids = []
    if position.get("object_id"):
        object_ids.append(str(position["object_id"]))
    object_ids.extend(str(value) for value in (position.get("object_ids") or []) if value)
    path_id = position.get("path_id")
    return list(dict.fromkeys(object_ids)), str(path_id) if path_id else None


def evaluate_formal_metrics(metric_payload: dict) -> list[dict]:
    """Evaluate formal metrics using only centralized, versioned rules."""
    rules_by_metric: dict[str, list[dict]] = {}
    for rule in FORMAL_RULES:
        rules_by_metric.setdefault(rule["metric_code"], []).append(rule)

    results = []
    severity_order = {"high": 2, "medium": 1}
    for metric in metric_payload.get("metrics", []):
        code = metric["metric_code"]
        rules = rules_by_metric.get(code, [])
        if not rules:
            raise ValueError(f"formal metric has no risk rule: {code}")
        object_ids, path_id = _related_ids(metric.get("position"))
        common = {
            "risk_type": metric["category"],
            "risk_name": f"{metric['name']}风险",
            "metric_code": code,
            "unit": metric["unit"],
            "position": metric.get("position"),
            "confidence": metric.get("confidence"),
            "related_object_ids": object_ids,
            "related_path_id": path_id,
        }
        if metric.get("status") == "not_evaluable":
            results.append(risk_result(
                risk_code=f"{code}_not_evaluable",
                measured_value=None,
                threshold=_threshold_summary(rules),
                risk_level=None,
                reason=metric.get("reason") or "metric_not_evaluable",
                advice=None,
                assessment_status="not_evaluable",
                **common,
            ))
            continue

        matches = [rule for rule in rules if _matches(rule, metric.get("value"))]
        selected = max(matches, key=lambda rule: severity_order[rule["severity"]]) if matches else None
        level = selected["severity"] if selected else "low"
        results.append(risk_result(
            risk_code=selected["rule_code"] if selected else f"{code}_low",
            measured_value=metric.get("value"),
            threshold=_threshold_summary(rules),
            risk_level=level,
            reason=(
                f"metric_triggered_{selected['rule_code']}"
                if selected else "within_configured_thresholds"
            ),
            advice=(
                selected["advice_template"] if selected
                else f"保持{metric['name']}现状，并在家具调整后复核。"
            ),
            assessment_status="evaluated",
            **common,
        ))
    return results
