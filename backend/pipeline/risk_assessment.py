"""Formal risk result schema for structured spatial assessment."""
from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

from pipeline.rules import FORMAL_CATEGORY_WEIGHTS, FORMAL_RULES


RISK_LEVELS = frozenset({"low", "medium", "high"})
ASSESSMENT_STATUSES = frozenset({"evaluated", "not_evaluable"})
RISK_LEVEL_SCORES = {"low": 100.0, "medium": 60.0, "high": 20.0}
CORE_REQUIRED_METRICS = frozenset({
    "main_passage_width", "door_width", "path_continuity",
    "furniture_spacing", "bed_surrounding_space",
})
MINIMUM_OFFICIAL_COVERAGE = 0.60


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


def score_formal_risks(risks: list[dict]) -> dict:
    """Compute the single official 40/30/30 score or report insufficient data."""
    evaluated = [item for item in risks if item.get("assessment_status") == "evaluated"]
    coverage_ratio = len(evaluated) / len(risks) if risks else 0.0
    evaluated_codes = {item["metric_code"] for item in evaluated}
    missing_core = sorted(CORE_REQUIRED_METRICS - evaluated_codes)
    category_scores = {}
    missing_categories = []
    for category, weight in FORMAL_CATEGORY_WEIGHTS.items():
        category_items = [item for item in risks if item.get("risk_type") == category]
        known = [item for item in category_items if item.get("assessment_status") == "evaluated"]
        if known:
            score = round(sum(RISK_LEVEL_SCORES[item["risk_level"]] for item in known) / len(known), 1)
            status = "evaluated"
        else:
            score = None
            status = "not_evaluable"
            missing_categories.append(category)
        category_scores[category] = {
            "score": score,
            "weight": weight,
            "status": status,
            "evaluated_count": len(known),
            "not_evaluable_count": len(category_items) - len(known),
            "total_count": len(category_items),
        }
    insufficient = (
        not risks
        or coverage_ratio < MINIMUM_OFFICIAL_COVERAGE
        or bool(missing_core)
        or bool(missing_categories)
    )
    overall_score = None if insufficient else round(sum(
        category_scores[category]["score"] * weight
        for category, weight in FORMAL_CATEGORY_WEIGHTS.items()
    ), 1)
    return {
        "status": "insufficient_data" if insufficient else "evaluated",
        "score": overall_score,
        "weights": FORMAL_CATEGORY_WEIGHTS,
        "category_scores": category_scores,
        "missing_core_metrics": missing_core,
        "missing_categories": missing_categories,
        "minimum_coverage_percent": MINIMUM_OFFICIAL_COVERAGE * 100,
    }


def summarize_assessment_confidence(metric_payload: dict, risks: list[dict]) -> dict:
    """Report evidence coverage and confidence independently from risk level."""
    metrics = metric_payload.get("metrics", [])
    evaluable_metrics = [item for item in metrics if item.get("status") != "not_evaluable"]
    evaluated_risks = [item for item in risks if item.get("assessment_status") == "evaluated"]
    total = len(risks)
    evaluated_count = len(evaluated_risks)
    coverage_percent = round(evaluated_count / total * 100, 1) if total else 0.0
    confidence_values = [
        float(item["confidence"]) for item in evaluated_risks
        if item.get("confidence") is not None
    ]
    evidence_confidence = (
        round(sum(confidence_values) / len(confidence_values), 3)
        if confidence_values else None
    )
    adjusted_confidence = (
        round(evidence_confidence * evaluated_count / total, 3)
        if evidence_confidence is not None and total else None
    )
    by_category = {}
    for category in FORMAL_CATEGORY_WEIGHTS:
        category_risks = [item for item in risks if item.get("risk_type") == category]
        known = [item for item in category_risks if item.get("assessment_status") == "evaluated"]
        values = [float(item["confidence"]) for item in known if item.get("confidence") is not None]
        by_category[category] = {
            "coverage_percent": round(len(known) / len(category_risks) * 100, 1) if category_risks else 0.0,
            "evidence_confidence": round(sum(values) / len(values), 3) if values else None,
        }
    return {
        "assessment_coverage": {
            "evaluated_count": evaluated_count,
            "not_evaluable_count": total - evaluated_count,
            "total_count": total,
            "percent": coverage_percent,
        },
        "metric_coverage": metric_payload.get("coverage") or {
            "evaluable_count": len(evaluable_metrics),
            "not_evaluable_count": len(metrics) - len(evaluable_metrics),
            "total_count": len(metrics),
            "percent": round(len(evaluable_metrics) / len(metrics) * 100, 1) if metrics else 0.0,
        },
        "evidence_confidence": evidence_confidence,
        "coverage_adjusted_confidence": adjusted_confidence,
        "confidence_sample_count": len(confidence_values),
        "by_category": by_category,
        "reason": None if confidence_values else "numeric_confidence_unavailable",
    }


