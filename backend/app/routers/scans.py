import logging
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..config import get_settings
from ..db import get_db
from ..deps import get_org_scope, require_admin
from ..models import Scan, User
from ..schemas import ReferenceMeasurementsIn, ScanOut
from ..storage import (
    MediaTooLargeError,
    delete_media,
    save_media_stream,
)
from ..tasks.pipeline_tasks import TaskDispatchError, dispatch_scan

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
    defer_processing: bool = False,
    files: list[UploadFile] = File(...),
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    scan = _own_scan(scan_id, db, admin.org_id)
    if scan.capture_type not in ("video", "photos"):
        raise HTTPException(409, "整屋 Scan 不能上传单房间素材")
    if scan.status not in ("uploading", "awaiting_video", "failed"):
        raise HTTPException(409, "请新建扫描上传新素材，不能覆盖正在处理或已完成的结果")
    defer_processing = defer_processing or scan.status == "awaiting_video"
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
            if defer_processing and (
                Path(file.filename or "").suffix.lower() not in {".mp4", ".mov", ".avi", ".mkv"}
                or file.size == 0
            ):
                raise HTTPException(400, "请选择非空的 MP4 / MOV / AVI / MKV 新视频")
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

    if defer_processing:
        scan.status = "uploaded"
        scan.progress = 0
        scan.message = "新视频已上传，等待开始重建与分析"
    db.commit()
    if defer_processing:
        return {"ok": True, "media": scan.media_path}
    try:
        dispatch_scan(scan.id)
    except TaskDispatchError as exc:
        logger.warning(
            "scan_dispatch_failed scan_id=%s exception_type=%s",
            scan.id,
            type(exc).__name__,
        )
        scan.status = "failed"
        scan.message = "任务队列暂不可用，请稍后重试"
        db.commit()
    return {"ok": True, "media": scan.media_path}


@router.post("/{scan_id}/start")
def start_scan(
    scan_id: int,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    scan = _own_scan(scan_id, db, admin.org_id)
    if scan.capture_type not in ("video", "photos") or not scan.media_path:
        raise HTTPException(409, "请先上传新素材")
    # Compare-and-set prevents double clicks / concurrent requests from queuing twice.
    claimed = db.query(Scan).filter(Scan.id == scan_id, Scan.status == "uploaded").update(
        {Scan.status: "extracting", Scan.progress: 0, Scan.message: "已提交，等待开始重建与分析"},
        synchronize_session=False,
    )
    db.commit()
    if not claimed:
        raise HTTPException(409, "当前扫描不在等待开始状态，请刷新查看进度")
    try:
        dispatch_scan(scan_id)
    except TaskDispatchError as exc:
        db.query(Scan).filter(Scan.id == scan_id, Scan.status == "extracting").update(
            {Scan.status: "uploaded", Scan.message: "任务队列暂不可用，视频已保留，请重试开始"},
            synchronize_session=False,
        )
        db.commit()
        logger.warning("scan_start_dispatch_failed scan_id=%s", scan_id)
        raise HTTPException(503, "任务队列暂不可用，视频已保留，请稍后重试") from exc
    return {"ok": True, "scan_id": scan_id}


@router.get("/{scan_id}", response_model=ScanOut)
def get_scan(
    scan_id: int,
    db: Session = Depends(get_db),
    org_id: int = Depends(get_org_scope),
):
    return _own_scan(scan_id, db, org_id)


@router.put("/{scan_id}/references", response_model=ScanOut)
def set_reference_measurements(
    scan_id: int,
    data: ReferenceMeasurementsIn,
    db: Session = Depends(get_db),
    admin: User = Depends(require_admin),
):
    """在上传前保存用户提供的真实尺寸，供统一三维尺度标定。"""
    scan = _own_scan(scan_id, db, admin.org_id)
    if scan.status not in ("uploading", "awaiting_video", "failed"):
        raise HTTPException(409, "扫描已经开始处理，不能修改参考尺寸")
    scan.reference_measurements = [item.model_dump() for item in data.measurements]
    db.commit()
    db.refresh(scan)
    return scan
