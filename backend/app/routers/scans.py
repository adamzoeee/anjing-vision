from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_org_scope
from ..models import Scan
from ..schemas import ScanOut
from ..storage import save_media
from ..tasks.pipeline_tasks import dispatch_scan

router = APIRouter()

MAX_UPLOAD_BYTES = 512 * 1024 * 1024  # 512MB 上传上限（1~3 分钟手机视频）


def _own_scan(scan_id: int, db: Session, org_id: int) -> Scan:
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    return scan


@router.post("/{scan_id}/upload")
def upload(scan_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
           org_id: int = Depends(get_org_scope)):
    scan = _own_scan(scan_id, db, org_id)
    content = file.file.read(MAX_UPLOAD_BYTES + 1)
    if len(content) > MAX_UPLOAD_BYTES:
        raise HTTPException(413, "文件超过 512MB 上限")
    rel = save_media(scan_id, file.filename or "media.bin", content)
    scan.media_path = rel
    db.commit()
    # 上传完成 → 触发管道（同步或 Celery）；管道启动失败不影响上传结果，错误写入 scan.message
    try:
        dispatch_scan(scan.id)
    except Exception as e:  # noqa: BLE001
        scan.status = "failed"
        scan.message = f"管道启动失败: {e}"
        db.commit()
    return {"ok": True, "media": rel}


@router.get("/{scan_id}", response_model=ScanOut)
def get_scan(scan_id: int, db: Session = Depends(get_db), org_id: int = Depends(get_org_scope)):
    scan = _own_scan(scan_id, db, org_id)
    return scan
