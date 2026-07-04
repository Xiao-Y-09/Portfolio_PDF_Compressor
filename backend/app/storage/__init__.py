"""存储层入口：根据配置自动选择 local 或 s3 后端。"""

from __future__ import annotations

from app.config.settings import get_settings
from app.storage.base import StorageBackend
from app.storage.local import LocalStorage
from app.storage.s3 import S3Storage

__all__ = ["StorageBackend", "LocalStorage", "S3Storage", "get_storage"]


def get_storage() -> StorageBackend:
    """工厂：settings.aws.s3_bucket 非空 → S3Storage，否则 LocalStorage。"""
    settings = get_settings()
    tmp_dir = settings.storage.resolved_tmp_dir()
    retention = settings.storage.retention_seconds
    if settings.aws.s3_bucket:
        return S3Storage(
            bucket=settings.aws.s3_bucket,
            region=settings.aws.region,
            local_cache_dir=tmp_dir,
            retention_seconds=retention,
        )
    return LocalStorage(tmp_dir=tmp_dir, retention_seconds=retention)
