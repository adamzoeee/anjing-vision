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

from assistant.intent_parser import parse_intent
from assistant.response_builder import build_response
from pipeline.renovation_simulator import simulate
from pipeline.risk_assessment import build_risk_assessment
from pipeline.spatial_assessment_inputs import build_spatial_assessment_inputs

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import get_org_scope
from ..models import Scan

router = APIRouter()


class RenovationRequest(BaseModel):
    prompt: str | None = Field(default=None, min_length=1, max_length=300)
    suggestion_ids: list[int] | None = Field(default=None)


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
    if payload.suggestion_ids:
        intent = {"action": "APPLY_SUGGESTIONS", "suggestion_ids": payload.suggestion_ids,
                  "raw": f"建议{payload.suggestion_ids}"}
    elif payload.prompt:
        try:
            intent = parse_intent(payload.prompt)
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc
    else:
        raise HTTPException(422, "请提供改造指令或勾选建议")
    structure = _load_structure(work)
    measurements = _load_measurements(work)
    metric_payload = _load_metric_payload(work)
    result = simulate(metric_payload, intent, structure, measurements)
    message = build_response(intent, result["comparison"], result["metric_changes"],
                             result["risk_changes"])
    return {
        "scan_id": scan_id,
        "intent": intent,
        "comparison": result["comparison"],
        "metric_changes": result["metric_changes"],
        "risk_changes": result["risk_changes"],
        "qualitative": result["qualitative"],
        "message": message,
        "disclaimer": "仅为内存沙盒模拟：未修改真实房间、点云、结构数据或正式报告；"
                      "评分由正式风险体系根据重新计算的空间指标得出。",
    }
