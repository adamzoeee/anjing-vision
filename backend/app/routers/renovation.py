"""AI 安全改造助手 API：只读真实数据，模拟结果仅存在于响应内存。

三个核心入口：
1) /suggestions —— 现有装修建议列表（编号展示，含可达成预判）；
2) /simulate —— 自由改造（移动家具/放置物体）或勾选建议后的真实几何模拟评分；
3) /solve —— 针对风险指标搜索可达标且改动最小的编辑方案。
"""
from __future__ import annotations

import hashlib
import io
import json
from pathlib import Path
from urllib.parse import quote
from uuid import uuid4

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from assistant.intent_edits import intent_to_edits
from assistant.intent_parser import parse_intent
from pipeline.scheme_assessment import build_scheme, diff_assessments
from pipeline.scheme_pdf import build_scheme_appendix_pdf
from pipeline.scheme_solver import CONSTRUCTION_METRICS, _movable_objects, solve_metric
from pipeline.scheme_state import apply_edits
from pipeline.space_foundation import analyze_structure_passages, render_passage_analysis
from pipeline.structure_figure import render_structure_plan_to

from ..config import Settings, get_settings
from ..db import get_db
from ..deps import get_org_scope, require_admin
from ..models import RenovationScheme, Scan, User

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


def _input_fingerprints(work: Path) -> dict:
    out = {}
    for name in ("measurements.json", "structure_calibrated.json", "structure.json"):
        path = work / "postprocess" / name
        if path.is_file():
            out[name.replace(".json", "_sha256")] = hashlib.sha256(path.read_bytes()).hexdigest()
    return out


def _based_on_status(scheme: RenovationScheme, work: Path) -> dict:
    """比较方案保存时的输入指纹与当前指纹，客户端据此提示「基于旧数据」。"""
    current = _input_fingerprints(work)
    saved = scheme.based_on or {}
    keys = sorted(set(saved) | set(current))
    mismatched = [key for key in keys if saved.get(key) != current.get(key)]
    return {
        "stale": bool(mismatched),
        "based_on_mismatch": bool(mismatched),
        "mismatched_keys": mismatched,
    }


