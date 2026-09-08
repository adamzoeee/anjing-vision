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

LEVEL_COLOR = {
    "high": "#E5484D", "medium": "#F5B301", "low": "#2E9E5B",
    "red": "#E5484D", "yellow": "#F5B301", "green": "#2E9E5B",
    "unknown": "#8B98B5", None: "#8B98B5",
}
LEVEL_TEXT = {
    "high": "高风险", "medium": "中风险", "low": "低风险",
    "red": "高风险", "yellow": "中风险", "green": "正常",
    "unknown": "未评估", None: "未评估",
}

CATEGORY_TEXT = {
    "mobility": "通行能力",
    "layout": "空间布局",
    "usage_safety": "使用安全",
}

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

STATUS_TEXT = {
    "completed": "已完成",
    "applied": "已应用",
    "metric_references": "米制（已标定）",
    "relative": "相对尺度",
    "not_started": "未开始",
    "not_available": "暂无数据",
}

CALIBRATION_METHOD_TEXT = {
    "trusted_bed_anchor_with_reference_consistency_audit": "参考物一致性校验",
    "reference": "参考尺寸标定",
    "apriltag": "视觉标记标定",
}


def _humanize_status(value) -> str:
    if value in (None, "", "—"):
        return "—"
    return STATUS_TEXT.get(str(value), str(value))


def _humanize_calibration_method(value) -> str:
    if value in (None, "", "—"):
        return "未提供"
    return CALIBRATION_METHOD_TEXT.get(str(value), "参考尺寸标定")


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
        "score_value": make("scv", 34, leading=38, alignment=1),
        "score_meta": make("scm", 10.5, leading=18, textColor="#35405A"),
    }


def _score_color(score: float | None) -> str:
    if score is None:
        return LEVEL_COLOR["unknown"]
    if score >= 80:
        return LEVEL_COLOR["green"]
    if score >= 60:
        return LEVEL_COLOR["yellow"]
    return LEVEL_COLOR["red"]


def _build_score_card(
    *,
    score: float | None,
    completeness: float | None,
    scale_text: str,
    calibration_method: str,
    styles: dict,
) -> Table:
    """Build a compact cover score card without inheriting the score font size."""
    score_text = f"{score:.1f}" if score is not None else "无法评分"
    completeness_text = (
        f"{float(completeness):.1f}%"
        if isinstance(completeness, (int, float))
        else "—"
    )
    method_text = calibration_method if calibration_method not in (None, "", "—") else "未提供"
    score_block = Paragraph(
        f'<font color="{_score_color(score)}"><b>{score_text}</b></font>'
        '<br/><font size="9" color="#5A6478">综合安全评分</font>',
        styles["score_value"],
    )
    meta_block = Paragraph(
        f'<b>评估完整度</b>　{completeness_text}<br/>'
        f'<b>尺度标定</b>　{scale_text}<br/>'
        f'<font size="9" color="#7A859D">标定方法：{method_text}</font>',
        styles["score_meta"],
    )
    card = Table([[score_block, meta_block]], colWidths=[58 * mm, 110 * mm])
    card.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, -1), colors.HexColor("#F5F8F7")),
        ("BOX", (0, 0), (-1, -1), 0.6, colors.HexColor("#DCE5E2")),
        ("LINEAFTER", (0, 0), (0, 0), 0.6, colors.HexColor("#DCE5E2")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("LEFTPADDING", (0, 0), (-1, -1), 12),
        ("RIGHTPADDING", (0, 0), (-1, -1), 12),
        ("TOPPADDING", (0, 0), (-1, -1), 10),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 10),
    ]))
    return card


def _fmt(value, unit: str = "") -> str:
    if value is None:
        return "—"
    if isinstance(value, bool):
        return "是" if value else "否"
    if isinstance(value, (int, float)):
        return f"{value:.2f}{unit}"
    return str(value)


def _formal_summary_rows(assessment: dict) -> list[list[str]]:
    """Build stable PDF rows for the official category score summary."""
    rows = [["评估维度", "得分", "权重", "覆盖情况"]]
    for code in ("mobility", "layout", "usage_safety"):
        category = (assessment.get("category_scores") or {}).get(code) or {}
        score = category.get("score")
        weight = category.get("weight")
        rows.append([
            CATEGORY_TEXT[code],
            f"{score:.1f}" if isinstance(score, (int, float)) else "无法评分",
            f"{float(weight) * 100:.0f}%" if isinstance(weight, (int, float)) else "—",
            f"{category.get('evaluated_count', 0)}/{category.get('total_count', 0)}",
        ])
    return rows


