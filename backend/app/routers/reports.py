from fastapi import APIRouter, Depends, HTTPException
from pathlib import Path
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_org_scope
from ..models import Report, Scan

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
def compare(a: int, b: int, db: Session = Depends(get_db), org_id: int = Depends(get_org_scope)):
    """按 Flutter 使用的 Scan ID 对比同一项目下两次报告。"""
    scan_a, scan_b = db.get(Scan, a), db.get(Scan, b)
    if scan_a is None or scan_b is None:
        raise HTTPException(404, "扫描任务不存在")
    if (
        scan_a.project.org_id != org_id
        or scan_b.project.org_id != org_id
        or scan_a.project_id != scan_b.project_id
    ):
        raise HTTPException(404, "对比对象无效或不属于同一项目")
    ra, rb = scan_a.report, scan_b.report
    if ra is None or rb is None:
        raise HTTPException(404, "报告不存在")
    return {
        "before": {"scan_id": ra.scan_id, "score": ra.score, "risks": ra.risks},
        "after": {"scan_id": rb.scan_id, "score": rb.score, "risks": rb.risks},
        "score_delta": round(rb.score - ra.score, 1),
    }
