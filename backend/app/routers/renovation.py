"""AI 安全改造助手 API：只读真实数据，模拟结果仅存在于响应内存。

三个核心入口：
1) /suggestions —— 现有装修建议列表（编号展示，含可达成预判）；
2) /simulate —— 自由改造（移动家具/放置物体）或勾选建议后的真实几何模拟评分；
3) /solve —— 针对风险指标搜索可达标且改动最小的编辑方案。
"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from assistant.intent_edits import intent_to_edits
from assistant.intent_parser import parse_intent
from pipeline.scheme_assessment import build_scheme, diff_assessments
from pipeline.scheme_solver import CONSTRUCTION_METRICS, _movable_objects, solve_metric

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import get_org_scope
from ..models import Scan

router = APIRouter()

_REACHABILITY_LABEL = {
    "movable": "可达成（移动家具）",
    "construction": "需施工（改门洞/换门）",
    "unknown": "无法达成",
}


class RenovationRequest(BaseModel):
    edits: list[dict] | None = None
    prompt: str | None = Field(default=None, min_length=1, max_length=300)


class SolveRequest(BaseModel):
    metric_codes: list[str] = Field(min_length=1, max_length=5)


class SuggestionRequest(BaseModel):
    pass


def _read_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


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
    data = _load_measurements(work)
    if data is None:
        raise HTTPException(409, "当前扫描缺少结构化测量数据，无法进行改造模拟")
    return data


def _reachability(measurements: dict, metric_code: str) -> str:
    if metric_code in CONSTRUCTION_METRICS:
        return "construction"
    return "movable" if _movable_objects(measurements) else "unknown"


def _scan_and_work(scan_id: int, db: Session, org_id: int, settings: Settings):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    work = (Path(settings.data_dir) / "work" / str(scan_id)).resolve()
    return scan, work


@router.get("/scans/{scan_id}/suggestions")
def list_suggestions(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    measurements, structure = _require_measurements(work), _load_structure(work)
    assessment = build_scheme(measurements, structure, [])
    suggestions = []
    for index, risk in enumerate(assessment.get("top_risks", []), 1):
        code = risk.get("metric_code")
        tier = _reachability(measurements, code)
        suggestions.append({
            "id": index,
            "name": risk.get("risk_name"),
            "level": risk.get("risk_level"),
            "advice": risk.get("advice"),
            "metric_code": code,
            "reachability": tier,
            "reachability_label": _REACHABILITY_LABEL[tier],
        })
    return {
        "scan_id": scan_id,
        "score": (assessment.get("overall") or {}).get("score"),
        "suggestions": suggestions,
    }


@router.post("/scans/{scan_id}/solve")
def solve_suggestions(
    scan_id: int,
    payload: SolveRequest,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    measurements = _require_measurements(work)
    structure = _load_structure(work)
    baseline = build_scheme(measurements, structure, [])
    known = {r["metric_code"] for r in baseline.get("risks", [])}
    unknown = [code for code in payload.metric_codes if code not in known]
    if unknown:
        raise HTTPException(422, f"未知的风险指标：{unknown}")
    return {"scan_id": scan_id,
            "baseline_score": (baseline.get("overall") or {}).get("score"),
            "results": [solve_metric(measurements, structure, code) for code in payload.metric_codes]}


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
