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
    from .pipeline_runner import run_pipeline
    run_pipeline(scan_id)


@celery.task(bind=True)
def run_pipeline_async(self, scan_id: int):
    run_pipeline_sync(scan_id)
