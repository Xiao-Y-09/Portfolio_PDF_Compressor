"""压缩任务触发与状态查询（手册 Phase 12 第二项）。

POST /api/v1/compress             → 触发 compress_pdf_task，返回 task_id
GET  /api/v1/tasks/{task_id}      → 状态机映射（手册 §4.3）：
     PENDING | PROGRESS | AWAITING_REVIEW | SUCCESS | FAILURE + meta
POST /api/v1/tasks/{task_id}/resume → 用户 review 后触发 resume_compression_task，
     返回后半段新 task_id

状态映射说明：Celery 的任务级 SUCCESS 内嵌业务状态（AWAITING_REVIEW/SUCCESS
在任务返回 dict 的 status 字段），本层拆包成手册规定的五态。SUCCESS meta 含
tier_used/warning（Tier 系统 2026-07-04，前端展示降级警告）；FAILURE meta 含
PhaseErrorData（含 CONVERGENCE_FAILED 的 diagnostics）。
"""

from __future__ import annotations

import json
import logging
from typing import Any, Dict, List

from celery.result import AsyncResult
from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel, Field

from app.contracts import CompressionTarget, PageOverride, PhaseError
from app.pipeline.orchestrator import render_page_thumbnail
from app.queue.celery_app import celery_app
from app.queue.tasks import _redis, _session_key, compress_pdf_task, resume_compression_task
from app.storage import get_storage

logger = logging.getLogger("app.api.compress")

router = APIRouter()


class CompressRequest(BaseModel):
    file_id: str
    target_size_mb: float = Field(gt=0)
    compression_target: CompressionTarget
    per_page_overrides: List[PageOverride] = Field(default_factory=list)

    def to_user_prefs_dict(self) -> dict:
        return {
            "target_size_mb": self.target_size_mb,
            "compression_target": self.compression_target,
            "per_page_overrides": [o.model_dump() for o in self.per_page_overrides],
        }


class ResumeRequest(BaseModel):
    session_id: str
    modified_classified: List[Dict[str, Any]]
    user_prefs: CompressRequest


@router.post("/compress")
def start_compression(body: CompressRequest) -> dict:
    try:
        get_storage().get_path(body.file_id)  # 早失败：file_id 不存在/已过期清理
    except (FileNotFoundError, ValueError):
        raise HTTPException(status_code=404,
                            detail=f"file_id {body.file_id} not found (expired or never uploaded)")
    result = compress_pdf_task.delay(body.file_id, body.to_user_prefs_dict())
    logger.info("compress task %s started for file %s", result.id, body.file_id)
    return {"task_id": result.id}


@router.get("/tasks/{task_id}")
def task_status(task_id: str) -> dict:
    result = AsyncResult(task_id, app=celery_app)
    state = result.state

    if state == "SUCCESS":
        payload = result.result or {}
        business = payload.get("status") if isinstance(payload, dict) else None
        if business == "AWAITING_REVIEW":
            return {"state": "AWAITING_REVIEW", "meta": payload}
        return {"state": "SUCCESS", "meta": payload}
    if state == "FAILURE":
        exc = result.result
        if isinstance(exc, PhaseError):
            meta = {"error": exc.model_dump()}
        else:
            meta = {"error": {"code": "INTERNAL_ERROR", "message": str(exc)}}
        return {"state": "FAILURE", "meta": meta}
    if state in ("PROGRESS", "STARTED"):
        info = result.info if isinstance(result.info, dict) else {}
        return {"state": "PROGRESS", "meta": info}
    return {"state": "PENDING", "meta": {}}


@router.get("/tasks/{task_id}/pages/{page_number}/thumbnail")
def page_thumbnail(task_id: str, page_number: int) -> Response:
    """review 界面缩略图（Phase 14 规格倒逼的追加路由，2026-07-05）。

    仅在 AWAITING_REVIEW 会话存活期间可用（session 指针在 → 工作区在）；
    会话过期/页号越界 → 404。渲染逻辑在 orchestrator（api 层不碰 pymupdf）。
    """
    raw = _redis().get(_session_key(task_id))
    if raw is None:
        raise HTTPException(status_code=404, detail="session not found or expired")
    workspace = json.loads(raw)["tmp_workspace"]
    try:
        png = render_page_thumbnail(workspace, page_number)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return Response(content=png, media_type="image/png",
                    headers={"Cache-Control": "private, max-age=1800"})


@router.post("/tasks/{task_id}/resume")
def resume_compression(task_id: str, body: ResumeRequest) -> dict:
    result = resume_compression_task.delay(
        body.session_id, body.modified_classified, body.user_prefs.to_user_prefs_dict(),
    )
    logger.info("resume task %s started for session %s", result.id, body.session_id)
    return {"task_id": result.id}
