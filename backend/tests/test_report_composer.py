"""report_composer 测试：风险几何构建 + 完整合成报告（PDF+标注图）。"""
from pathlib import Path

import numpy as np
import pytest

from pipeline.report_composer import (
    ComposedReport,
    build_risk_geometries,
    compose_report,
)
from pipeline.risk_visualization import RiskGeometry

pytest.importorskip("reportlab")

SAMPLE_RISKS = [
    {"code": "passage_width", "name": "通道净宽", "level": "red", "measure": 0.72, "unit": "m"},
    {"code": "threshold", "name": "门槛高度", "level": "yellow", "measure": 0.03, "unit": "m"},
    {"code": "obstacle", "name": "通道障碍物", "level": "red",
     "measure": [{"label": "纸箱", "obb": True}]},
    {"code": "slope", "name": "地面坡度", "level": "green", "measure": 0.01, "unit": ""},
]
MEASURES = {
    "passage_width_m": 0.72,
    "narrowest_point": [1.0, 0.0, 0.02],
    "threshold_m": 0.03,
}
SEMANTIC = {
    "纸箱": {"center": [0.8, 0.4, 0.3], "axes": np.eye(3).tolist(),
             "extents": [0.3, 0.2, 0.4]},
}


def test_build_risk_geometries_types():
    geoms = build_risk_geometries(SAMPLE_RISKS, MEASURES, SEMANTIC)
    kinds = sorted(g.kind for g in geoms)
    assert kinds == ["arrow", "box", "segment"]
    assert all(isinstance(g, RiskGeometry) for g in geoms)


def test_passage_segment_length_matches_width():
    geoms = build_risk_geometries(SAMPLE_RISKS, MEASURES, SEMANTIC)
    seg = next(g for g in geoms if g.kind == "segment")
    p1 = np.asarray(seg.params["p1"])
    p2 = np.asarray(seg.params["p2"])
    assert abs(np.linalg.norm(p2 - p1) - 0.72) < 1e-6


def test_obstacle_without_semantic_objects_skipped():
    geoms = build_risk_geometries(SAMPLE_RISKS, MEASURES, None)
    assert not any(g.kind == "box" for g in geoms)


def _scene_points():
    rng = np.random.default_rng(7)
    floor = np.c_[rng.uniform(-2, 2, 600), rng.uniform(-1.5, 1.5, 600), np.zeros(600)]
    wall = np.c_[np.full(150, 2.0), rng.uniform(-1.5, 1.5, 150), rng.uniform(0.05, 2.2, 150)]
    return np.vstack([floor, wall])


def test_compose_report_with_points(tmp_path):
    out_dir = tmp_path / "compose-test"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = compose_report(
        title="合成报告",
        score=76.0,
        risks=SAMPLE_RISKS,
        measures=MEASURES,
        advice=["清理纸箱"],
        points=_scene_points(),
        out_dir=out_dir,
        semantic_objects=SEMANTIC,
        n_views=1,
    )
    assert isinstance(report, ComposedReport)
    assert report.pdf_path and Path(report.pdf_path).is_file()
    assert Path(report.pdf_path).read_bytes().startswith(b"%PDF")
    assert report.risk_geometries


def test_compose_report_without_points_pdf_only(tmp_path):
    out_dir = tmp_path / "compose-test"
    out_dir.mkdir(parents=True, exist_ok=True)
    report = compose_report(
        title="无点云报告",
        score=60.0,
        risks=SAMPLE_RISKS,
        measures=MEASURES,
        advice=[],
        points=None,
        out_dir=out_dir,
    )
    assert report.pdf_path and Path(report.pdf_path).is_file()


def test_compose_report_accepts_formal_assessment_as_single_source(tmp_path):
    out_dir = tmp_path / "report-composer-formal"
    assessment = {
        "official": True,
        "overall": {"status": "evaluated", "score": 75.0, "coverage_percent": 100.0},
        "category_scores": {},
        "risks": [],
        "advice": [],
        "confidence": {},
        "key_metrics": [],
        "not_evaluable": [],
    }
    report = compose_report(
        title="正式评估", score=1.0, risks=SAMPLE_RISKS, measures={}, advice=["旧建议"],
        points=None, out_dir=out_dir, risk_assessment=assessment,
    )
    assert report.status == "ok"
    assert report.pdf_path and Path(report.pdf_path).is_file()
    assert report.risk_geometries == []
    assert report.risk_images == []
    assert report.status == "ok"
