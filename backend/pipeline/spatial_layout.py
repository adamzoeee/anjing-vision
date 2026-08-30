"""Layout metrics derived exclusively from structured room/furniture records."""
from __future__ import annotations

from pipeline.spatial_metrics import build_metric, confidence_value, unavailable_metric


def extract_furniture_spacing_metric(passage: dict) -> dict:
    """Return the minimum reported clearance between distinct furniture instances."""
    source = {"artifact": "passage_analysis.json", "field": "furniture_clearances[*]"}
    candidates = [
        item for item in passage.get("furniture_clearances", [])
        if item.get("clearance_m") is not None and len(item.get("between") or []) == 2
    ]
    if not candidates:
        return unavailable_metric("furniture_spacing", "furniture_clearance_unavailable", source=source)
    narrowest = min(candidates, key=lambda item: float(item["clearance_m"]))
    return build_metric(
        "furniture_spacing",
        value=round(float(narrowest["clearance_m"]), 3),
        status="derived",
        confidence=confidence_value(narrowest.get("confidence")),
        position={
            "object_ids": list(narrowest["between"]),
            "labels": list(narrowest.get("between_labels") or []),
        },
        source=source,
    )
