import numpy as np

from pipeline.instance_builder import build_semantic_instances
from pipeline.semantic import SemanticFusion


def _structure():
    return {"room": {"bounds_xy": {"min": [-5.0, -5.0], "max": [5.0, 5.0]}}}


def _fusion(points, detections):
    votes = {}
    support = {}
    point_labels = {}
    rows = []
    for mask_index, (view, label, ids, camera) in enumerate(detections):
        ids = np.asarray(ids, dtype=np.int64)
        rows.append({
            "view_id": view, "frame_order": view, "camera_id": view,
            "observation_id": f"obs_{view}_{mask_index}", "mask_id": mask_index,
            "image_name": f"{view:05d}.jpg", "label": label, "score": 0.92,
            "mask_score": 0.95, "mask_area_px": max(len(ids) * 4, 1),
            "point_ids": ids, "camera_position": camera,
            "camera_direction": [0.0, 0.0, -1.0],
        })
        for point_id in ids:
            votes.setdefault(int(point_id), {})[label] = votes.setdefault(int(point_id), {}).get(label, 0.0) + 0.874
            support.setdefault(int(point_id), {})[label] = support.setdefault(int(point_id), {}).get(label, 0) + 1
    for point_id, labels in votes.items():
        winner = max(labels, key=labels.get)
        if support[point_id][winner] >= 2:
            point_labels[point_id] = winner
    return SemanticFusion(
        visible_views=np.full(len(points), max(len({row[0] for row in detections}), 1)),
        votes=votes, supporting_views=support, point_labels=point_labels,
        semantic_score={point_id: 0.9 for point_id in votes},
        consistency={point_id: 1.0 for point_id in votes}, detection_support=rows,
    )


def _build(points, detections):
    return build_semantic_instances(
        points, _fusion(points, detections), _structure(), {"objects": []},
    )


def test_partial_views_of_one_bed_form_one_complete_instance():
    rng = np.random.default_rng(61)
    points = rng.normal([0.0, 0.0, 0.55], [0.55, 0.35, 0.08], size=(420, 3))
    order = np.argsort(points[:, 0])
    detections = [
        (1, "床", order[:350], [-2.0, 0.0, 1.6]),
        (2, "床", order[35:385], [0.0, -2.0, 1.6]),
        (3, "床", order[70:], [2.0, 0.0, 1.6]),
        (4, "床", np.arange(len(points)), [0.0, 2.0, 1.6]),
    ]
    payload, diagnostics, point_sets = _build(points, detections)
    stable = [item for item in payload["instances"] if item["status"] == "stable"]
    assert len(stable) == 1
    assert len(point_sets[stable[0]["instance_id"]]) >= 350
    assert stable[0]["source_observations"] == [row["observation_id"] for row in diagnostics["_instance_observations"]["observations"]]


def test_two_same_class_objects_remain_two_instances():
    rng = np.random.default_rng(62)
    left = rng.normal([-1.3, 0.0, 0.8], [0.12, 0.12, 0.2], size=(180, 3))
    right = rng.normal([1.3, 0.0, 0.8], [0.12, 0.12, 0.2], size=(190, 3))
    points = np.vstack([left, right])
    detections = []
    for view, camera in ((1, [-2, -2, 1.5]), (2, [0, -2, 1.5]), (3, [2, -2, 1.5])):
        detections.extend([
            (view, "椅子", np.arange(180), camera),
            (view, "椅子", np.arange(180, 370), camera),
        ])
    payload, diagnostics, _ = _build(points, detections)
    assert len([item for item in payload["instances"] if item["status"] == "stable"]) == 2
    assert any(edge["reason"] == "same_view_separate_masks" for edge in diagnostics["association_edges"])


def test_far_fragments_merge_only_with_repeated_multiview_track_support():
    rng = np.random.default_rng(63)
    first = rng.normal([-0.8, 0.0, 0.55], [0.08, 0.08, 0.05], size=(140, 3))
    second = rng.normal([0.8, 0.0, 0.55], [0.08, 0.08, 0.05], size=(150, 3))
    points = np.vstack([first, second])
    ids = np.arange(len(points))
    detections = [
        (1, "床", ids, [-2, 0, 1.5]), (2, "床", ids, [0, -2, 1.5]),
        (3, "床", ids, [2, 0, 1.5]), (4, "床", ids, [0, 2, 1.5]),
    ]
    payload, diagnostics, point_sets = _build(points, detections)
    stable = [item for item in payload["instances"] if item["status"] == "stable"]
    assert len(stable) == 1
    assert len(point_sets[stable[0]["instance_id"]]) >= len(points) - 5
    assert any(event["reason"] == "repeated_multiview_track_support" for track in diagnostics["tracks"] for event in track["merge_events"])