def rank_top_risks(risks: list[dict], *, limit: int = 6) -> list[dict]:
    """Rank evaluated high/medium risks deterministically for reports."""
    if limit < 0:
        raise ValueError("limit must not be negative")
    priority = {"high": 2, "medium": 1}
    candidates = [
        item for item in risks
        if item.get("assessment_status") == "evaluated"
        and item.get("risk_level") in priority
    ]
    candidates.sort(key=lambda item: (
        -priority[item["risk_level"]],
        -(item.get("confidence") if item.get("confidence") is not None else -1.0),
        item.get("risk_code") or "",
    ))
    return candidates[:limit]


def collect_specific_advice(risks: list[dict]) -> list[str]:
    """Return stable, deduplicated advice for evaluated actionable risks."""
    advice = []
    for item in rank_top_risks(risks, limit=len(risks)):
        text = item.get("advice")
        if text and text not in advice:
            advice.append(text)
    return advice


KEY_METRIC_CODES = (
    "main_passage_width", "minimum_passage_width", "door_width",
    "entrance_space", "bedside_clearance", "crowding",
    "bed_surrounding_space",
)


def build_risk_assessment(metric_payload: dict) -> dict:
    """Build the single backend-owned formal assessment payload."""
    risks = evaluate_formal_metrics(metric_payload)
    scoring = score_formal_risks(risks)
    confidence = summarize_assessment_confidence(metric_payload, risks)
    metrics_by_code = {
        item["metric_code"]: item for item in metric_payload.get("metrics", [])
    }
    not_evaluable = [
        item for item in risks if item["assessment_status"] == "not_evaluable"
    ]
    return {
        "schema_version": "1.0",
        "official": True,
        "overall": {
            "status": scoring["status"],
            "score": scoring["score"],
            "confidence": confidence["coverage_adjusted_confidence"],
            "coverage_percent": confidence["assessment_coverage"]["percent"],
            "missing_core_metrics": scoring["missing_core_metrics"],
        },
        "category_scores": scoring["category_scores"],
        "weights": scoring["weights"],
        "key_metrics": [
            metrics_by_code[code] for code in KEY_METRIC_CODES if code in metrics_by_code
        ],
        "metrics": metric_payload.get("metrics", []),
        "paths": metric_payload.get("paths", []),
        "risks": risks,
        "top_risks": rank_top_risks(risks),
        "not_evaluable": not_evaluable,
        "advice": collect_specific_advice(risks),
        "confidence": confidence,
        "provenance": metric_payload.get("provenance"),
        "scope": {
            "backend_source_of_truth": True,
            "structured_inputs_only": True,
            "official_score_system": "mobility_40_layout_30_usage_safety_30",
        },
    }


def build_risk_assessment_file(metric_json: Path, output_json: Path) -> dict:
    """Serialize the backend-owned assessment from a formal metric JSON file."""
    metric_json = Path(metric_json)
    if metric_json.suffix.lower() != ".json":
        raise ValueError("formal risk assessment input must be a JSON artifact")
    source_bytes = metric_json.read_bytes()
    metric_payload = json.loads(source_bytes.decode("utf-8"))
    assessment = build_risk_assessment(metric_payload)
    assessment["metric_input"] = {
        "artifact": metric_json.name,
        "sha256": hashlib.sha256(source_bytes).hexdigest(),
        "input_modified": False,
    }
    output_json = Path(output_json)
    output_json.parent.mkdir(parents=True, exist_ok=True)
    output_json.write_text(json.dumps(assessment, ensure_ascii=False, indent=2), encoding="utf-8")
    return assessment
