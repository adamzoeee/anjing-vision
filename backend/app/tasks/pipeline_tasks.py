"""管道任务分发；生产环境禁止队列失败后在 API 请求内同步执行。"""
import threading

from celery.exceptions import CeleryError
from kombu.exceptions import KombuError

from ..config import get_settings
from .celery_app import celery

s = get_settings()


class TaskDispatchError(RuntimeError):
    pass


def dispatch_scan(scan_id: int):
    if s.task_sync:
        _run_in_background(scan_id)
    else:
        try:
            run_pipeline_async.delay(scan_id)
        except (CeleryError, KombuError, OSError) as exc:
            if not s.allow_sync_fallback:
                raise TaskDispatchError("任务队列不可用") from exc
            _run_in_background(scan_id)


def _run_in_background(scan_id: int):
    """同步兜底改为后台线程执行，避免 upload 请求被分钟级管道阻塞到超时。"""
    threading.Thread(target=run_pipeline_sync, args=(scan_id,), daemon=True).start()


def run_pipeline_sync(scan_id: int):
    from .pipeline_runner import run_pipeline
    run_pipeline(scan_id)


@celery.task(bind=True)
def run_pipeline_async(self, scan_id: int):
    run_pipeline_sync(scan_id)
