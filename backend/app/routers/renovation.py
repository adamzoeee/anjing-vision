"""AI 安全改造助手 API：只读真实数据，模拟结果仅存在于响应内存。

两个核心入口：
1) /suggestions —— 现有装修建议列表（编号展示，供勾选模拟）；
2) /simulate —— 自由改造（移动家具/放置物体）或勾选建议后的真实几何模拟评分。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from assistant.intent_edits import intent_to_edits
from assistant.intent_parser import parse_intent
from pipeline.risk_assessment import build_risk_assessment
from pipeline.scheme_assessment import build_scheme, diff_assessments
from pipeline.spatial_assessment_inputs import build_spatial_assessment_inputs

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import get_org_scope
from ..models import Scan

router = APIRouter()


class RenovationRequest(BaseModel):
    edits: list[dict] | None = None
    prompt: str | None = Field(default=None, min_length=1, max_length=300)


class SuggestionRequest(BaseModel):
    pass


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _load_metric_payload(work: Path) -> dict:
    post = work / "postprocess"
    metric_path = post / "spatial_metrics.json"
    if metric_path.is_file():
        return _read_json(metric_path)
    required = [post / "measurements.json", post / "passage_analysis.json", post / "spatial_foundation.json"]
    if not all(path.is_file() for path in required):
        raise HTTPException(409, "当前扫描缺少结构化测量数据，无法进行改造模拟")
    return build_spatial_assessment_inputs(*(_read_json(path) for path in required))


def _load_structure(work: Path) -> dict:
    post = work / "postprocess"
    for name in ("structure_calibrated.json", "structure.json"):
        path = post / name
        if path.is_file():
            return _read_json(path)
    raise HTTPException(409, "当前扫描缺少空间结构数据，无法进行改造模拟")


def _load_measurements(work: Path) -> dict | None:
    path = work / "postprocess" / "measurements.json"
    return _read_json(path) if path.is_file() else None

def _require_measurements(work: Path) -> dict:
    path = work / "postprocess" / "measurements.json"
    if not path.is_file():
        raise HTTPException(409, "当前扫描缺少结构化测量数据，无法进行改造模拟")
    return _read_json(path)


def _scan_and_work(scan_id: int, db: Session, org_id: int, settings: Settings):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    work = (Path(settings.data_dir) / "work" / str(scan_id)).resolve()
    return scan, work


def _build_before(work: Path):
    metrics = _load_metric_payload(work)
    return metrics, build_risk_assessment(metrics)


@router.get("/scans/{scan_id}/suggestions")
def list_suggestions(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    _, assessment = _build_before(work)
    suggestions = []
    for index, risk in enumerate(assessment.get("top_risks", []), 1):
        suggestions.append({
            "id": index,
            "name": risk.get("risk_name"),
            "level": risk.get("risk_level"),
            "advice": risk.get("advice"),
            "metric_code": risk.get("metric_code"),
        })
    return {
        "scan_id": scan_id,
        "score": (assessment.get("overall") or {}).get("score"),
        "suggestions": suggestions,
    }


@router.post("/scans/{scan_id}/simulate")
def simulate_renovation(
    scan_id: int,
    payload: RenovationRequest,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    measurements = _require_measurements(work)
    structure = _load_structure(work)

    if payload.edits is not None:
        edits = payload.edits
    elif payload.prompt:
        try:
            edits = intent_to_edits(parse_intent(payload.prompt), measurements, structure)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    else:
        raise HTTPException(422, "请提供改造指令或编辑列表")

    baseline = build_scheme(measurements, structure, [])
    result = build_scheme(measurements, structure, edits)
    diff = diff_assessments(baseline, result)
    applied = result["applied_edits"]
    return {
        "scan_id": scan_id,
        "edits": edits,
        "applied_edits": applied,
        "conflicts": result["conflicts"],
        "assumed_ids": result["assumed_ids"],
        "layout_before": result["layout_before"],
        "layout_after": result["layout_after"],
        "changed_ids": sorted({e["target"] for e in applied if e.get("target")}),
        "removed_ids": sorted({e["target"] for e in applied if e.get("op") == "remove"}),
        "metrics": result["metrics"],
        "risks": result["risks"],
        "overall": result["overall"],
        "category_scores": result["category_scores"],
        **diff,
        "scope": result["scope"],
        "disclaimer": "仅为内存沙盒模拟：未修改真实房间、点云、结构数据或正式报告；"
                      "分数由正式评估链路在改后布局上重算。",
    }