def _formal_metric_rows(assessment: dict) -> list[list[str]]:
    """Format backend-owned key metrics without recalculating any value."""
    rows = [["关键指标", "测量值", "证据状态"]]
    for metric in assessment.get("key_metrics") or []:
        status = metric.get("status", "not_evaluable")
        value = (
            _fmt(metric.get("value"), metric.get("unit", ""))
            if status != "not_evaluable" else "—"
        )
        rows.append([
            metric.get("name") or metric.get("metric_code") or "未命名指标",
            value,
            "已评估" if status != "not_evaluable" else "当前空间数据不足，暂无法评估。",
        ])
    return rows


def _formal_not_evaluable_rows(assessment: dict) -> list[list[str]]:
    """Expose missing evidence explicitly instead of treating it as safe."""
    return [
        [
            item.get("risk_name") or item.get("metric_code") or "未命名指标",
            "当前空间数据不足，暂无法评估。",
        ]
        for item in (assessment.get("not_evaluable") or [])
    ]


def _visible_risks(risks: list[dict]) -> list[dict]:
    """Match the report page: only high and medium risks are shown."""
    return [
        risk for risk in risks
        if risk.get("risk_level", risk.get("level")) in {"high", "medium", "red", "yellow"}
    ]


def _report_measurement_rows(measurements: dict) -> list[list[str]]:
    """Build the same concise measurement summary used by the report page."""
    rows: list[list[str]] = []
    room = measurements.get("room") or {}
    if all(isinstance(room.get(key), (int, float)) for key in ("length_m", "width_m", "height_m")):
        rows.append([
            "房间尺寸",
            "长 {:.2f}m × 宽 {:.2f}m × 高 {:.2f}m".format(
                room["length_m"], room["width_m"], room["height_m"]
            ),
        ])
    door = next(
        (item for item in (measurements.get("openings") or []) if item.get("type") == "door"),
        None,
    )
    if door and isinstance(door.get("width_m"), (int, float)):
        height = door.get("height_m")
        rows.append([
            "门洞净尺寸",
            f"宽 {door['width_m']:.2f}m"
            + (f" × 高 {height:.2f}m" if isinstance(height, (int, float)) else ""),
        ])
    passage = measurements.get("passage") or {}
    if passage.get("status") == "ok":
        parts = []
        if isinstance(passage.get("passage_width_m"), (int, float)):
            parts.append(f"最窄通道 {passage['passage_width_m']:.2f}m")
        if isinstance(passage.get("path_length_m"), (int, float)):
            parts.append(f"门→床路径 {passage['path_length_m']:.2f}m")
        if parts:
            rows.append(["通道与可行走", " · ".join(parts)])
    scale = measurements.get("scale") or {}
    if scale.get("status") == "metric_references":
        rows.append([
            "真实尺寸标定",
            f"成功（比例系数 {float(scale.get('scale', 0)):.3f}）",
        ])
    else:
        rows.append(["真实尺寸标定", "未完成，结果仅供参考"])
    return rows


def _metric_value_text(metric: dict) -> str:
    if metric.get("status") == "not_evaluable":
        return "当前空间数据不足，暂无法评估。"
    value = metric.get("value")
    if metric.get("metric_code") == "crowding" and isinstance(value, (int, float)):
        position = metric.get("position") or {}
        details = ""
        if isinstance(position.get("furniture_area_m2"), (int, float)) and isinstance(
            position.get("room_area_m2"), (int, float)
        ):
            details = (
                f"（家具占地 {position['furniture_area_m2']:.2f}㎡ / "
                f"房间有效面积 {position['room_area_m2']:.2f}㎡）"
            )
        return f"{value * 100:.1f}%{details}"
    return _fmt(value, f" {metric.get('unit', '')}" if metric.get("unit") else "")


def _furniture_rows(measurements: dict) -> list[list[str]]:
    names = {
        "bed": "床", "wardrobe": "衣柜", "sofa": "沙发", "desk": "书桌",
        "table": "桌子", "cabinet": "柜子", "bookshelf": "书架", "chair": "椅子",
        "stool": "凳子", "small_table": "小桌", "curtain": "窗帘",
        "storage_rack": "小收纳架", "clothes_rack": "衣物架",
    }
    confidence_text = {"high": "高", "medium": "中", "low": "低"}
    counts: dict[str, int] = {}
    rows: list[list[str]] = []
    for item in measurements.get("objects") or []:
        object_type = str(item.get("type") or item.get("label") or "物品").rstrip("_")
        counts[object_type] = counts.get(object_type, 0) + 1
        suffix = str(counts[object_type]) if counts[object_type] > 1 else ""
        dimensions = "长 {}m × 宽 {}m × 高 {}m".format(
            _fmt(item.get("length_m")), _fmt(item.get("width_m")), _fmt(item.get("height_m"))
        )
        rows.append([
            f"{names.get(object_type, object_type)}{suffix}",
            dimensions,
            confidence_text.get(str(item.get("confidence")), "低"),
        ])
    return rows


