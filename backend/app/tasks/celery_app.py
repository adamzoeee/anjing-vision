from celery import Celery

from ..config import get_settings

s = get_settings()
celery = Celery("anjing", broker=s.celery_broker_url, backend=s.celery_broker_url)
celery.conf.update(
    task_serializer="json", result_serializer="json", accept_content=["json"],
    broker_connection_timeout=2,
    broker_connection_retry_on_startup=False,
)

# 注册任务模块，否则 worker 收到任务会报 unregistered task
from . import pipeline_tasks  # noqa: E402,F401

