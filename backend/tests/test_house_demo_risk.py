import pytest
from pipeline.house_demo import ROOMS, PORTALS, generate_room
from pipeline.house_demo_layout import analyze_house
from test_house_demo_lived import (
    test_lived_counts_points_clear_portals_and_no_unintended_intersection as check_geometry,
    test_clutter_supported_by_actual_table_not_floating as check_support,
    test_all_rooms_have_a_geometric_route_and_traceable_advice as check_routes,
)


@pytest.fixture
def risk(monkeypatch):
    monkeypatch.setenv("HOUSE_DEMO_PROFILE", "risk")


@pytest.mark.parametrize("room", ROOMS, ids=lambda r: r["id"])
def test_risk_geometry(risk, room):
    check_geometry(None, room)


def test_risk_support_and_open_routes(risk):
    check_support(None)
    check_routes(None)


def test_risk_changes_evidence_without_changing_rules(monkeypatch):
    results = {}
    for profile in ("lived", "risk"):
        monkeypatch.setenv("HOUSE_DEMO_PROFILE", profile)
        objects = [o for room in ROOMS for o in generate_room(room, .08)[3]]
        results[profile] = analyze_house({"rooms": ROOMS, "portals": PORTALS}, objects,
                                        "synthetic_ground_truth")["rooms"]
    for room in ("living_room", "bathroom", "corridor"):
        old = min(r["minimum_width_m"] for r in results["lived"][room]["routes"])
        new = min(r["minimum_width_m"] for r in results["risk"][room]["routes"])
        assert new < old
    assert results["risk"]["living_room"]["safety_status"] == "not_assessed"
