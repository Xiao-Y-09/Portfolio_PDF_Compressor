"""本地文件存储（EC2 EBS / 开发机磁盘）。

所有文件保存为 tmp_dir/<file_id>.<ext>，file_id 为 UUID v4。
cleanup_expired() 按文件 mtime 判断过期（不依赖任何外部记录，见 base.py）。
"""

from __future__ import annotations

import logging
import shutil
import time
from pathlib import Path

from app.storage.base import (
    StorageBackend,
    new_file_id,
    validate_extension,
    validate_file_id,
)

logger = logging.getLogger(__name__)


class LocalStorage(StorageBackend):
    def __init__(self, tmp_dir: Path | str, retention_seconds: int) -> None:
        self.tmp_dir = Path(tmp_dir)
        self.retention_seconds = retention_seconds
        self.tmp_dir.mkdir(parents=True, exist_ok=True)

    # ---- 写入 ----

    def save_upload(self, file_bytes: bytes, extension: str) -> str:
        ext = validate_extension(extension)
        file_id = new_file_id()
        path = self.tmp_dir / f"{file_id}.{ext}"
        path.write_bytes(file_bytes)
        return file_id

    def save_from_path(self, source_path: Path | str) -> str:
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(f"source not found: {source}")
        ext = validate_extension(source.suffix or ".bin")
        file_id = new_file_id()
        shutil.copy2(source, self.tmp_dir / f"{file_id}.{ext}")
        return file_id

    # ---- 读取 / 删除 ----

    def get_path(self, file_id: str) -> Path:
        validate_file_id(file_id)
        matches = sorted(self.tmp_dir.glob(f"{file_id}.*"))
        if not matches:
            raise FileNotFoundError(f"file_id not found: {file_id}")
        return matches[0]

    def delete(self, file_id: str) -> None:
        validate_file_id(file_id)
        for path in self.tmp_dir.glob(f"{file_id}.*"):
            path.unlink(missing_ok=True)

    # ---- 过期清理（铁律 5 的定时兜底）----

    def cleanup_expired(self) -> int:
        """扫描 tmp_dir 顶层文件，删除 mtime 早于 retention_seconds 的文件。

        只清理文件；任务工作区目录（tmp_workspace）的生命周期由 orchestrator
        的成功/失败清理 + review session 超时扫描负责（Phase 11）。
        """
        deadline = time.time() - self.retention_seconds
        removed = 0
        for path in self.tmp_dir.iterdir():
            if not path.is_file():
                continue
            try:
                if path.stat().st_mtime < deadline:
                    path.unlink(missing_ok=True)
                    removed += 1
            except OSError:
                # 文件正被占用/已被并发删除：跳过，下轮再试
                logger.warning("cleanup_expired: skip %s", path, exc_info=True)
        if removed:
            logger.info("cleanup_expired: removed %d expired file(s)", removed)
        return removed
