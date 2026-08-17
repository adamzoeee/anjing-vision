"""PDF 智能报告（第四阶段）：评估结果 → 结构化 PDF。

内容：标题/评分（分级色）→ 评估完整度与标定状态 → 测量值表 →
风险项表（等级色块）→ 改造建议 → 标注图。

中文支持：reportlab 内置 CID 字体 STSong-Light（无需字体文件）。
"""
from __future__ import annotations

from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.cidfonts import UnicodeCIDFont
from reportlab.platypus import (
    Image,
    PageBreak,
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

pdfmetrics.registerFont(UnicodeCIDFont("STSong-Light"))

LEVEL_COLOR = {"red": "#E5484D", "yellow": "#F5B301", "green": "#2E9E5B", "unknown": "#8B98B5"}
LEVEL_TEXT = {"red": "高风险", "yellow": "中风险", "green": "正常", "unknown": "未评估"}

RISK_NAMES = {
    "door_width": "门宽", "passage_width": "通道净宽", "threshold": "门槛高度",
    "stairs": "台阶", "slope": "地面坡度", "uneven": "地面高差/不平",
    "obstacle": "通道障碍物", "bathroom_door": "卫生间门口",
}
RISK_ADVICE = {
    "door_width": "门宽不足 80cm 轮椅无法通行，建议扩门或改用折叠门。",
    "passage_width": "通道过窄，建议清理通道或调整家具布局。",
    "threshold": "门槛过高易绊倒，建议安装斜坡过渡条。",
    "stairs": "存在台阶且无扶手，建议安装扶手或坡道。",
    "slope": "地面坡度超标，轮椅有溜坡风险。",
    "uneven": "地面高差超过 1.5cm，建议找平或加缓坡。",
    "obstacle": "通道内存在杂物/障碍物，建议移除以保证通行。",
    "bathroom_door": "卫生间门口过窄，轮椅无法进出。",
}


def _style() -> dict:
    base = getSampleStyleSheet()
    font = "STSong-Light"

    def make(name, size, **kw):
        defaults = dict(fontName=font, fontSize=size, leading=size * 1.5)
        defaults.update(kw)
        return ParagraphStyle(name, **defaults)

    return {
        "title": make("t", 22, alignment=1, spaceAfter=6 * mm),
        "subtitle": make("st", 10, textColor="#8B98B5", alignment=1, spaceAfter=8 * mm),
        "h2": make("h2", 14, spaceBefore=6 * mm, spaceAfter=3 * mm, textColor="#1F2A44"),
        "body": make("b", 10.5, spaceAfter=2 * mm),
        "small": make("s", 9, textColor="#5A6478"),
        "score": make("sc", 46, alignment=1, spaceAfter=2 * mm),
    }


def _score_color(score: float) -> str:
    if score >= 80:
        return LEVEL_COLOR["green"]
    if score >= 60:
        return LEVEL_COLOR["yellow"]
    return LEVEL_COLOR["red"]


def _fmt(value, unit: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return f"{value:.2f}{unit}"
    return str(value)


def build_pdf_report(
    *,
    title: str,
    score: float,
    risks: list[dict],
    measures: dict,
    advice: list[str],
    images: list[str],
    out_path: str | Path,
) -> str:
    """生成 PDF 报告，返回输出路径。

    risks: rules.evaluate_risks 输出（含 code/name/level/measure/unit）。
    measures: 管道 measures dict。
    images: 标注图 PNG 路径列表（嵌入报告）。
    """
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    styles = _style()
    doc = SimpleDocTemplate(
        str(out_path), pagesize=A4,
        leftMargin=18 * mm, rightMargin=18 * mm, topMargin=14 * mm, bottomMargin=14 * mm,
    )
    story: list = []

    # 标题
    story.append(Paragraph(f"安龄智境 · {title}", styles["title"]))
    story.append(Paragraph("适老空间安全评估报告", styles["subtitle"]))

    # 评分
    completeness = (measures.get("assessment_completeness") or {}).get("percent")
    calibration = measures.get("calibration_quality") or {}
    calib_method = calibration.get("method", "—")
    scale_status = measures.get("scale_status", "relative")
    scale_text = "米制（已标定）" if scale_status == "metric_references" else "相对尺度"
    story.append(Paragraph(
        f'<font size="46" color="{_score_color(score)}"><b>{score:.1f}</b></font>',
        styles["score"],
    ))
    story.append(Paragraph(
        f'<font size="11" color="#5A6478">综合安全评分</font>'
        f'　评估完整度 {completeness if completeness is not None else "—"}%'
        f'　标定：{scale_text}（{calib_method}）',
        styles["score"],
    ))
    story.append(Spacer(1, 4 * mm))

    # 测量值表
    story.append(Paragraph("一、空间测量", styles["h2"]))
    measure_rows = [
        ["门宽", _fmt(measures.get("door_width_m"), " m")],
        ["通道净宽", _fmt(measures.get("passage_width_m"), " m")],
        ["门槛高度", _fmt(measures.get("threshold_m"), " m")],
        ["台阶", _fmt(measures.get("stairs_exist"))],
        ["地面坡度", _fmt(measures.get("slope"))],
        ["地面高差", _fmt(measures.get("uneven_m"), " m")],
        ["空间判定状态", measures.get("geometry_assessment_status", "—")],
    ]
    extent = measures.get("reconstruction_extent_m")
    if extent:
        measure_rows.append(["重建范围（长/宽/高）", " / ".join(f"{v:.2f}m" for v in extent)])
    measure_table = Table(
        [[Paragraph(k, styles["small"]), Paragraph(v, styles["body"])] for k, v in measure_rows],
        colWidths=[48 * mm, 120 * mm],
    )
    measure_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8DEE9")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F4F6FA")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]))
    story.append(measure_table)

    # 风险表
    story.append(Paragraph("二、风险项", styles["h2"]))
    header = ["风险项", "测量值", "等级"]
    risk_rows = [header]
    for risk in risks:
        level = risk.get("level", "unknown")
        measure = risk.get("measure")
        if isinstance(measure, list):
            measure_text = "、".join(item.get("label", str(item)) for item in measure) if measure else "无"
        else:
            measure_text = _fmt(measure, risk.get("unit", ""))
        risk_rows.append([
            RISK_NAMES.get(risk.get("code"), risk.get("name", "?")),
            measure_text,
            LEVEL_TEXT.get(level, level),
        ])
    risk_table = Table(
        [[Paragraph(c, styles["body"] if i else styles["small"]) for i, c in enumerate(row)]
         for row in risk_rows],
        colWidths=[48 * mm, 84 * mm, 36 * mm],
    )
    style_cmds = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8DEE9")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F4F6FA")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for row_index, risk in enumerate(risks, start=1):
        level = risk.get("level", "unknown")
        style_cmds.append(
            ("BACKGROUND", (2, row_index), (2, row_index), colors.HexColor(LEVEL_COLOR.get(level, "#8B98B5")))
        )
        style_cmds.append(("TEXTCOLOR", (2, row_index), (2, row_index), colors.white))
    risk_table.setStyle(TableStyle(style_cmds))
    story.append(risk_table)

    # 建议
    story.append(Paragraph("三、改造建议", styles["h2"]))
    if advice:
        for item in advice:
            story.append(Paragraph(f"• {item}", styles["body"]))
    else:
        story.append(Paragraph("本次评估未发现需要整改的高/中风险项。", styles["body"]))

    # 标注图
    valid_images = [p for p in images if Path(p).is_file()]
    if valid_images:
        story.append(PageBreak())
        story.append(Paragraph("四、三维标注图", styles["h2"]))
        from reportlab.lib.utils import ImageReader

        for path in valid_images[:6]:
            try:
                reader = ImageReader(path)
                iw, ih = reader.getSize()
                scale = min(160 * mm / iw, 100 * mm / ih, 1.0)
                story.append(Image(path, width=iw * scale, height=ih * scale))
                story.append(Spacer(1, 3 * mm))
            except Exception:  # noqa: BLE001 - 单张图损坏跳过
                continue

    doc.build(story)
    return str(out_path)
