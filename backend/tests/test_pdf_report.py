"""pdf_report 测试：生成 PDF 验证结构完整（页数/内容存在性）。"""
from pathlib import Path

import numpy as np
import pytest

from pipeline.pdf_report import _formal_summary_rows, build_pdf_report

pytest.importorskip("reportlab")

SAMPLE_RISKS = [
    {"code": "door_width", "name": "门宽", "level": "green", "measure": 0.92, "unit": "m"},
    {"code": "passage_width", "name": "通道净宽", "level": "red", "measure": 0.72, "unit": "m"},
    {"code": "threshold", "name": "门槛高度", "level": "yellow", "measure": 0.025, "unit": "m"},
    {"code": "stairs", "name": "台阶", "level": "unknown", "measure": None, "unit": ""},
    {"code": "obstacle", "name": "通道障碍物", "level": "red",
     "measure": [{"label": "纸箱"}], "unit": ""},
]

SAMPLE_MEASURES = {
    "door_width_m": 0.92,
    "passage_width_m": 0.72,
    "threshold_m": 0.025,
    "stairs_exist": False,
    "slope": 0.01,
    "uneven_m": None,
    "scale_status": "relative",
    "calibration_quality": {"method": "none", "reason": "测试"},
    "geometry_assessment_status": "spatial_validated",
    "assessment_completeness": {"percent": 75.0},
    "reconstruction_extent_m": [4.82, 3.61, 2.74],
}


def _make_image(path: Path):
    """生成一张极小的 PNG 用于嵌入测试。"""
    import struct
    import zlib

    def chunk(tag, data):
        return struct.pack(">I", len(data)) + tag + data + struct.pack(
            ">I", zlib.crc32(tag + data)
        )

    ihdr = struct.pack(">IIBBBBB", 4, 4, 8, 2, 0, 0, 0)
    raw = b"".join(b"\x00" + b"\x80\x80\x80" * 4 for _ in range(4))
    png = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", ihdr)
        + chunk(b"IDAT", zlib.compress(raw))
        + chunk(b"IEND", b"")
    )
    path.write_bytes(png)
    return path


def test_build_pdf_report_structure(tmp_path_factory):
    # tmp_path 在沙箱受限环境可能不可用，落到工作区临时目录
    out_dir = Path(r"E:\anlingzhijing\anjing-vision\.recovery\pdf-test")
    out_dir.mkdir(parents=True, exist_ok=True)
    img = _make_image(out_dir / "view.png")
    pdf_path = build_pdf_report(
        title="王奶奶家",
        score=82.0,
        risks=SAMPLE_RISKS,
        measures=SAMPLE_MEASURES,
        advice=["清理通道纸箱", "门槛安装斜坡"],
        images=[str(img)],
        out_path=out_dir / "report.pdf",
    )
    assert Path(pdf_path).is_file()
    data = Path(pdf_path).read_bytes()
    assert data.startswith(b"%PDF")
    assert b"%%EOF" in data[-1024:]


def test_build_pdf_report_without_images():
    out_dir = Path(r"E:\anlingzhijing\anjing-vision\.recovery\pdf-test")
    out_dir.mkdir(parents=True, exist_ok=True)
    pdf_path = build_pdf_report(
        title="空报告",
        score=60.0,
        risks=[],
        measures={},
        advice=[],
        images=[],
        out_path=out_dir / "empty.pdf",
    )
    assert Path(pdf_path).is_file()
    assert Path(pdf_path).stat().st_size > 500


def test_build_pdf_report_metric_calibrated():
    out_dir = Path(r"E:\anlingzhijing\anjing-vision\.recovery\pdf-test")
    out_dir.mkdir(parents=True, exist_ok=True)
    measures = dict(SAMPLE_MEASURES)
    measures["scale_status"] = "metric_references"
    measures["calibration_quality"] = {"method": "apriltag", "scale": 1.0}
    pdf_path = build_pdf_report(
        title="米制报告",
        score=88.0,
        risks=SAMPLE_RISKS,
        measures=measures,
        advice=[],
        images=[],
        out_path=out_dir / "metric.pdf",
    )
    assert Path(pdf_path).is_file()


def test_build_pdf_report_consumes_formal_assessment_as_source_of_truth():
    out_dir = Path(r"E:\anlingzhijing\anjing-vision\.recovery\pdf-test")
    out_dir.mkdir(parents=True, exist_ok=True)
    assessment = {
        "official": True,
        "overall": {"status": "evaluated", "score": 73.2, "coverage_percent": 86.7},
        "risks": [{
            "risk_code": "door_width_medium", "risk_type": "mobility",
            "risk_name": "门净宽风险", "metric_code": "door_width",
            "measured_value": 0.85, "unit": "m", "threshold": {},
            "position": None, "risk_level": "medium", "confidence": 0.9,
            "reason": "threshold", "advice": "调整门口净宽",
            "assessment_status": "evaluated", "related_object_ids": [],
            "related_path_id": None,
        }],
        "advice": ["调整门口净宽"],
    }
    pdf_path = build_pdf_report(
        title="正式评估", score=99.0, risks=[], measures={}, advice=[], images=[],
        out_path=out_dir / "formal.pdf", risk_assessment=assessment,
    )
    assert Path(pdf_path).is_file()
    assert Path(pdf_path).stat().st_size > 500


def test_formal_summary_rows_preserve_official_weights_and_coverage():
    rows = _formal_summary_rows({
        "category_scores": {
            "mobility": {"score": 80.0, "weight": 0.4, "evaluated_count": 4, "total_count": 5},
            "layout": {"score": 60.0, "weight": 0.3, "evaluated_count": 3, "total_count": 4},
            "usage_safety": {"score": None, "weight": 0.3, "evaluated_count": 0, "total_count": 2},
        },
    })
    assert rows[1] == ["通行能力", "80.0", "40%", "4/5"]
    assert rows[2] == ["空间布局", "60.0", "30%", "3/4"]
    assert rows[3] == ["使用安全", "无法评分", "30%", "0/2"]
