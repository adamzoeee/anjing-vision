import numpy as np

from pipeline.instance_builder import build_semantic_instances
from pipeline.semantic import SemanticFusion


def _fusion(points, label, detection_groups):
    point_labels = {index: label for index in range(len(points))}
    votes = {index: {label: 2.7} for index in range(len(points))}
    support = {index: {label: 3} for index in range(len(points))}
    detections = []
    for view_id, ids in detection_groups:
        detections.append({
            "view_id": view_id, "image_name": f"{view_id:05d}.jpg", "label": label,
            "score": 0.9, "mask_score": 0.95, "mask_area_px": 200,
            "point_ids": np.asarray(ids, dtype=np.int64),
        })
    return SemanticFusion(
        visible_views=np.full(len(points), 3), votes=votes, supporting_views=support,
        point_labels=point_labels, semantic_score={index: 0.9 for index in range(len(points))},
        consistency={index: 1.0 for index in range(len(points))},
        detection_support=detections,
    )


def _structure():
    return {"room": {"bounds_xy": {"min": [-3.0, -3.0], "max": [3.0, 3.0]}}}


def _candidate(label, center, size):
    return {"objects": [{
        "candidate_id": "candidate_001",
        "spatiallm_candidate": {
            "label": label, "normalized_label": label,
            "center": center, "size": size, "rotation_z_deg": 0.0,
        },
        "geometry": {"bbox": None},
    }]}


def test_one_spatial_candidate_with_two_same_label_objects_becomes_two_instances():
    rng = np.random.default_rng(31)
    left = rng.normal([-1.0, 0.0, 1.0], [0.08, 0.08, 0.25], size=(140, 3))
    right = rng.normal([1.0, 0.0, 1.0], [0.08, 0.08, 0.25], size=(150, 3))
    points = np.vstack([left, right])
    groups = []
    for view in (1, 2, 3):
        groups.extend([(view, np.arange(140)), (view, np.arange(140, 290))])

    payload, diagnostics, point_sets = build_semantic_instances(
        points, _fusion(points, "书架", groups), _structure(),
        _candidate("bookshelf", [0, 0, 1], [3.0, 1.0, 2.0]),
    )

    stable = [item for item in payload["instances"] if item["status"] == "stable"]
    assert [item["instance_id"] for item in stable] == ["bookshelf_001", "bookshelf_002"]
    assert all(item["bbox"] is None and item["bbox_status"] == "pending_stage4" for item in stable)
    assert all(item["support_views"] == 3 for item in stable)
    assert set(point_sets) == {"bookshelf_001", "bookshelf_002"}
    candidate = diagnostics["candidate_results"][0]
    assert candidate["generated_instance_count"] == 2
    assert candidate["decision"] == "split_into_multiple_instances"


def test_wall_points_are_removed_before_furniture_instance_clustering():
    rng = np.random.default_rng(32)
    cabinet = rng.normal([0.0, 0.0, 1.0], [0.12, 0.08, 0.3], size=(160, 3))
    wall = np.column_stack([
        rng.normal(3.0, 0.01, 300), rng.uniform(-1.5, 1.5, 300), rng.uniform(0.1, 2.5, 300),
    ])
    points = np.vstack([cabinet, wall])
    groups = [(view, np.arange(160)) for view in (1, 2, 3)]

    payload, diagnostics, point_sets = build_semantic_instances(
        points, _fusion(points, "柜子", groups), _structure(),
        _candidate("cabinet", [0, 0, 1], [1.0, 1.0, 2.5]),
    )

    stable = [item for item in payload["instances"] if item["status"] == "stable"]
    assert len(stable) == 1
    ids = point_sets[stable[0]["instance_id"]]
    assert np.max(points[ids, 0]) < 1.0
    label_diag = next(item for item in diagnostics["labels"] if item["semantic_label"] == "柜子")
    assert label_diag["filtering"]["wall_removed"] >= 290


def test_stage2_bed_points_remain_one_stable_instance():
    rng = np.random.default_rng(33)
    bed = rng.normal([0.0, 0.0, 0.45], [0.45, 0.3, 0.08], size=(500, 3))
    groups = [(view, np.arange(len(bed))) for view in (4, 8, 12, 16)]

    payload, _diagnostics, _point_sets = build_semantic_instances(
        bed, _fusion(bed, "床", groups), _structure(),
        _candidate("bed", [0, 0, 0.45], [2.0, 1.5, 0.6]),
    )

    stable = [item for item in payload["instances"] if item["status"] == "stable"]
    assert len(stable) == 1
    assert stable[0]["instance_id"] == "bed_001"
    assert stable[0]["support_views"] == 4
    assert stable[0]["point_count"] >= 450

