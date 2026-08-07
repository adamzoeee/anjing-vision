import hashlib
import hmac
import time
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_org_scope
from ..models import Report, Scan

router = APIRouter()
_ASSET_TTL_SECONDS = 15 * 60


def _asset_signature(scan_id: int, filename: str, expires: int) -> str:
    secret = get_settings().secret_key.encode("utf-8")
    message = f"{scan_id}:{filename}:{expires}".encode("utf-8")
    return hmac.new(secret, message, hashlib.sha256).hexdigest()


def _asset_url(scan_id: int, image: str) -> str:
    filename = Path(image).name
    expires = int(time.time()) + _ASSET_TTL_SECONDS
    signature = _asset_signature(scan_id, filename, expires)
    return (
        f"/api/reports/assets/{scan_id}/{quote(filename)}"
        f"?expires={expires}&signature={signature}"
    )


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
        "images": [_asset_url(scan_id, image) for image in scan.report.images],
        "preview": scan.report.preview,
        "calibrated": scan.report.calibrated,
        "created_at": scan.report.created_at.isoformat(),
    }


@router.get("/assets/{scan_id}/{filename}")
def get_report_asset(
    scan_id: int,
    filename: str,
    expires: int = Query(..., ge=1),
    signature: str = Query(..., min_length=64, max_length=64),
    db: Session = Depends(get_db),
):
    if expires < int(time.time()):
        raise HTTPException(401, "资源链接已过期")
    expected = _asset_signature(scan_id, filename, expires)
    if not hmac.compare_digest(signature, expected):
        raise HTTPException(401, "资源签名无效")

    scan = db.get(Scan, scan_id)
    if scan is None or scan.report is None:
        raise HTTPException(404, "报告资源不存在")
    matched = next(
        (
            Path(image)
            for image in scan.report.images
            if Path(image).name == Path(filename).name
        ),
        None,
    )
    if matched is None or not matched.is_file():
        raise HTTPException(404, "报告资源不存在")
    return FileResponse(matched)


@router.get("/compare")
def compare(
    a: int = Query(..., ge=1, description="改造前扫描 ID"),
    b: int = Query(..., ge=1, description="改造后扫描 ID"),
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
):
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
