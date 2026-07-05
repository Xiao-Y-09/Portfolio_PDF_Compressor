"""FastAPI 入口（Phase 2 骨架 + Phase 11/12 扩展）。

- startup（lifespan）：打印配置概况（隐藏敏感字段）+ 启动定时清理循环
  （每 CLEANUP_INTERVAL_SECONDS 秒调用 storage.cleanup_expired() 清上传文件 +
  orchestrator.cleanup_stale_workspaces() 清过期 review session 工作区，
  铁律 5 兜底、《系统架构设计.md》§4.4/§5.3）。
- REST 路由（Phase 12）：/api/v1 前缀挂载 upload/compress/tasks/download/health；
  CORS 来源从 config 读取；PhaseError → 4xx/5xx JSON（api/errors.py）。
- `python -m app.main --check`：自检模式（配置可加载、tmp_dir 可写、Redis 可达性）。
"""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api import (
    compress_router,
    download_router,
    health_router,
    register_error_handlers,
    upload_router,
)
from app.config.settings import get_settings, summarize_settings
from app.pipeline.orchestrator import cleanup_stale_workspaces
from app.storage import get_storage

logger = logging.getLogger("app.main")

# 手册 Phase 2 规定：每 60 秒执行一次过期清理
CLEANUP_INTERVAL_SECONDS = 60


async def _cleanup_loop() -> None:
    storage = get_storage()
    while True:
        try:
            storage.cleanup_expired()
        except Exception:  # 清理失败不能拖垮服务，下一轮重试
            logger.exception("periodic cleanup_expired failed")
        try:
            cleanup_stale_workspaces()
        except Exception:
            logger.exception("periodic cleanup_stale_workspaces failed")
        await asyncio.sleep(CLEANUP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    logging.basicConfig(level=settings.app.log_level)
    logger.info("config summary: %s", summarize_settings(settings))
    cleanup_task = asyncio.create_task(_cleanup_loop())
    try:
        yield
    finally:
        cleanup_task.cancel()


app = FastAPI(title="PDF Compressor API", lifespan=lifespan)

# CORS（手册 Phase 12 第五项）：允许来源从 config 读取（dev: localhost:3000）
app.add_middleware(
    CORSMiddleware,
    allow_origins=get_settings().api.cors_origins,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.middleware("http")
async def log_client_session(request, call_next):
    """client session token 关联日志（Phase 14 用户指定，2026-07-05）：
    前端为同一浏览器会话生成 UUID，经 X-Client-Session 头随每个请求发送；
    后端简单信任、仅日志关联（无鉴权语义——暂无账户系统）。"""
    token = request.headers.get("x-client-session")
    response = await call_next(request)
    if token:
        logger.info("client-session=%s %s %s -> %d",
                    token[:8], request.method, request.url.path, response.status_code)
    return response

# 统一错误处理（手册 Phase 12 第六项）：PhaseError → 4xx/5xx；未捕获 → 500 不泄露
register_error_handlers(app)

# REST 路由（手册 Phase 12 第一至四项）
API_PREFIX = "/api/v1"
app.include_router(upload_router, prefix=API_PREFIX)
app.include_router(compress_router, prefix=API_PREFIX)
app.include_router(download_router, prefix=API_PREFIX)
app.include_router(health_router, prefix=API_PREFIX)


# ---------- 自检模式 ----------

def _redis_reachable(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def run_check() -> int:
    """python -m app.main --check：非零退出码表示配置/存储层损坏。"""
    settings = get_settings()
    print("config summary:", summarize_settings(settings))

    # tmp_dir 可写性探针
    storage = get_storage()
    try:
        probe_id = storage.save_upload(b"probe", "bin")
        storage.delete(probe_id)
        print("storage: writable ok")
    except Exception as exc:
        print(f"storage: FAILED ({exc})")
        return 1

    # Redis 可达性（信息性：本地开发可能未启动 Redis，不作为失败条件）
    # 注意：--check 的所有 print 保持 ASCII，避免 Windows 控制台 cp1252 编码崩溃
    reachable = _redis_reachable(settings.redis.host, settings.redis.port)
    print(f"redis: {'reachable' if reachable else 'NOT reachable (worker/queue requires Redis)'}")

    print("check: ok")
    return 0


if __name__ == "__main__":
    if "--check" in sys.argv:
        raise SystemExit(run_check())
    import uvicorn

    uvicorn.run("app.main:app", host="127.0.0.1", port=8000, reload=True)
