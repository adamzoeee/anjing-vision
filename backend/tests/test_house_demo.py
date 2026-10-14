"""Geometry/connectivity tests do not load model weights or require a GPU."""
import json
from pathlib import Path

import numpy as np
import pytest

from pipeline.house_demo import PORTALS, ROOMS, build_demo, generate_room, infer_rooms


@pytest.mark.parametrize("spacing", [0, -1, 0.001, float("nan"), 0.2])
def test_density_is_bounded(spacing):
    with pytest.raises(ValueError):
        generate_room(ROOMS[0], spacing)


@pytest.mark.parametrize("room", ROOMS, ids=lambda r: r["id"])
def test_geometry_is_finite_and_open_at_both_sides_of_portals(room):
    points, colors, layers, truth = generate_room(room, 0.08)
    assert points.shape == colors.shape
    assert len(layers) == len(points)
    assert np.isfinite(points).all()
    assert (colors >= 0).all() and (colors <= 1).all()
    assert (layers == 1).any()
    assert (points[:, 2] >= 0).all() and (points[:, 2] <= 3).all()
    for portal in PORTALS:
        if room["id"] not in portal["rooms"]:
            continue
        # Includes jamb depth, both wall surfaces and any furniture near the door.
        obstruction = ((np.abs(points[:, 0] - portal["plane"]) < 0.041)
                       & (np.abs(points[:, 1] - portal["center"]) < 0.449)
                       & (points[:, 2] > 0.01) & (points[:, 2] < 2.099))
        assert not obstruction.any()
    assert all(item["source"] == "synthetic_ground_truth" for item in truth)


def test_rooms_non_overlapping_and_graph_connected():
    for i, room in enumerate(ROOMS):
        for other in ROOMS[i + 1:]:
            a, b = np.array(room["origin"][:2]), np.array(other["origin"][:2])
            overlap = np.minimum(a + room["size"][:2], b + other["size"][:2]) - np.maximum(a, b)
            assert not (overlap > 1e-8).all()
    seen = {"corridor"}
    for portal in PORTALS:
        assert portal["width"] == 0.9 and portal["height"] == 2.1
        connected = [next(r for r in ROOMS if r["id"] == rid) for rid in portal["rooms"]]
        for room in connected:
            assert portal["plane"] in (room["origin"][0], room["origin"][0] + room["size"][0])
            assert room["origin"][1] <= portal["center"] - 0.45
            assert portal["center"] + 0.45 <= room["origin"][1] + room["size"][1]
        assert seen.intersection(portal["rooms"])
        seen.update(portal["rooms"])
    assert seen == {r["id"] for r in ROOMS}


def test_generation_round_trip_and_no_overwrite(tmp_path):
    import open3d as o3d

    manifest = build_demo(tmp_path, spacing=0.10)
    for room in manifest["rooms"]:
        for extension in ("pcd", "ply"):
            cloud = o3d.io.read_point_cloud(str(tmp_path / room[extension]))
            assert len(cloud.points) == room["points"]
            assert cloud.has_colors()
        assert (tmp_path / room["buffer"]).stat().st_size == room["points"] * 24
        assert (tmp_path / room["layers"]).stat().st_size == room["points"]
    complete = o3d.io.read_point_cloud(str(tmp_path / "house_complete.pcd"))
    assert len(complete.points) == manifest["complete_points"]
    assert manifest["complete_points"] < sum(r["points"] for r in manifest["rooms"])
    with pytest.raises(FileExistsError):
        build_demo(tmp_path)


def _setup(tmp_path):
    (tmp_path / "manifest.json").write_text(json.dumps({"synthetic": True}))
    (tmp_path / "predictions.json").write_text(json.dumps({"source": "spatiallm", "rooms": {
        r["id"]: {"status": "not_run", "objects": []} for r in ROOMS}}))


