import json

import numpy as np
import open3d as o3d

from pipeline.structure_builder import build_structure


def test_build_structure_filters_hallucination_and_fits_furniture(tmp_path):
    rng = np.random.default_rng(7)
    bed = rng.uniform([-0.8, -0.5, 0.0], [0.8, 0.5, 0.55], size=(2500, 3))
    floor = np.column_stack((rng.uniform(-2, 2, 2000), rng.uniform(-1.5, 1.5, 2000), rng.normal(0, 0.005, 2000)))
    # 四面墙；在 y=-1.5 墙上留门洞，SpatialLM 故意把候选中心放到对面墙。
    wall_x = rng.uniform(-2, 2, 5000)
    wall_z = rng.uniform(0, 2.6, 5000)
    keep = ~((wall_x > -1.8) & (wall_x < -1.0) & (wall_z < 2.05))
    wall0 = np.column_stack((wall_x[keep], np.full(keep.sum(), -1.5), wall_z[keep]))
    wall2 = np.column_stack((rng.uniform(-2, 2, 5000), np.full(5000, 1.5), rng.uniform(0, 2.6, 5000)))
    wall1 = np.column_stack((np.full(3500, 2.0), rng.uniform(-1.5, 1.5, 3500), rng.uniform(0, 2.6, 3500)))
    wall3 = np.column_stack((np.full(3500, -2.0), rng.uniform(-1.5, 1.5, 3500), rng.uniform(0, 2.6, 3500)))
    points = np.vstack((bed, floor, wall0, wall1, wall2, wall3))
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    ply = tmp_path / "scene_aligned.ply"
    o3d.io.write_point_cloud(str(ply), cloud)
    alignment = tmp_path / "alignment.json"
    alignment.write_text(json.dumps({"extents_m": {"x": [-2, 2], "y": [-1.5, 1.5], "z": [0, 2.6]}}))
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps({
        "doors": [{"center": [-1.4, 1.4, 1.0], "size": [0.8, 0.1, 2.0]}],
        "walls": [], "windows": [],
        "objects": [
            {"category": "bed", "center": [0, 0, 0.3], "size": [2.0, 1.4, 0.8], "rotation_z_deg": 0},
            {"category": "washing_machine", "center": [1.5, 1.0, 0.5], "size": [0.7, 0.7, 1.0], "rotation_z_deg": 0},
        ],
    }))
    result = build_structure(ply, layout, alignment, tmp_path / "structure.json")
    assert result["counts"] == {"walls": 4, "doors": 1, "windows": 0, "objects": 1, "geometric_obstacles": 0, "rejected": 1}
    assert result["objects"][0]["label"] == "bed"
    assert result["objects"][0]["geometry_status"] == "verified"
    assert result["rejected_objects"][0]["rejection_reason"] == "unsupported_category"
    assert result["doors"][0]["wall_id"] == 0


def test_low_evidence_window_is_rejected(tmp_path):
    rng = np.random.default_rng(11)
    points = np.column_stack((rng.uniform(-2, 2, 3000), rng.uniform(-1.5, 1.5, 3000), rng.uniform(0, 2.6, 3000)))
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(points))
    ply = tmp_path / "scene_aligned.ply"
    o3d.io.write_point_cloud(str(ply), cloud)
    alignment = tmp_path / "alignment.json"
    alignment.write_text(json.dumps({"extents_m": {"x": [-2, 2], "y": [-1.5, 1.5], "z": [0, 2.6]}}))
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps({"walls": [], "doors": [], "windows": [{"center": [0, 1.4, 1.2], "size": [1.2, 0.1, 1.0]}], "objects": []}))
    result = build_structure(ply, layout, alignment, tmp_path / "structure.json")
    assert result["windows"] == []
    assert result["rejected_openings"][0]["kind"] == "window"


