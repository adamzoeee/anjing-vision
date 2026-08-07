import logging

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_org_scope
from ..models import Scan
from ..schemas import ScanOut
from ..storage import (
    MediaTooLargeError,
    delete_media,
    save_media_stream,
)
from ..tasks.pipeline_tasks import dispatch_scan

router = APIRouter()
logger = logging.getLogger("anjing.api")

MAX_UPLOAD_BYTES = get_settings().max_upload_bytes


def _own_scan(scan_id: int, db: Session, org_id: int) -> Scan:
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    return scan


@router.post("/{scan_id}/upload")
def upload(
    scan_id: int,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
):
    scan = _own_scan(scan_id, db, org_id)
    stored_paths: list[str] = []
    remaining = MAX_UPLOAD_BYTES
    try:
        if scan.capture_type == "photos":
            for file in files:
                stored = save_media_stream(
                    scan_id,
                    file.filename or "photo.jpg",
                    file.file,
                    remaining,
                )
                stored_paths.append(stored.path)
                remaining -= stored.size
            scan.media_path = f"media/{scan_id}"
        else:
            if len(files) != 1:
                raise HTTPException(400, "视频模式仅接受单个文件")
            file = files[0]
            stored = save_media_stream(
                scan_id,
                file.filename or "media.bin",
                file.file,
                MAX_UPLOAD_BYTES,
            )
            stored_paths.append(stored.path)
            scan.media_path = stored.path
    except MediaTooLargeError as exc:
        for path in stored_paths:
            delete_media(path)
        raise HTTPException(413, "上传内容超过总大小上限") from exc

    db.commit()
    try:
        dispatch_scan(scan.id)
    except Exception as exc:  # task dispatch is isolated from upload success
        logger.warning(
            "scan_dispatch_failed scan_id=%s exception_type=%s",
            scan.id,
            type(exc).__name__,
        )
        scan.status = "failed"
        scan.message = "任务队列暂不可用，请稍后重试"
        db.commit()
    return {"ok": True, "media": scan.media_path}


@router.get("/{scan_id}", response_model=ScanOut)
def get_scan(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
):
    return _own_scan(scan_id, db, org_id)
