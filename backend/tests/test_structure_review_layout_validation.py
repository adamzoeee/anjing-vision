from __future__ import annotations

import pytest

from pipeline.structure_review import _validate_layout


def _structure() -> dict:
    return {
        "room": {"bounds_xy": {"min": [0.0, 0.0], "max": [3.0, 3.0]}},
        "doors": [{
            "center": [2.4, 0.0, 1.0], "size": [0.8, 0.1, 2.0],
            "rotation_z_deg": 0.0, "wall_id": 0,
        }],
    }


def _item(instance_id: str, center: list[float], size: list[float]) -> dict:
    return {
        "instance_id": instance_id, "center": center, "size": size,
        "rotation_z_deg": 0.0,
    }


def test_reviewed_layout_accepts_clear_non_overlapping_plan() -> None:
    items = {
        "bed": _item("bed", [0.8, 2.0, 0.3], [1.4, 1.8, 0.6]),
        "desk": _item("desk", [0.5, 0.3, 0.4], [0.8, 0.4, 0.8]),
    }
    assert _validate_layout(_structure(), items, {"layout_constraints": {}})["status"] == "passed"


@pytest.mark.parametrize(
    "items",
    [
        {
            "bed": _item("bed", [1.0, 1.0, 0.3], [1.4, 1.4, 0.6]),
            "desk": _item("desk", [1.2, 1.0, 0.4], [1.0, 0.5, 0.8]),
        },
        {"cabinet": _item("cabinet", [2.4, 0.3, 0.7], [0.7, 0.5, 1.4])},
    ],
)
def test_reviewed_layout_rejects_overlap_or_blocked_door(items: dict) -> None:
    with pytest.raises(ValueError):
        _validate_layout(_structure(), items, {"layout_constraints": {}})
