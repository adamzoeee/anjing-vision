import json

from pipeline.structure_builder import _new_object_diagnostic, _write_object_diagnostics


def test_object_diagnostics_trace_final_decisions_without_semantic_inference(tmp_path):
    accepted_record = _new_object_diagnostic(
        1,
        {"category": "bookcase", "center": [1, 2, 1], "size": [1, 0.4, 2], "rotation_z_deg": 90},
    )
    rejected_record = _new_object_diagnostic(
        2,
        {"category": "wardrobe", "center": [2, 2, 1], "size": [1, 0.5, 2]},
    )
    accepted_record["geometry"].update(
        points_before_filter=420,
        points_after_filter=260,
        cluster_count=2,
        selected_cluster_points=210,
    )
    output = tmp_path / "diagnostics" / "objects.json"

    payload = _write_object_diagnostics(
        output,
        [accepted_record, rejected_record],
        [{
            "_diagnostic_id": "candidate_001", "instance_id": "bookshelf_01",
            "center": [1.0, 2.0, 1.0], "size": [1.0, 0.4, 2.0],
            "rotation_z_deg": 1.57, "geometry_confidence": 0.8,
        }],
        [{"_diagnostic_id": "candidate_002", "rejection_reason": "insufficient_cluster"}],
        video_fusion_status="not_available",
        source_files={"geometry": "scene_aligned.ply"},
    )

    assert output.is_file()
    assert json.loads(output.read_text(encoding="utf-8")) == payload
    assert payload["decision_behavior"] == "observational_only"
    assert payload["semantic_pipeline_status"] == "not_run"
    assert payload["counts"] == {"candidates": 2, "accepted": 1, "rejected": 1}
    assert payload["objects"][0]["instance_id"] == "bookshelf_01"
    assert payload["objects"][0]["semantic_evidence"]["support_views"] == 0
    assert payload["objects"][0]["geometry"]["selected_cluster_points"] == 210
    assert payload["objects"][1]["reject_reason"] == "insufficient_cluster"