def build_pdf_report(
    *,
    title: str,
    score: float | None,
    risks: list[dict],
    measures: dict,
    advice: list[str],
    images: list[str],
    out_path: str | Path,
    risk_assessment: dict | None = None,
    structure_plan_path: str | Path | None = None,
    passage_plan_path: str | Path | None = None,
) -> str:
    """生成 PDF 报告，返回输出路径。

    risks: rules.evaluate_risks 输出（含 code/name/level/measure/unit）。
    measures: 管道 measures dict。
    images: 标注图 PNG 路径列表（嵌入报告）。
    """
    if risk_assessment and risk_assessment.get("official") is True:
        score = (risk_assessment.get("overall") or {}).get("score")
        risks = list(risk_assessment.get("risks") or [])
        advice = list(risk_assessment.get("advice") or [])
        measures = {**measures, "risk_assessment": risk_assessment}
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
    formal = measures.get("risk_assessment") or {}
    formal_overall = formal.get("overall") or {}
    completeness = formal_overall.get("coverage_percent")
    if completeness is None:
        completeness = (measures.get("assessment_completeness") or {}).get("percent")
    calibration = measures.get("calibration_quality") or {}
    calib_method = _humanize_calibration_method(calibration.get("method"))
    scale_status = measures.get("scale_status", "relative")
    scale_text = "米制（已标定）" if scale_status == "metric_references" else "相对尺度"
    story.append(_build_score_card(
        score=score,
        completeness=completeness,
        scale_text=scale_text,
        calibration_method=calib_method,
        styles=styles,
    ))
    story.append(Spacer(1, 4 * mm))

    confidence = measures.get("confidence_summary") or {}
    formal_confidence = formal.get("confidence") or {}
    measurement_coverage = (
        formal_confidence.get("metric_coverage")
        or confidence.get("measurement_coverage")
        or measures.get("measurement_coverage")
        or {}
    )
    risk_coverage = (
        formal_confidence.get("assessment_coverage")
        or confidence.get("risk_assessment_coverage")
        or measures.get("risk_assessment_coverage")
        or {}
    )
    story.append(Paragraph(
        "重建状态：{}　语义状态：{}　尺度状态：{}　可靠测量：{}/{}　正式风险评估：{}/{}".format(
            _humanize_status(confidence.get("reconstruction_status")),
            _humanize_status(confidence.get("semantic_status")),
            _humanize_status(confidence.get("scale_status", scale_status)),
            measurement_coverage.get("verified_count", measurement_coverage.get("evaluable_count", 0)),
            measurement_coverage.get("total_count", 0), risk_coverage.get("evaluated_count", 0),
            risk_coverage.get("total_count", 0),
        ), styles["small"],
    ))

    if formal:
        story.append(Paragraph("正式评估维度", styles["h2"]))
        summary_table = Table(
            [[Paragraph(str(cell), styles["small"]) for cell in row]
             for row in _formal_summary_rows(formal)],
            colWidths=[55 * mm, 35 * mm, 35 * mm, 43 * mm],
        )
        summary_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8DEE9")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F4F6FA")),
            ("ALIGN", (1, 1), (-1, -1), "CENTER"),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(summary_table)
        evidence_confidence = formal_confidence.get("evidence_confidence")
        adjusted_confidence = formal_confidence.get("coverage_adjusted_confidence")
        story.append(Paragraph(
            "证据置信度：{}　覆盖修正置信度：{}".format(
                f"{evidence_confidence * 100:.1f}%" if isinstance(evidence_confidence, (int, float)) else "—",
                f"{adjusted_confidence * 100:.1f}%" if isinstance(adjusted_confidence, (int, float)) else "—",
            ),
            styles["small"],
        ))

    # 与网页评估报告一致：关键空间指标 → 2D 图 → 尺寸/家具 → 风险与建议。
    visible_metric_codes = {
        "main_passage_width", "minimum_passage_width", "door_width", "entrance_space",
        "bedside_clearance", "activity_area", "crowding",
    }
    metric_rows = [["类别", "关键空间指标", "测量值"]]
    for metric in formal.get("key_metrics") or []:
        if metric.get("metric_code") not in visible_metric_codes:
            continue
        metric_rows.append([
            CATEGORY_TEXT.get(metric.get("category"), "空间指标"),
            metric.get("name") or metric.get("metric_code"),
            _metric_value_text(metric),
        ])
    story.append(Paragraph("一、关键空间指标", styles["h2"]))
    metric_table = Table(
        [[Paragraph(str(cell), styles["small"]) for cell in row] for row in metric_rows],
        colWidths=[32 * mm, 50 * mm, 86 * mm],
    )
    metric_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8DEE9")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F4F6FA")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(metric_table)

    plan_sections = [
        ("二、2D 结构图（按测量结果绘制）", structure_plan_path),
        ("三、通行图（门→床路径与通道净宽）", passage_plan_path),
    ]
    from reportlab.lib.utils import ImageReader
    for heading, path in plan_sections:
        if not path or not Path(path).is_file():
            continue
        story.append(PageBreak())
        story.append(Paragraph(heading, styles["h2"]))
        reader = ImageReader(path)
        iw, ih = reader.getSize()
        scale = min(168 * mm / iw, 225 * mm / ih)
        story.append(Image(str(path), width=iw * scale, height=ih * scale))

    measurements = measures.get("measurements") or measures
    story.append(PageBreak())
    story.append(Paragraph("四、空间与参考尺寸", styles["h2"]))
    measurement_rows = _report_measurement_rows(measurements)
    measurement_table = Table(
        [[Paragraph(str(cell), styles["small"]) for cell in row] for row in measurement_rows],
        colWidths=[45 * mm, 123 * mm],
    )
    measurement_table.setStyle(TableStyle([
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8DEE9")),
        ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#F4F6FA")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    story.append(measurement_table)

    furniture_rows = _furniture_rows(measurements)
    if furniture_rows:
        story.append(Paragraph("五、家具详情", styles["h2"]))
        furniture_table = Table(
            [[Paragraph(str(cell), styles["small"]) for cell in row]
             for row in [["家具", "参考尺寸", "置信度"], *furniture_rows]],
            colWidths=[35 * mm, 103 * mm, 30 * mm],
            repeatRows=1,
        )
        furniture_table.setStyle(TableStyle([
            ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8DEE9")),
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F4F6FA")),
            ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
            ("TOPPADDING", (0, 0), (-1, -1), 4),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ]))
        story.append(furniture_table)

    unavailable_rows = _formal_not_evaluable_rows(formal)
    if unavailable_rows:
        story.append(Paragraph("证据不足项", styles["h2"]))
        for name, reason in unavailable_rows:
            story.append(Paragraph(f"• {name}：{reason}", styles["body"]))

    # 风险表（与网页一致，不展示低风险项）
    risks = _visible_risks(risks)
    story.append(Paragraph(f"六、风险项（{len(risks)}）", styles["h2"]))
    header = ["风险项", "测量值", "等级", "改造建议"]
    risk_rows = [header]
    for risk in risks:
        level = risk.get("risk_level", risk.get("level", "unknown"))
        measure = risk.get("measured_value", risk.get("measure"))
        if isinstance(measure, list):
            measure_text = "、".join(item.get("label", str(item)) for item in measure) if measure else "无"
        else:
            measure_text = _fmt(measure, risk.get("unit", ""))
        if risk.get("assessment_status") == "not_evaluable":
            measure_text = "当前空间数据不足，暂无法评估。"
        risk_rows.append([
            risk.get("risk_name") or RISK_NAMES.get(risk.get("code"), risk.get("name", "?")),
            measure_text,
            LEVEL_TEXT.get(level, level),
            risk.get("advice") or "—",
        ])
    risk_table = Table(
        [[Paragraph(c, styles["body"] if i else styles["small"]) for i, c in enumerate(row)]
         for row in risk_rows],
        colWidths=[43 * mm, 28 * mm, 22 * mm, 75 * mm],
    )
    style_cmds = [
        ("GRID", (0, 0), (-1, -1), 0.4, colors.HexColor("#D8DEE9")),
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#F4F6FA")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
    ]
    for row_index, risk in enumerate(risks, start=1):
        level = risk.get("risk_level", risk.get("level", "unknown"))
        style_cmds.append(
            ("BACKGROUND", (2, row_index), (2, row_index), colors.HexColor(LEVEL_COLOR.get(level, "#8B98B5")))
        )
        style_cmds.append(("TEXTCOLOR", (2, row_index), (2, row_index), colors.white))
    risk_table.setStyle(TableStyle(style_cmds))
    story.append(risk_table)

    # 建议
    story.append(Paragraph("七、改造建议", styles["h2"]))
    if advice:
        for item in advice:
            story.append(Paragraph(f"• {item}", styles["body"]))
    else:
        story.append(Paragraph("本次评估未发现需要整改的高/中风险项。", styles["body"]))

    doc.build(story)
    return str(out_path)
