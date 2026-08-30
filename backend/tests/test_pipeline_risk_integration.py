import json

from app.tasks.pipeline_runner import (
    _build_formal_assessment,
    _build_formal_pdf,
    _build_formal_risk_map,
)


def test_pipeline_helper_writes_formal_assessment_artifacts(tmp_path):
    post = tmp_path / "postprocess"
    post.mkdir()
    structure = {
        "room": {
            "bounds_xy": {"min": [0, 0], "max": [4, 3]},
            "floor_polygon": [[0, 0], [4, 0], [4, 3], [0, 3]],
        },
        "semantic_instances": [],
    }
    measurements = {
        "room": {
            "length_m": 4, "width_m": 3, "height_m": 2.6,
            "measurement_status": "verified", "confidence": "high",
        },
        "openings": [], "objects": [],
    }
    (post / "structure.json").write_text(json.dumps(structure), encoding="utf-8")
    (post / "measurements.json").write_text(json.dumps(measurements), encoding="utf-8")
    outputs = _build_formal_assessment(tmp_path, scan_id=46)
    assert outputs["spatial_metrics"].is_file()
    assert outputs["risk_assessment"].is_file()
    assert outputs["risk_payload"]["overall"]["status"] == "insufficient_data"
    assert outputs["risk_payload"]["overall"]["score"] is None


def test_pipeline_helper_generates_pdf_from_same_formal_payload(tmp_path):
    assessment = {
        "official": True,
        "overall": {"status": "evaluated", "score": 80.0, "coverage_percent": 100.0},
        "category_scores": {}, "confidence": {}, "key_metrics": [],
        "not_evaluable": [], "risks": [], "advice": [],
    }
    pdf_path = _build_formal_pdf(tmp_path, 46, assessment, {"scale_status": "relative"})
    assert pdf_path == str(tmp_path / "report" / "report.pdf")
    assert (tmp_path / "report" / "report.pdf").read_bytes().startswith(b"%PDF")


def test_pipeline_helper_generates_formal_risk_map_from_artifacts(tmp_path):
    post = tmp_path / "postprocess"
    post.mkdir()
    (post / "risk_assessment.json").write_text(json.dumps({"risks": [{
        "risk_code": "passage", "risk_name": "通道", "risk_level": "high",
        "assessment_status": "evaluated", "position": {"point_xy": [1, 1]},
        "measured_value": 0.48, "unit": "m",
    }]}), encoding="utf-8")
    (post / "structure.json").write_text(json.dumps({
        "room": {"floor_polygon": [[0, 0], [4, 0], [4, 3], [0, 3]]},
    }), encoding="utf-8")
    image_path = _build_formal_risk_map(tmp_path)
    assert image_path == str(tmp_path / "images" / "formal_risks.png")
    assert (tmp_path / "images" / "formal_risks.png").read_bytes().startswith(b"\x89PNG")
