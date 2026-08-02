"""管道任务分发：task_sync=True 同步执行（开发兜底）；否则投递 Celery，投递失败回退同步。"""
from ..config import get_settings
from .celery_app import celery

s = get_settings()


def dispatch_scan(scan_id: int):
    if s.task_sync:
        run_pipeline_sync(scan_id)
    else:
        try:
            run_pipeline_async.delay(scan_id)
        except Exception:
            # Redis/Celery 不可用（开发环境）→ 回退同步执行
            run_pipeline_sync(scan_id)


def run_pipeline_sync(scan_id: int):
    """同步执行整条管道。A12 将充实完整实现；当前标记失败以保持状态机流转。"""
    from ..db import SessionLocal
    from ..models import Scan
    db = SessionLocal()
    try:
        scan = db.get(Scan, scan_id)
        if scan is None:
            return
        scan.status = "failed"
        scan.progress = 100
        scan.message = "管道实现中（A12）"
        db.commit()
    finally:
        db.close()


@celery.task(bind=True)
def run_pipeline_async(self, scan_id: int):
    run_pipeline_sync(scan_id)
