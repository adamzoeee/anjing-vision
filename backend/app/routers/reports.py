from fastapi import APIRouter, Depends, HTTPException, Query
from pathlib import Path
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_org_scope
from ..models import Scan

router = APIRouter()


@router.get("/scans/{scan_id}")
def get_report(scan_id: int, db: Session = Depends(get_db), org_id: int = Depends(get_org_scope)):
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    if scan.report is None:
        raise HTTPException(404, "报告尚未生成")
    return {
        "scan_id": scan_id,
        "score": scan.report.score,
        "risks": scan.report.risks,
        "measures": scan.report.measures,
        "advice": scan.report.advice,
        "images": [f"/static/{scan_id}/{Path(img).name}" for img in scan.report.images],
        "preview": scan.report.preview,
        "calibrated": scan.report.calibrated,
        "created_at": scan.report.created_at.isoformat(),
    }


@router.get("/compare")
def compare(
    before_scan_id: int | None = Query(default=None, ge=1),
    after_scan_id: int | None = Query(default=None, ge=1),
    legacy_before_scan_id: int | None = Query(
        default=None,
        alias="a",
        ge=1,
        include_in_schema=False,
    ),
    legacy_after_scan_id: int | None = Query(
        default=None,
        alias="b",
        ge=1,
        include_in_schema=False,
    ),
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
):
    """按扫描 ID 对比；a/b 仅作为现有 Flutter 的兼容参数。"""
    if (
        before_scan_id is not None
        and legacy_before_scan_id is not None
        and before_scan_id != legacy_before_scan_id
    ) or (
        after_scan_id is not None
        and legacy_after_scan_id is not None
        and after_scan_id != legacy_after_scan_id
    ):
        raise HTTPException(422, "新旧对比参数不能互相冲突")
    before_scan_id = before_scan_id or legacy_before_scan_id
    after_scan_id = after_scan_id or legacy_after_scan_id
    if before_scan_id is None or after_scan_id is None:
        raise HTTPException(422, "必须提供 before_scan_id 和 after_scan_id")

    before_scan = db.get(Scan, before_scan_id)
    after_scan = db.get(Scan, after_scan_id)
    if before_scan is None or after_scan is None:
        raise HTTPException(404, "扫描任务不存在")
    if (
        before_scan.project.org_id != org_id
        or after_scan.project.org_id != org_id
        or before_scan.project_id != after_scan.project_id
    ):
        raise HTTPException(404, "对比对象无效或不属于同一项目")
    before_report = before_scan.report
    after_report = after_scan.report
    if before_report is None or after_report is None:
        raise HTTPException(404, "报告不存在")
    return {
        "before": {
            "scan_id": before_report.scan_id,
            "score": before_report.score,
            "risks": before_report.risks,
        },
        "after": {
            "scan_id": after_report.scan_id,
            "score": after_report.score,
            "risks": after_report.risks,
        },
        "score_delta": round(after_report.score - before_report.score, 1),
    }
