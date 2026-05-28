"""Celery 应用 - 队列 = ocr / asr / mdt(高优先级独占)。"""
from __future__ import annotations

from celery import Celery

from config import settings

celery_app = Celery(
    "tumorboard",
    broker=settings.redis_url,
    backend=settings.redis_url,
)

celery_app.conf.update(
    task_serializer="json",
    accept_content=["json"],
    result_serializer="json",
    timezone="Asia/Shanghai",
    enable_utc=False,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_routes={
        "tasks.ocr_task": {"queue": "ocr"},
        "tasks.asr_task": {"queue": "asr"},
        "tasks.summary_task": {"queue": "mdt"},
        "tasks.mdt_analysis_task": {"queue": "mdt"},
    },
    task_default_queue="default",
    broker_connection_retry_on_startup=True,
)

# 显式 import 任务模块以注册 task
from services import celery_tasks  # noqa: E402,F401
