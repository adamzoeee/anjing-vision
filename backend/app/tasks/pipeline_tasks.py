"""管道任务分发；生产环境禁止队列失败后在 API 请求内同步执行。"""
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
            if not s.allow_sync_fallback:
                raise
            run_pipeline_sync(scan_id)


def run_pipeline_sync(scan_id: int):
    from .pipeline_runner import run_pipeline
    run_pipeline(scan_id)


@celery.task(bind=True)
def run_pipeline_async(self, scan_id: int):
    run_pipeline_sync(scan_id)
