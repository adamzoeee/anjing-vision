"""AI 安全改造助手 API：只读真实数据，模拟结果仅存在于响应内存。"""
from __future__ import annotations

import json
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from assistant.intent_parser import parse_intent
from assistant.response_builder import build_response
from pipeline.renovation_compare import compare_assessments
from pipeline.renovation_simulator import simulate
from pipeline.risk_assessment import build_risk_assessment
from pipeline.spatial_assessment_inputs import build_spatial_assessment_inputs

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import get_org_scope
from ..models import Scan

router = APIRouter()


class RenovationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=300)


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


@router.post("/scans/{scan_id}/simulate")
def simulate_renovation(
    scan_id: int,
    payload: RenovationRequest,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    try:
        intent = parse_intent(payload.prompt)
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc
    work = (Path(settings.data_dir) / "work" / str(scan_id)).resolve()
    metrics = _load_metric_payload(work)
    before = build_risk_assessment(metrics)
    result = simulate(metrics, intent)
    comparison = compare_assessments(before, result["assessment"])
    return {
        "scan_id": scan_id,
        "intent": intent,
        "before": before,
        "after": result["assessment"],
        "comparison": comparison,
        "metric_changes": result["changes"],
        "message": build_response(intent, comparison, result["changes"]),
        "disclaimer": "仅为临时沙盒模拟，未修改真实房间、点云、结构数据或正式报告。",
    }
