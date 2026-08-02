from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from sqlalchemy.orm import Session

from ..db import get_db
from ..deps import get_org_scope
from ..models import Project, Scan
from ..schemas import ScanIn, ScanOut
from ..storage import save_media
from ..tasks.pipeline_tasks import dispatch_scan

router = APIRouter()


def _own_scan(scan_id: int, db: Session, org_id: int) -> Scan:
    scan = db.get(Scan, scan_id)
    if scan is None or scan.project.org_id != org_id:
        raise HTTPException(404, "扫描任务不存在")
    return scan




@router.post("/{scan_id}/upload")
def upload(scan_id: int, file: UploadFile = File(...), db: Session = Depends(get_db),
           org_id: int = Depends(get_org_scope)):
    scan = _own_scan(scan_id, db, org_id)
    content = file.file.read()
    rel = save_media(scan_id, file.filename or "media.bin", content)
    scan.media_path = rel
    db.commit()
    # 上传完成 → 触发管道（同步或 Celery）
    dispatch_scan(scan.id)
    return {"ok": True, "media": rel}


@router.get("/{scan_id}", response_model=ScanOut)
def get_scan(scan_id: int, db: Session = Depends(get_db), org_id: int = Depends(get_org_scope)):
    scan = _own_scan(scan_id, db, org_id)
    return scan
