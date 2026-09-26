import io

import PIL.Image
from pipeline.scheme_pdf import build_scheme_appendix_pdf


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    PIL.Image.new("RGB", (240, 240), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_build_scheme_appendix_pdf(tmp_path):
    out = tmp_path / "scheme.pdf"
    path = build_scheme_appendix_pdf(
        scan_name="王奶奶家", scheme_name="方案A · 书架下移",
        created_at="2026-09-26T18:20:00Z",
        plan_png=_png_bytes(),
        before_score=80.0, after_score=85.0,
        metric_changes=[{"name": "床边活动空间", "unit": "m", "before": 0.30, "after": 0.55}],
        risk_changes=[{"name": "床边活动空间不足", "before": "high", "after": "low"}],
        provenance={"measurements_sha256": "abc"},
        disclaimer="沙盒模拟 · 非实测",
        output=out,
    )
    assert path == out and out.is_file()
    assert out.read_bytes().startswith(b"%PDF")
    assert out.stat().st_size > 1500
