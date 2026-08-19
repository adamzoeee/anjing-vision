import copy
import json

import numpy as np

from pipeline.semantic import SemanticFusion
from pipeline.semantic_evidence import (
    annotate_structure_semantics,
    mark_semantics_unavailable,
    select_keyframe_indices,
)


def test_keyframe_selection_is_uniform_bounded_and_never_duplicates_low_fps_input():
    assert select_keyframe_indices(0) == []
    assert select_keyframe_indices(7) == list(range(7))
    selected = select_keyframe_indices(600)
    assert len(selected) == 30
    assert selected[0] == 0 and selected[-1] == 599
    assert len(selected) == len(set(selected))


def test_semantic_evidence_is_additive_and_never_deletes_or_resizes_baseline_objects():
    points = np.array([
        [0.0, 0.0, 0.5], [0.2, 0.0, 0.5], [-0.2, 0.0, 0.5],
        [0.0, 0.2, 0.5], [0.0, -0.2, 0.5], [2.0, 2.0, 0.5],
    ])
    structure = {
        "objects": [{
            "instance_id": "desk_01", "label": "desk", "center": [0, 0, 0.5],
            "size": [1, 1, 1], "rotation_z_deg": 0.0,
        }],
        "counts": {"objects": 1},
    }
    diagnostics = {"objects": [{
        "candidate_id": "candidate_001", "instance_id": "desk_01",
        "spatiallm_candidate": {"normalized_label": "desk"},
        "geometry": {"bbox": {"center": [0, 0, 0.5], "size": [1, 1, 1], "rotation_z_deg": 0}},
    }]}
    fusion = SemanticFusion(
        visible_views=np.full(len(points), 3),
        votes={index: {"书桌": 2.5} for index in range(5)},
        supporting_views={index: {"书桌": 3} for index in range(5)},
        point_labels={index: "书桌" for index in range(5)},
        semantic_score={index: 0.8 for index in range(5)},
        consistency={index: 1.0 for index in range(5)},
        diagnostics={"labeled_point_count": 5},
        detection_support=[
            {"view_id": view, "image_name": f"{view:05d}.jpg", "label": "书桌",
             "score": 0.9, "mask_score": 0.95, "mask_area_px": 100,
             "point_ids": np.arange(5)}
            for view in (1, 2, 3)
        ],
    )
    baseline = copy.deepcopy(structure["objects"][0])

    enriched, traced = annotate_structure_semantics(points, structure, diagnostics, fusion)

    assert len(enriched["objects"]) == 1
    for key in ("instance_id", "label", "center", "size", "rotation_z_deg"):
        assert enriched["objects"][0][key] == baseline[key]
    assert enriched["objects"][0]["semantic_status"] == "supported"
    assert enriched["objects"][0]["semantic_confidence"] == "medium"
    assert enriched["objects"][0]["semantic_support_views"] == 3
    evidence = traced["objects"][0]["semantic_evidence"]
    assert evidence["support_view_ids"] == [1, 2, 3]
    assert evidence["groundingdino_detections"] == 3
    assert evidence["sam_masks"] == 3


def test_unavailable_semantics_marks_unknown_without_removing_object(tmp_path):
    structure_path = tmp_path / "structure.json"
    diagnostics_path = tmp_path / "diagnostics" / "objects.json"
    diagnostics_path.parent.mkdir(parents=True)
    original = {"objects": [{"instance_id": "bed_01", "label": "bed", "size": [2, 1.5, 0.5]}]}
    structure_path.write_text(json.dumps(original), encoding="utf-8")
    diagnostics_path.write_text(json.dumps({"objects": [{"instance_id": "bed_01"}]}), encoding="utf-8")

    mark_semantics_unavailable(structure_path, diagnostics_path, "models unavailable")

    structure = json.loads(structure_path.read_text(encoding="utf-8"))
    assert len(structure["objects"]) == 1
    assert structure["objects"][0]["label"] == "bed"
    assert structure["objects"][0]["size"] == [2, 1.5, 0.5]
    assert structure["objects"][0]["semantic_status"] == "unavailable"
    assert structure["objects"][0]["semantic_confidence"] == "unknown"

