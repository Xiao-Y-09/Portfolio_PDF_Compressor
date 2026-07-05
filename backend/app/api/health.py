"""GET /api/v1/health（手册 Phase 12 第四项）。

返回 { status, redis_connected, storage_writable }。探针与
`python -m app.main --check` 同源逻辑：storage 写删探针 + Redis TCP 可达。
"""

from __future__ import annotations

import logging
import socket

from fastapi import APIRouter

from app.config.settings import get_settings
from app.storage import get_storage

logger = logging.getLogger("app.api.health")

router = APIRouter()

REDIS_PROBE_TIMEOUT = 2.0


def _redis_connected() -> bool:
    s = get_settings().redis
    try:
        with socket.create_connection((s.host, s.port), timeout=REDIS_PROBE_TIMEOUT):
            return True
    except OSError:
        return False


def _storage_writable() -> bool:
    try:
        storage = get_storage()
        probe_id = storage.save_upload(b"probe", "bin")
        storage.delete(probe_id)
        return True
    except Exception:
        logger.warning("storage probe failed", exc_info=True)
        return False


@router.get("/health")
def health() -> dict:
    redis_ok = _redis_connected()
    storage_ok = _storage_writable()
    return {
        "status": "ok" if (redis_ok and storage_ok) else "degraded",
        "redis_connected": redis_ok,
        "storage_writable": storage_ok,
    }