def test_far_same_class_fragments_without_cross_view_relation_do_not_merge():
    rng = np.random.default_rng(64)
    first = rng.normal([-1.2, 0.0, 0.8], [0.1, 0.1, 0.2], size=(110, 3))
    second = rng.normal([1.2, 0.0, 0.8], [0.1, 0.1, 0.2], size=(120, 3))
    points = np.vstack([first, second])
    payload, _diagnostics, _ = _build(points, [
        (1, "柜子", np.arange(110), [-2, 0, 1.5]),
        (9, "柜子", np.arange(110, 230), [2, 0, 1.5]),
    ])
    assert len(payload["instances"]) == 2
    assert all(item["status"] != "stable" for item in payload["instances"])


def test_table_and_desk_aliases_share_one_track():
    rng = np.random.default_rng(65)
    points = rng.normal([0.0, 0.0, 0.8], [0.4, 0.25, 0.08], size=(360, 3))
    ids = np.arange(len(points))
    payload, _diagnostics, _ = _build(points, [
        (1, "桌子", ids, [-2, 0, 1.5]), (2, "书桌", ids, [0, -2, 1.5]),
        (3, "桌子", ids, [2, 0, 1.5]), (4, "书桌", ids, [0, 2, 1.5]),
    ])
    assert len(payload["instances"]) == 1
    item = payload["instances"][0]
    assert item["canonical_group"] == "table_group"
    assert set(item["observation_semantic_votes"]) == {"书桌", "桌子"}


def test_small_isolated_detection_is_retained_as_low_confidence():
    rng = np.random.default_rng(66)
    points = rng.normal([0.0, 0.0, 0.7], [0.05, 0.05, 0.05], size=(24, 3))
    payload, _diagnostics, _ = _build(points, [(1, "柜子", np.arange(24), [-2, 0, 1.5])])
    assert len(payload["instances"]) == 1
    assert payload["instances"][0]["status"] == "low_confidence"
    assert "too_few_semantic_points" in payload["instances"][0]["status_reason"]


def test_vertical_bed_fragment_is_explained_and_fails_completeness():
    rng = np.random.default_rng(67)
    z = np.linspace(0.15, 2.2, 390)
    points = np.column_stack([rng.normal(0.0, 0.03, len(z)), rng.normal(0.0, 0.04, len(z)), z])
    detections = [
        (1, "床", np.arange(0, 180), [-2.00, 0.0, 1.4]),
        (2, "床", np.arange(100, 300), [-1.95, 0.02, 1.4]),
        (3, "床", np.arange(220, 390), [-1.90, 0.04, 1.4]),
    ]
    payload, diagnostics, _ = _build(points, detections)
    assert payload["instances"]
    item = max(payload["instances"], key=lambda value: value["point_count"])
    assert item["status"] != "stable"
    assert "insufficient_view_diversity" in item["status_reason"] or "instance_boundary_unstable" in item["status_reason"]
    assert len(item["source_observations"]) >= 2
    records = diagnostics["_instance_observations"]["observations"]
    assert all(record["projected_point_ids"] for record in records)


def test_conflicting_spatial_candidate_evidence_is_purified_before_fragment_merge():
    rng = np.random.default_rng(68)
    horizontal = rng.normal([-0.8, 0.0, 0.5], [0.2, 0.15, 0.04], size=(180, 3))
    vertical = rng.normal([0.8, 0.0, 1.2], [0.04, 0.15, 0.35], size=(190, 3))
    points = np.vstack([horizontal, vertical])
    ids = np.arange(len(points))
    detections = [
        (1, "床", ids, [-2, 0, 1.5]), (2, "床", ids, [0, -2, 1.5]),
        (3, "床", ids, [2, 0, 1.5]),
    ]
    candidates = {"objects": [
        {"candidate_id": "candidate_bed", "spatiallm_candidate": {
            "normalized_label": "bed", "center": [-0.8, 0.0, 0.5],
            "size": [0.9, 0.8, 0.4], "rotation_z_deg": 0.0,
        }, "geometry": {"bbox": None}},
        {"candidate_id": "candidate_storage", "spatiallm_candidate": {
            "normalized_label": "wardrobe", "center": [0.8, 0.0, 1.2],
            "size": [0.5, 0.8, 1.8], "rotation_z_deg": 0.0,
        }, "geometry": {"bbox": None}},
    ]}
    payload, diagnostics, _ = build_semantic_instances(
        points, _fusion(points, detections), _structure(), candidates,
    )
    assert len(payload["instances"]) == 1
    quality = diagnostics["_semantic_observation_quality"]
    assert quality["counts"]["rejected_points"] >= len(vertical) - 5