def test_model_adapter_uses_actual_predictions_not_truth(tmp_path):
    _setup(tmp_path)
    calls = []

    def runner(ply, txt, **kwargs):
        calls.append((ply, txt, kwargs))
        return {"seconds": 1.5, "boxes": {"objects": [{"category": "chair", "center": [1, 2, 0.4],
                "size": [0.5, 0.5, 0.8], "rotation_z_deg": 17}], "walls": [], "doors": [], "windows": []}}

    result = infer_rooms(tmp_path, ["living_room"], runner)
    assert calls[0][0] == tmp_path / "living_room.ply"
    assert calls[0][2] == {"detect_type": "all"}
    actual = result["rooms"]["living_room"]["objects"]
    assert len(actual) == 1 and actual[0]["rotation"] == 17
    assert actual[0]["source"] == "spatiallm"
    assert result["rooms"]["bedroom"]["status"] == "not_run"


def test_model_failure_does_not_fabricate_objects(tmp_path):
    _setup(tmp_path)

    def failed(*args, **kwargs):
        raise RuntimeError("model unavailable")

    result = infer_rooms(tmp_path, ["bedroom"], failed)
    assert result["rooms"]["bedroom"]["status"] == "failed"
    assert result["rooms"]["bedroom"]["objects"] == []
    assert "model unavailable" in result["rooms"]["bedroom"]["error"]


def test_unknown_room_rejected(tmp_path):
    _setup(tmp_path)
    with pytest.raises(ValueError):
        infer_rooms(tmp_path, ["../scan46"], lambda *args: None)


def test_empty_prediction_remains_empty(tmp_path):
    _setup(tmp_path)
    result = infer_rooms(tmp_path, ["kitchen"], lambda *a, **kw: {"boxes": {"objects": []}, "seconds": 1})
    assert result["rooms"]["kitchen"]["status"] == "done"
    assert result["rooms"]["kitchen"]["objects"] == []


def test_default_inference_calls_each_furnished_room_once(tmp_path):
    _setup(tmp_path)
    calls = []

    def runner(ply, *args, **kwargs):
        calls.append(ply.stem)
        return {"boxes": {"objects": []}, "seconds": 1}

    result = infer_rooms(tmp_path, runner=runner)
    assert calls == ["bathroom", "bedroom", "kitchen", "living_room"]
    assert result["rooms"]["corridor"]["status"] == "not_run"
    assert not (tmp_path / "predictions.json.tmp").exists()


def test_invalid_model_geometry_is_failed_not_published(tmp_path):
    _setup(tmp_path)
    result = infer_rooms(tmp_path, ["bedroom"], lambda *a, **kw: {
        "seconds": 1, "boxes": {"objects": [{"category": "bed", "center": [float("nan"), 0, 0],
                                             "size": [1, 2, 1]}]}})
    assert result["rooms"]["bedroom"]["status"] == "failed"
    assert result["rooms"]["bedroom"]["objects"] == []


def test_native_radians_converted_from_layout_without_changing_original(tmp_path):
    _setup(tmp_path)
    text = tmp_path / "layout.txt"
    text.write_text("bbox_0=Bbox(chair,1,2,0.4,1.5707963267948966,0.5,0.5,0.8)")
    item = {"category": "chair", "center": [1, 2, 0.4], "size": [0.5, 0.5, 0.8],
            "rotation_z_deg": np.pi / 2}
    result = infer_rooms(tmp_path, ["living_room"], lambda *a, **kw: {
        "layout_txt": text, "seconds": 1, "boxes": {"objects": [item]}})
    room = result["rooms"]["living_room"]
    assert room["objects"][0]["rotation"] == pytest.approx(90)
    assert room["structure"]["objects"][0]["rotation_z_deg"] == pytest.approx(90)
    assert item["rotation_z_deg"] == np.pi / 2


def test_untraceable_model_rotation_not_guessed(tmp_path):
    _setup(tmp_path)
    text = tmp_path / "layout.txt"
    text.write_text("")
    result = infer_rooms(tmp_path, ["living_room"], lambda *a, **kw: {
        "layout_txt": text, "seconds": 1, "boxes": {"objects": [
            {"category": "chair", "center": [1, 2, 3], "size": [1, 1, 1], "rotation_z_deg": 1}]}})
    assert result["rooms"]["living_room"]["status"] == "failed"
    assert result["rooms"]["living_room"]["objects"] == []
