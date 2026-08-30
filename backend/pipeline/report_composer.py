"""报告合成器（第四阶段集成层）：风险 + 测量 → 3D 标注图 + PDF 报告。

把 risk_visualization（3D 标注渲染）与 pdf_report（PDF 生成）组合成一次调用，
供 pipeline_runner 的 reporting 阶段使用（接线在管道稳定后完成）。

本模块只 import 两个渲染/生成模块（均无 GPU 依赖），自身不 import 任何
业务管道模块，因此可以安全地在管道运行期间单独测试。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path

import numpy as np

from pipeline.risk_visualization import RiskGeometry, render_risk_annotations


@dataclass
class ComposedReport:
    """合成报告产物。"""
    pdf_path: str | None = None
    risk_images: list[str] = field(default_factory=list)
    risk_geometries: list[RiskGeometry] = field(default_factory=list)
    status: str = "ok"  # ok | partial | skipped
    reason: str = ""


def build_risk_geometries(
    risks: list[dict],
    measures: dict,
    semantic_objects: dict | None = None,
) -> list[RiskGeometry]:
    """风险列表 + 测量上下文 → 3D 风险标注几何。

    几何来源：
    - passage_width：measures 的 narrowest_point 附近画测量线段（缺省在原点附近，
      标注"相对尺度下无真实位置"时仍给出可视化占位）；
    - threshold：门槛高度箭头（从地面到门槛高度）；
    - obstacle：障碍物列表里带点云的条目 → OBB 红框（semantic_objects 提供）；
    - stairs：台阶边界折线（若有 stairs 信息）。
    """
    geometries: list[RiskGeometry] = []
    for risk in risks:
        code = risk.get("code")
        level = risk.get("level", "unknown")
        if code == "passage_width" and level in ("red", "yellow"):
            width = measures.get("passage_width_m")
            if width is not None:
                half = width / 2.0
                point = measures.get("narrowest_point") or [0.0, 0.0, 0.02]
                x, y, z = float(point[0]), float(point[1]), float(point[2])
                geometries.append(RiskGeometry(
                    kind="segment", label=f"通道净宽 {width:.2f}m", level=level,
                    params={"p1": [x - half, y, z], "p2": [x + half, y, z]},
                ))
        elif code == "threshold" and level in ("red", "yellow"):
            height = measures.get("threshold_m")
            if height is not None and height > 0:
                geometries.append(RiskGeometry(
                    kind="arrow", label=f"门槛 {height:.2f}m", level=level,
                    params={"p1": [0.0, 0.0, 0.0], "p2": [0.0, 0.0, float(height)]},
                ))
        elif code == "obstacle":
            measure = risk.get("measure") or []
            if not isinstance(measure, list):
                continue
            for item in measure:
                label = item.get("label", "障碍物")
                obb = item.get("obb") if isinstance(item, dict) else None
                if obb and semantic_objects:
                    obj = semantic_objects.get(label)
                    if obj and isinstance(obj, dict):
                        center = obj.get("center")
                        axes = obj.get("axes")
                        extents = obj.get("extents")
                        if center and axes and extents:
                            geometries.append(RiskGeometry(
                                kind="box", label=label, level=level,
                                params={
                                    "center": center,
                                    "axes": np.asarray(axes),
                                    "extents": np.asarray(extents),
                                },
                            ))
    return geometries


def compose_report(
    *,
    title: str,
    score: float | None,
    risks: list[dict],
    measures: dict,
    advice: list[str],
    points: np.ndarray | None,
    out_dir: str | Path,
    colors: np.ndarray | None = None,
    semantic_objects: dict | None = None,
    n_views: int = 3,
    risk_assessment: dict | None = None,
) -> ComposedReport:
    """一次生成：3D 风险标注图 + PDF 报告。

    points 为 None 时跳过 3D 标注图（仅 PDF）；PDF 始终生成。
    渲染失败不抛异常（partial 状态），保证报告流程健壮。
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    composed = ComposedReport()

    if risk_assessment and risk_assessment.get("official") is True:
        score = (risk_assessment.get("overall") or {}).get("score")
        risks = list(risk_assessment.get("risks") or [])
        advice = list(risk_assessment.get("advice") or [])
    geometries = build_risk_geometries(risks, measures, semantic_objects)
    composed.risk_geometries = geometries

    risk_images: list[str] = []
    if points is not None and len(np.asarray(points).reshape(-1, 3)) >= 10:
        try:
            risk_images = render_risk_annotations(
                np.asarray(points), geometries, out_dir / "risk_views",
                colors=colors, n_views=n_views,
            )
        except Exception as exc:  # noqa: BLE001 - 渲染环境缺失时降级
            composed.status = "partial"
            composed.reason = f"3D 标注渲染失败: {exc}"
    composed.risk_images = risk_images

    try:
        # 惰性导入：reportlab 为可选依赖，未安装时降级为无 PDF 报告
        from pipeline.pdf_report import build_pdf_report

        pdf_path = build_pdf_report(
            title=title,
            score=score,
            risks=risks,
            measures=measures,
            advice=advice,
            images=risk_images,
            out_path=out_dir / "report.pdf",
            risk_assessment=risk_assessment,
        )
        composed.pdf_path = pdf_path
    except ImportError as exc:
        composed.status = "partial"
        composed.reason = (composed.reason + "；" if composed.reason else "") + f"reportlab 未安装，跳过 PDF: {exc}"
    except Exception as exc:  # noqa: BLE001 - PDF 失败时报告 partial
        composed.status = "partial"
        composed.reason = (composed.reason + "；" if composed.reason else "") + f"PDF 生成失败: {exc}"

    return composed
