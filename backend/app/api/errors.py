"""Phase 12：统一错误处理（手册 Phase 12 第六项）。

- PhaseError → HTTP 4xx/5xx + JSON body（body = PhaseErrorData.model_dump()，
  含 diagnostics——CONVERGENCE_FAILED 的可行动诊断经此透传给前端，Phase 14 渲染）
- 未捕获异常 → 500，log 完整堆栈但不返回给用户（不泄露内部细节）

状态码映射原则：用户输入/目标可修正 → 4xx；系统内部失败 → 5xx。
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app.contracts import PhaseError

logger = logging.getLogger("app.api.errors")

# code → HTTP 状态。未列出的 code 默认 500（保守：未知错误当内部错误）。
PHASE_ERROR_STATUS = {
    "INVALID_PDF": 422,          # 输入不是有效 PDF
    "ENCRYPTED_PDF": 422,        # 加密 PDF（用户可解密后重传）
    "TARGET_TOO_SMALL": 422,     # Tier 系统下通常不直达用户，保留映射以防直调
    "CONVERGENCE_FAILED": 422,   # 三级降级耗尽，body 携带 diagnostics 可行动建议
    "SESSION_EXPIRED": 410,      # review 会话过期（Gone），需重新上传
    "EXECUTION_FAILURE_RATE": 500,
    "ASSEMBLY_FAILED": 500,
}


def register_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(PhaseError)
    async def phase_error_handler(request: Request, exc: PhaseError):
        status = PHASE_ERROR_STATUS.get(exc.code, 500)
        logger.info("PhaseError -> %d: [%s:%s]", status, exc.phase, exc.code)
        return JSONResponse(status_code=status, content={"error": exc.model_dump()})

    @app.exception_handler(Exception)
    async def unhandled_handler(request: Request, exc: Exception):
        logger.exception("unhandled exception on %s %s", request.method, request.url.path)
        return JSONResponse(status_code=500,
                            content={"error": {"code": "INTERNAL_ERROR",
                                               "message": "internal server error"}})
