"""FastAPI 入口（Phase 2）。

- startup（lifespan）：打印配置概况（隐藏敏感字段）+ 启动定时清理循环
  （每 CLEANUP_INTERVAL_SECONDS 秒调用 storage.cleanup_expired()，铁律 5 兜底）。
- REST 路由在 Phase 12 挂载。
- `python -m app.main --check`：自检模式（配置可加载、tmp_dir 可写、Redis 可达性）。
"""

from __future__ import annotations

import asyncio
import logging
import socket
import sys
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.config.settings import get_settings, summarize_settings
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
