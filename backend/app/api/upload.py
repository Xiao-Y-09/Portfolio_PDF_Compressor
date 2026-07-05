"""POST /api/v1/upload（手册 Phase 12 第一项）。

校验三关（《系统架构设计.md》§6.5）：Content-Type application/pdf →
首字节 %PDF- 魔数 → 大小 ≤ settings.api.max_upload_mb（超限 413）。
通过后存入 storage，返回 file_id（UUID v4，不可枚举）。

V1 实现说明：文件一次性读入内存（500MB 上限内可接受）；上传物生命周期由
storage.retention_seconds（300s）+ 定时清理兜底（铁律 5）。
"""

from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException, UploadFile

from app.config.settings import get_settings
from app.storage import get_storage

logger = logging.getLogger("app.api.upload")

router = APIRouter()

PDF_MAGIC = b"%PDF-"


@router.post("/upload")
async def upload_pdf(file: UploadFile) -> dict:
    if (file.content_type or "").lower() != "application/pdf":
        raise HTTPException(status_code=415,
                            detail=f"expect application/pdf, got {file.content_type!r}")
    data = await file.read()
    max_bytes = get_settings().api.max_upload_mb * 1024 * 1024
    if len(data) > max_bytes:
        raise HTTPException(status_code=413,
                            detail=f"file exceeds {get_settings().api.max_upload_mb}MB limit")
    if not data.startswith(PDF_MAGIC):
        raise HTTPException(status_code=415, detail="not a PDF (missing %PDF- magic)")

    file_id = get_storage().save_upload(data, "pdf")
    logger.info("uploaded %s (%.2fMB)", file_id, len(data) / 1e6)
    return {"file_id": file_id, "size_mb": round(len(data) / 1e6, 2)}
