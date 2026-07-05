"""REST 路由层（Phase 12）。所有路由挂载于 /api/v1 前缀（main.py 集成）。"""

from app.api.compress import router as compress_router
from app.api.download import router as download_router
from app.api.errors import register_error_handlers
from app.api.health import router as health_router
from app.api.upload import router as upload_router

__all__ = [
    "upload_router",
    "compress_router",
    "download_router",
    "health_router",
    "register_error_handlers",
]
