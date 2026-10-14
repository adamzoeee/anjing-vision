"""Rich-scene geometry checks; no model, labels or random clouds are substituted."""
import numpy as np
import pytest

from pipeline.house_demo import ROOMS, PORTALS, generate_room, furniture_specs, _box
from pipeline.house_demo_assets import surface_object, window_openings


@pytest.mark.parametrize("room", ROOMS, ids=lambda r: r["id"])
def test_asset_point_ranges_bounds_and_determinism(room):
    points, colors, layers, objects = generate_room(room, .05)
    repeated = generate_room(room, .05)
    np.testing.assert_array_equal(points, repeated[0])
    np.testing.assert_array_equal(colors, repeated[1])
    assert len(points) < 250_000
    covered = np.zeros(len(points), dtype=int)
    lower, upper = np.array(room["origin"]), np.array(room["origin"]) + room["size"]
    for item in objects:
        a, b = item["point_range"]
        assert 0 <= a < b <= len(points)
        covered[a:b] += 1
        assert np.all(layers[a:b] == 2)
        assert (points[a:b] >= lower - .001).all()
        assert (points[a:b] <= upper + .001).all()
        assert item["source"] == "synthetic_ground_truth"
    np.testing.assert_array_equal(covered, layers == 2)
    assert np.isfinite(points).all() and np.isfinite(colors).all()


@pytest.mark.parametrize("room_id,required", [
    ("living_room", {"sofa", "table", "tv_stand", "tv", "window", "curtain", "air_conditioner", "rug", "plant"}),
    ("bedroom", {"bed", "nightstand", "wardrobe", "desk", "chair", "window", "curtain", "lamp", "air_conditioner"}),
    ("kitchen", {"cabinet", "sink", "fridge", "stove", "range_hood", "window"}),
    ("bathroom", {"toilet", "vanity", "mirror", "shower", "shower_head", "towel_rack"}),
    ("corridor", {"shoe_cabinet", "wall_art", "ceiling_light"}),
])
def test_requested_categories_have_real_points(room_id, required):
    room = next(r for r in ROOMS if r["id"] == room_id)
    points, _, _, objects = generate_room(room, .08)
    assert required <= {item["object"] for item in objects}
    for item in objects:
        a, b = item["point_range"]
        assert len(points[a:b]) > 10


def test_windows_remove_wall_not_overlay_on_wall():
    for room in ROOMS:
        points, _, layers, _ = generate_room(room, .05)
        for opening in window_openings(room["id"]):
            axis, along = opening["axis"], 1-opening["axis"]
            wall = np.isclose(points[:,axis], room["origin"][axis] + opening["plane"])
            hole = (np.abs(points[:,along] - room["origin"][along] - opening["center"]) < opening["width"]/2-.01)
            hole &= (points[:,2] > opening["bottom"]+.01) & (points[:,2] < opening["bottom"]+opening["height"]-.01)
            assert not (wall & hole & (layers == 0)).any()


def test_bed_mattress_height_and_pillows_separate_from_sofa():
    bed = next(item for item in furniture_specs("bedroom") if item["object"] == "bed")
    xyz, _ = surface_object(bed, .05, _box)
    assert bed["size"][:2] == [1.8, 2.0]
    assert ((np.abs(xyz[:,2]-.50)<.001) & (np.abs(xyz[:,0])<.8)).any()
    assert ((xyz[:,2]>.53) & (xyz[:,1]>.3) & (xyz[:,1]<.8)).any()
    assert ((xyz[:,2]>.8) & (xyz[:,1]>.9)).any()


def test_curtain_has_folds_and_clear_center():
    curtain = next(item for item in furniture_specs("living_room") if item["object"] == "curtain")
    xyz, _ = surface_object(curtain, .05, _box)
    cloth = xyz[xyz[:,2] < curtain["size"][2]-.1]
    assert np.ptp(cloth[:,1]) > .08
    assert (np.abs(cloth[:,0]) > curtain["size"][0]*.19).all()


def test_kitchen_sink_has_real_counter_cutout():
    counter = next(item for item in furniture_specs("kitchen") if item["object"] == "cabinet")
    xyz, _ = surface_object(counter, .05, _box)
    inside = (np.abs(xyz[:,0]+.5)<.31) & (np.abs(xyz[:,1])<.20) & (xyz[:,2]>.71)
    assert not inside.any()


def test_toilet_has_open_seat_and_tank():
    toilet = next(item for item in furniture_specs("bathroom") if item["object"] == "toilet")
    xyz, _ = surface_object(toilet, .05, _box)
    assert (xyz[:,2] > .7).any()
    center = (np.abs(xyz[:,0])<.08) & (np.abs(xyz[:,1]+.068)<.08)
    assert not (center & (xyz[:,2]>.39) & (xyz[:,2]<.41)).any()
