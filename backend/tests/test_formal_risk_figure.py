import json

from pipeline.formal_risk_figure import collect_risk_markers, render_formal_risk_figure


def test_collect_risk_markers_uses_only_explicit_structured_positions():
    structure = {
        "semantic_instances": [{"instance_id": "bed_01", "center": [2.0, 1.0, 0.3]}],
    }
    assessment = {"risks": [
        {"risk_code": "passage", "risk_name": "通道", "risk_level": "high",
         "assessment_status": "evaluated", "position": {"point_xy": [1.2, 0.8]},
         "measured_value": 0.48, "unit": "m"},
        {"risk_code": "bed", "risk_name": "床侧", "risk_level": "medium",
         "assessment_status": "evaluated", "position": {"object_id": "bed_01"},
         "measured_value": 0.3, "unit": "m"},
        {"risk_code": "missing", "risk_name": "未知位置", "risk_level": "medium",
         "assessment_status": "evaluated", "position": None},
        {"risk_code": "unknown", "risk_name": "无法评估", "risk_level": None,
         "assessment_status": "not_evaluable", "position": {"point_xy": [3, 2]}},
    ]}
    markers = collect_risk_markers(assessment, structure)
    assert [item["risk_code"] for item in markers] == ["passage", "bed"]
    assert markers[1]["xy"] == [2.0, 1.0]


def test_render_formal_risk_figure_from_json_only(tmp_path):
    assessment_path = tmp_path / "risk_assessment.json"
    structure_path = tmp_path / "structure.json"
    output_path = tmp_path / "formal_risks.png"
    assessment_path.write_text(json.dumps({"risks": [{
        "risk_code": "passage", "risk_name": "通道", "risk_level": "high",
        "assessment_status": "evaluated", "position": {"point_xy": [1.2, 0.8]},
        "measured_value": 0.48, "unit": "m",
    }]}), encoding="utf-8")
    structure_path.write_text(json.dumps({
        "room": {"floor_polygon": [[0, 0], [4, 0], [4, 3], [0, 3]]},
    }), encoding="utf-8")
    assert render_formal_risk_figure(assessment_path, structure_path, output_path) == output_path
    assert output_path.read_bytes().startswith(b"\x89PNG")
