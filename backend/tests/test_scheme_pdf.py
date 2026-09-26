"""方案 PDF 附录：结构完整 + 非实测声明与输入指纹文案。"""
import io

import PIL.Image
from reportlab.platypus import Paragraph

from pipeline.scheme_pdf import (
    build_scheme_appendix_pdf,
    ensure_disclaimer_label,
    scheme_appendix_flowables,
)


def _png_bytes() -> bytes:
    buffer = io.BytesIO()
    PIL.Image.new("RGB", (240, 240), (255, 255, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _pdf_kwargs() -> dict:
    return dict(
        scan_name="王奶奶家", scheme_name="方案A · 书架下移",
        created_at="2026-09-26T18:20:00Z",
        plan_png=_png_bytes(),
        before_score=80.0, after_score=85.0,
        metric_changes=[{"name": "床边活动空间", "unit": "m", "before": 0.30, "after": 0.55}],
        risk_changes=[{"name": "床边活动空间不足", "before": "high", "after": "low"}],
        provenance={"measurements_sha256": "abc"},
        disclaimer="沙盒模拟 · 非实测：本页分数由正式评估链路在假设布局上重算。",
    )


def _flowable_texts(flow) -> list[str]:
    return [item.getPlainText() for item in flow if isinstance(item, Paragraph)]


def test_build_scheme_appendix_pdf(tmp_path):
    out = tmp_path / "scheme.pdf"
    path = build_scheme_appendix_pdf(**_pdf_kwargs(), output=out)
    assert path == out and out.is_file()
    data = out.read_bytes()
    assert data.startswith(b"%PDF")
    assert b"%%EOF" in data[-1024:]
    assert out.stat().st_size > 1500

    # backend/.venv 未安装 pypdf，且 STSong-Light 为 CID 编码、文本提取不可靠，
    # 因此改用生成 PDF 的同一份 flowable 内容流断言文案：
    # 非实测声明与输入指纹必须出现在写进 PDF 的内容里。
    texts = _flowable_texts(scheme_appendix_flowables(**_pdf_kwargs()))
    assert any("沙盒模拟 · 非实测" in text for text in texts)
    assert any("measurements_sha256" in text and "abc" in text for text in texts)


def test_disclaimer_label_is_guaranteed(tmp_path):
    # 旧文案/空文案也必须补上统一标签，DEFAULT_DISCLAIMER 不再是死代码。
    assert "非实测" in ensure_disclaimer_label(None)
    assert ensure_disclaimer_label("仅为内存沙盒模拟").startswith("沙盒模拟 · 非实测")

    kwargs = _pdf_kwargs()
    kwargs["disclaimer"] = "仅为内存沙盒模拟"
    out = tmp_path / "legacy.pdf"
    build_scheme_appendix_pdf(**kwargs, output=out)
    assert out.read_bytes().startswith(b"%PDF")
    texts = _flowable_texts(scheme_appendix_flowables(**kwargs))
    assert any("沙盒模拟 · 非实测" in text for text in texts)