def _scheme_payload(scan_id: int, measurements: dict, structure: dict, edits: list[dict]) -> dict:
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
        "disclaimer": "沙盒模拟 · 非实测：未修改真实房间、点云、结构数据或正式报告；"
                      "分数由正式评估链路在改后布局上重算。",
    }


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
        "scope": assessment["scope"],
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
    risk_by_code = {r["metric_code"]: r for r in baseline.get("risks", [])}
    unknown = [code for code in payload.metric_codes if code not in risk_by_code]
    if unknown:
        raise HTTPException(422, f"未知的风险指标：{unknown}")
    not_evaluable = [code for code in payload.metric_codes
                     if risk_by_code[code].get("assessment_status") != "evaluated"]
    if not_evaluable:
        raise HTTPException(422, f"以下指标当前不可评估，无法给出达标方案：{not_evaluable}")
    return {"scan_id": scan_id,
            "baseline_score": (baseline.get("overall") or {}).get("score"),
            "scope": baseline["scope"],
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

    return _scheme_payload(scan_id, measurements, structure, edits)


class SchemeCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    edits: list[dict] = Field(default_factory=list)
    source: str = Field(default="manual", max_length=20)


class SchemePatchRequest(BaseModel):
    name: str = Field(min_length=1, max_length=120)


def _get_scheme(db: Session, scan_id: int, scheme_id: int) -> RenovationScheme:
    scheme = db.get(RenovationScheme, scheme_id)
    if scheme is None or scheme.scan_id != scan_id:
        raise HTTPException(404, "方案不存在")
    return scheme


@router.get("/scans/{scan_id}/schemes")
def list_schemes(scan_id: int, db: Session = Depends(get_db), org_id: int = Depends(get_org_scope),
                 settings: Settings = Depends(get_settings)):
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    measurements, structure = _require_measurements(work), _load_structure(work)
    baseline_score = (build_scheme(measurements, structure, []).get("overall") or {}).get("score")
    schemes = (db.query(RenovationScheme)
               .filter(RenovationScheme.scan_id == scan_id)
               .order_by(RenovationScheme.id).all())
    items = []
    for scheme in schemes:
        result = build_scheme(measurements, structure, scheme.edits or [])
        items.append({
            "id": scheme.id, "name": scheme.name, "edits": scheme.edits, "source": scheme.source,
            "created_at": scheme.created_at.isoformat() if scheme.created_at else None,
            "score": (result.get("overall") or {}).get("score"),
            "baseline_score": baseline_score,
            "conflicts": result["conflicts"],
            **_based_on_status(scheme, work),
        })
    return {"scan_id": scan_id, "baseline_score": baseline_score, "schemes": items}


@router.post("/scans/{scan_id}/schemes")
def create_scheme(scan_id: int, payload: SchemeCreateRequest, db: Session = Depends(get_db),
                  admin: User = Depends(require_admin), settings: Settings = Depends(get_settings)):
    _, work = _scan_and_work(scan_id, db, admin.org_id, settings)
    measurements, structure = _require_measurements(work), _load_structure(work)
    validation = build_scheme(measurements, structure, payload.edits)
    if validation["conflicts"]:
        raise HTTPException(422, f"方案包含无效编辑：{validation['conflicts'][0]}")
    scheme = RenovationScheme(scan_id=scan_id, name=payload.name, edits=payload.edits,
                              source=payload.source, based_on=_input_fingerprints(work))
    db.add(scheme)
    db.commit()
    db.refresh(scheme)
    return {"id": scheme.id, "name": scheme.name, "edits": scheme.edits,
            "score": (validation.get("overall") or {}).get("score")}


@router.get("/scans/{scan_id}/schemes/{scheme_id}")
def get_scheme(scan_id: int, scheme_id: int, db: Session = Depends(get_db),
               org_id: int = Depends(get_org_scope), settings: Settings = Depends(get_settings)):
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    scheme = _get_scheme(db, scan_id, scheme_id)
    measurements, structure = _require_measurements(work), _load_structure(work)
    payload = _scheme_payload(scan_id, measurements, structure, scheme.edits or [])
    return {**payload, "score": (payload["overall"] or {}).get("score"),
            **_based_on_status(scheme, work),
            "plan": {"id": scheme.id, "name": scheme.name, "edits": scheme.edits,
                     "source": scheme.source, "based_on": scheme.based_on}}


def _scheme_derived(work: Path, scheme: RenovationScheme) -> tuple[dict, dict]:
    """方案派生状态：真实产物只读，编辑全部在内存副本上执行。"""
    measurements, structure = _require_measurements(work), _load_structure(work)
    state = apply_edits(measurements, structure, scheme.edits or [])
    return state["measurements"], state["structure"]


@router.get("/scans/{scan_id}/schemes/{scheme_id}/plan.png")
def scheme_plan_png(scan_id: int, scheme_id: int, db: Session = Depends(get_db),
                    org_id: int = Depends(get_org_scope), settings: Settings = Depends(get_settings)):
    """改造后的 2D 平面图：与报告同一渲染器，按方案派生状态出图（不落盘）。"""
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    scheme = _get_scheme(db, scan_id, scheme_id)
    measurements, structure = _scheme_derived(work, scheme)
    buffer = io.BytesIO()
    render_structure_plan_to(measurements, structure, buffer)
    return Response(content=buffer.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.get("/scans/{scan_id}/schemes/{scheme_id}/passage.png")
def scheme_passage_png(scan_id: int, scheme_id: int, db: Session = Depends(get_db),
                       org_id: int = Depends(get_org_scope), settings: Settings = Depends(get_settings)):
    """改造后的通行图：与正式链路同一分析+渲染（门→床路径、净宽红黄绿分级）。"""
    _, work = _scan_and_work(scan_id, db, org_id, settings)
    scheme = _get_scheme(db, scan_id, scheme_id)
    measurements, structure = _scheme_derived(work, scheme)
    passage = analyze_structure_passages(measurements, structure)
    buffer = io.BytesIO()
    render_passage_analysis(measurements, structure, passage, buffer)
    return Response(content=buffer.getvalue(), media_type="image/png",
                    headers={"Cache-Control": "no-store"})


@router.patch("/scans/{scan_id}/schemes/{scheme_id}")
def rename_scheme(scan_id: int, scheme_id: int, payload: SchemePatchRequest, db: Session = Depends(get_db),
                  admin: User = Depends(require_admin), settings: Settings = Depends(get_settings)):
    _scan_and_work(scan_id, db, admin.org_id, settings)
    scheme = _get_scheme(db, scan_id, scheme_id)
    scheme.name = payload.name
    db.commit()
    db.refresh(scheme)
    return {"id": scheme.id, "name": scheme.name}


@router.delete("/scans/{scan_id}/schemes/{scheme_id}")
def delete_scheme(scan_id: int, scheme_id: int, db: Session = Depends(get_db),
                  admin: User = Depends(require_admin), settings: Settings = Depends(get_settings)):
    _scan_and_work(scan_id, db, admin.org_id, settings)
    scheme = _get_scheme(db, scan_id, scheme_id)
    db.delete(scheme)
    db.commit()
    return {"deleted": scheme_id}


MAX_PLAN_BYTES = 10 * 1024 * 1024


@router.post("/scans/{scan_id}/schemes/{scheme_id}/export.pdf")
async def export_scheme_pdf(
    scan_id: int,
    scheme_id: int,
    plan: UploadFile = File(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
    settings: Settings = Depends(get_settings),
):
    scan, work = _scan_and_work(scan_id, db, admin.org_id, settings)
    scheme = _get_scheme(db, scan_id, scheme_id)
    measurements, structure = _require_measurements(work), _load_structure(work)
    payload = _scheme_payload(scan_id, measurements, structure, scheme.edits or [])
    if plan.size is not None and plan.size > MAX_PLAN_BYTES:
        raise HTTPException(413, "plan 图片过大（上限 10 MB）")
    png = await plan.read(MAX_PLAN_BYTES + 1)
    if len(png) > MAX_PLAN_BYTES:
        raise HTTPException(413, "plan 图片过大（上限 10 MB）")
    if not png.startswith(b"\x89PNG"):
        raise HTTPException(422, "plan 必须是 PNG 图片")
    out_dir = work / "schemes"
    out_dir.mkdir(parents=True, exist_ok=True)
    output = out_dir / f"scheme_{scheme_id}_{uuid4().hex}.pdf"
    status = _based_on_status(scheme, work)
    try:
        build_scheme_appendix_pdf(
            scan_name=(scan.project.name if scan.project else f"扫描 {scan_id}"),
            scheme_name=scheme.name,
            created_at=(scheme.created_at.isoformat() if scheme.created_at else ""),
            plan_png=png,
            before_score=(payload.get("comparison") or {}).get("before_score"),
            after_score=(payload.get("comparison") or {}).get("after_score"),
            metric_changes=payload["metric_changes"], risk_changes=payload["risk_changes"],
            provenance={**(scheme.based_on or {}), **status},
            disclaimer=payload["disclaimer"], output=output,
        )
    except HTTPException:
        raise
    except Exception as exc:
        # 魔术字节正确但内容损坏的 PNG：报告库解码失败，返回 422 而不是 500。
        if output.exists():
            output.unlink()
        raise HTTPException(422, "plan 不是有效的 PNG 图片") from exc
    pdf_bytes = output.read_bytes()
    output.unlink()
    ascii_name = scheme.name.encode("ascii", "ignore").decode().strip() or "scheme"
    return Response(
        content=pdf_bytes,
        media_type="application/pdf",
        headers={"Content-Disposition": (
            f'attachment; filename="{ascii_name}.pdf"; '
            f"filename*=UTF-8''{quote(scheme.name + '.pdf')}"
        )},
    )
