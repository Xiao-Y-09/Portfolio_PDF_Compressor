"""GET /api/v1/download/{download_id}（手册 Phase 12 第三项）。

Content-Disposition: attachment 返回压缩产物。生命周期：storage 定时清理
（retention_seconds=300）自保存起 5 分钟删除——满足手册"下载后 5 分钟删除"
的上界（自保存起计时更严格；铁律 5 的三重保障之一，无需为下载单独起定时器）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse

from app.storage import get_storage

logger = logging.getLogger("app.api.download")

router = APIRouter()


@router.get("/download/{download_id}")
def download_pdf(download_id: str) -> FileResponse:
    try:
        path = get_storage().get_path(download_id)
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404,
                            detail=f"download_id {download_id} not found (expired or invalid)")
    logger.info("download %s (%.2fMB)", download_id, path.stat().st_size / 1e6)
    return FileResponse(
        path=str(path),
        media_type="application/pdf",
        filename="compressed.pdf",
        content_disposition_type="attachment",
    )
