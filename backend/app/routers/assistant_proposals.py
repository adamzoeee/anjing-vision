"""Single-room AI proposal generation with org isolation and read-only inputs."""
from functools import partial

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from assistant.deepseek_client import DeepSeekInvalidResponse, DeepSeekUnavailable, request_candidates
from assistant.proposal_engine import generate_room_proposals

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import get_org_scope
from .renovation import _input_fingerprints, _load_structure, _require_measurements, _scan_and_work

router = APIRouter()


class ProposalRequest(BaseModel):
    goal: str | None = Field(default=None, max_length=300)


@router.post("/scans/{scan_id}/proposals")
def room_proposals(
    scan_id: int,
    payload: ProposalRequest,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
    settings: Settings = Depends(get_settings),
):
    scan, work = _scan_and_work(scan_id, db, org_id, settings)
    if scan.capture_type not in {"video", "photos"}:
        raise HTTPException(409, "小助手仅支持单房间扫描，整屋扫描暂不可用")
    if scan.status != "done":
        raise HTTPException(409, "请等待房间扫描完成后生成方案")
    try:
        fingerprints = _input_fingerprints(work)
        measurements, structure = _require_measurements(work), _load_structure(work)
        if not isinstance(measurements, dict) or not isinstance(structure, dict):
            raise ValueError("Invalid structured input")
    except (OSError, ValueError, TypeError):
        raise HTTPException(409, "房间结构化资料无法读取，请重新处理扫描") from None
    if not settings.deepseek_api_key:
        raise HTTPException(503, "AI 方案服务尚未配置，请联系管理员")
    try:
        result = generate_room_proposals(
            measurements, structure, payload.goal,
            partial(request_candidates, api_key=settings.deepseek_api_key,
                    model=settings.deepseek_model, timeout_s=settings.deepseek_timeout_s),
        )
    except DeepSeekUnavailable:
        raise HTTPException(503, "AI 方案服务暂时不可用，请稍后重试") from None
    except DeepSeekInvalidResponse:
        raise HTTPException(502, "AI 方案服务返回无效数据，请重试") from None
    except (ValueError, TypeError, KeyError, IndexError, OverflowError):
        raise HTTPException(409, "房间结构化资料不足以进行可信验算，请补充测量后重试") from None
    if _input_fingerprints(work) != fingerprints:
        raise HTTPException(409, "房间资料已更新，请重新生成方案")
    body = {"scan_id": scan_id, "input_fingerprints": fingerprints, **result}
    if result["status"] == "insufficient":
        return JSONResponse(status_code=422, content={**body, "error": {
            "code": "insufficient_proposals", "message": result["message"]}})
    return body