def test_window_near_right_wall_does_not_jump_to_opposite_wall(tmp_path):
    rng = np.random.default_rng(13)
    right = np.column_stack((np.full(5000, 2.0), rng.uniform(-1.5, 1.5, 5000), rng.uniform(0, 2.6, 5000)))
    left = np.column_stack((np.full(5000, -2.0), rng.uniform(-1.5, 1.5, 5000), rng.uniform(0, 2.6, 5000)))
    floor = np.column_stack((rng.uniform(-2, 2, 1500), rng.uniform(-1.5, 1.5, 1500), np.zeros(1500)))
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.vstack((right, left, floor))))
    ply = tmp_path / "scene_aligned.ply"
    o3d.io.write_point_cloud(str(ply), cloud)
    alignment = tmp_path / "alignment.json"
    alignment.write_text(json.dumps({"extents_m": {"x": [-2, 2], "y": [-1.5, 1.5], "z": [0, 2.6]}}))
    layout = tmp_path / "layout.json"
    layout.write_text(json.dumps({
        "walls": [{"id": 7, "center": [2.0, 0.0, 1.3], "size": [3.0, 0.05, 2.6], "rotation_z_deg": 90}],
        "doors": [], "windows": [{"wall_id": 7, "center": [1.86, 0.1, 1.25], "size": [1.2, 0.1, 1.0]}], "objects": []
    }))
    result = build_structure(ply, layout, alignment, tmp_path / "structure.json")
    assert len(result["windows"]) == 1
    assert result["windows"][0]["wall_id"] == 1
    assert result["windows"][0]["geometry_status"] == "semantic_supported"


def test_targeted_object_aliases_and_overlap_are_deduplicated(tmp_path):
    rng = np.random.default_rng(17)
    bed = rng.uniform([-1.0, -0.7, 0.05], [1.0, 0.7, 0.55], size=(3500, 3))
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(bed))
    ply = tmp_path / "scene_aligned.ply"; o3d.io.write_point_cloud(str(ply), cloud)
    alignment = tmp_path / "alignment.json"
    alignment.write_text(json.dumps({"extents_m": {"x": [-2, 2], "y": [-1.5, 1.5], "z": [0, 2.6]}}))
    arch = tmp_path / "layout.json"; arch.write_text(json.dumps({"walls": [], "doors": [], "windows": [], "objects": []}))
    objects = tmp_path / "furniture.json"
    objects.write_text(json.dumps({"objects": [
        {"category": "multifunctional_combination_bed", "center": [0, 0, 0.3], "size": [2.2, 1.6, 0.7], "rotation_z_deg": 0},
        {"category": "coffee_table", "center": [0, 0, 0.3], "size": [2.1, 1.5, 0.7], "rotation_z_deg": 0},
    ]}))
    result = build_structure(ply, arch, alignment, tmp_path / "structure.json", objects)
    assert [item["label"] for item in result["objects"]] == ["bed"]
    assert any(item.get("rejection_reason", "").startswith("duplicate_of") for item in result["rejected_objects"])


def test_unlabelled_floor_obstacle_is_kept_for_risk_analysis(tmp_path):
    rng = np.random.default_rng(23)
    floor = np.column_stack((rng.uniform(-2, 2, 2500), rng.uniform(-1.5, 1.5, 2500), rng.normal(0, 0.003, 2500)))
    obstacle = rng.uniform([0.65, -0.15, 0.08], [1.05, 0.25, 0.65], size=(2600, 3))
    cloud = o3d.geometry.PointCloud(o3d.utility.Vector3dVector(np.vstack((floor, obstacle))))
    ply = tmp_path / "scene_aligned.ply"; o3d.io.write_point_cloud(str(ply), cloud)
    alignment = tmp_path / "alignment.json"
    alignment.write_text(json.dumps({"extents_m": {"x": [-2, 2], "y": [-1.5, 1.5], "z": [0, 2.6]}}))
    layout = tmp_path / "layout.json"; layout.write_text(json.dumps({"walls": [], "doors": [], "windows": [], "objects": []}))
    result = build_structure(ply, layout, alignment, tmp_path / "structure.json")
    assert len(result["geometric_obstacles"]) == 1
    item = result["geometric_obstacles"][0]
    assert item["label"] == "unknown_obstacle"
    assert item["size"][0] >= 0.30 and item["size"][1] >= 0.30
    assert item["height_range_m"][1] >= 0.5
