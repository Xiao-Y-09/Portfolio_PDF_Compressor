"""Celery 应用配置（Phase 2）。

技术选型（用户批准，2026-07-04）：Celery 而非 RQ——更成熟、社区更大，
任务链与进度回调（update_state）支持更完善，Phase 11 的
PROGRESS / AWAITING_REVIEW 状态机依赖后者。

Broker 与 result backend 均为 Redis（settings.redis）。
"""

from __future__ import annotations

from celery import Celery

from app.config.settings import get_settings

settings = get_settings()

celery_app = Celery(
    "pdf_compressor",
    broker=settings.redis.url,
    backend=settings.redis.url,
    include=["app.queue.tasks"],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    task_track_started=True,
    result_expires=3600,
    timezone="UTC",
    enable_utc=True,
    # worker 重启时 broker 未就绪不致命，持续重试
    broker_connection_retry_on_startup=True,
)
