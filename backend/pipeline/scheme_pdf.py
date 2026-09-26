"""改造方案 PDF 附录：客户端渲染的叠影图 + 指标/风险变化 + 声明。"""
from __future__ import annotations

import io
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import Image, Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

DEFAULT_DISCLAIMER = "沙盒模拟 · 非实测：本页分数由正式评估链路在假设布局上重算，不代表现场实测结论。"


def _fmt(value) -> str:
    return "无法评分" if value is None else f"{float(value):.1f}"


def _fmt_delta(before, after) -> str:
    if before is None or after is None:
        return "—"
    delta = float(after) - float(before)
    return f"{'+' if delta >= 0 else ''}{delta:.1f}"


def _table(rows: list[list[str]], style: ParagraphStyle) -> Table:
    data = [[Paragraph(str(cell), style) for cell in row] for row in rows]
    table = Table(data, hAlign="LEFT")
    table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D1D1D6")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F2F2F7")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 6),
        ("RIGHTPADDING", (0, 0), (-1, -1), 6),
    ]))
    return table


def build_scheme_appendix_pdf(*, scan_name: str, scheme_name: str, created_at: str,
                              plan_png: bytes, before_score, after_score,
                              metric_changes: list[dict], risk_changes: list[dict],
                              provenance: dict, disclaimer: str, output: Path) -> Path:
    output = Path(output)
    output.parent.mkdir(parents=True, exist_ok=True)
    styles = getSampleStyleSheet()
    body = ParagraphStyle("body", parent=styles["Normal"], fontName="STSong-Light",
                          fontSize=10, leading=15)
    title = ParagraphStyle("title", parent=body, fontSize=17, leading=24, spaceAfter=6)

    flow = [
        Paragraph("改造方案附录（沙盒模拟）", title),
        Paragraph(f"项目：{scan_name}　方案：{scheme_name}", body),
        Paragraph(f"生成时间：{created_at}", body),
        Paragraph(f"评分变化：{_fmt(before_score)} → {_fmt(after_score)}"
                  f"（Δ {_fmt_delta(before_score, after_score)}）", body),
        Spacer(1, 6 * mm),
        Image(io.BytesIO(plan_png), width=110 * mm, height=110 * mm),
        Spacer(1, 4 * mm),
        Paragraph("关键指标变化", body),
        _table([["指标", "改造前", "改造后"]] +
               [[m["name"], f"{m['before']}{m.get('unit') or ''}", f"{m['after']}{m.get('unit') or ''}"]
                for m in metric_changes], body),
        Spacer(1, 4 * mm),
        Paragraph("风险等级变化", body),
        _table([["风险项", "改造前", "改造后"]] +
               [[r["name"], r.get("before") or "-", r.get("after") or "-"] for r in risk_changes], body),
        Spacer(1, 6 * mm),
        Paragraph(disclaimer or DEFAULT_DISCLAIMER, body),
        Paragraph(f"输入指纹：{provenance}", body),
    ]
    SimpleDocTemplate(str(output), pagesize=A4, title=f"{scan_name} · {scheme_name}").build(flow)
    return output
