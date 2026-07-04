"""S3 存储后端（可选）。

仅当 settings.aws.s3_bucket 非空时由工厂启用；接口与 LocalStorage 完全一致
（同一 StorageBackend ABC）。对象 key 为 <file_id>.<ext>。

凭证：boto3 从标准环境变量（AWS_ACCESS_KEY_ID / AWS_SECRET_ACCESS_KEY）或
EC2 IAM Role 读取，永不经过本项目配置文件。

get_path() 语义说明：消费方（pipeline）需要本地路径做 PDF 处理，因此 S3 后端
把对象下载到本地缓存目录再返回路径；缓存文件同样受 cleanup_expired() 管理。
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path

import boto3

from app.storage.base import (
    StorageBackend,
    new_file_id,
    validate_extension,
    validate_file_id,
)

logger = logging.getLogger(__name__)


class S3Storage(StorageBackend):
    def __init__(
        self,
        bucket: str,
        region: str,
        local_cache_dir: Path | str,
        retention_seconds: int,
    ) -> None:
        if not bucket:
            raise ValueError("S3Storage requires a non-empty bucket")
        self.bucket = bucket
        self.retention_seconds = retention_seconds
        self.local_cache_dir = Path(local_cache_dir)
        self.local_cache_dir.mkdir(parents=True, exist_ok=True)
        self.client = boto3.client("s3", region_name=region or None)

    # ---- 写入 ----

    def save_upload(self, file_bytes: bytes, extension: str) -> str:
        ext = validate_extension(extension)
        file_id = new_file_id()
        self.client.put_object(Bucket=self.bucket, Key=f"{file_id}.{ext}", Body=file_bytes)
        return file_id

    def save_from_path(self, source_path: Path | str) -> str:
        source = Path(source_path)
        if not source.is_file():
            raise FileNotFoundError(f"source not found: {source}")
        ext = validate_extension(source.suffix or ".bin")
        file_id = new_file_id()
        self.client.upload_file(str(source), self.bucket, f"{file_id}.{ext}")
        return file_id

    # ---- 读取 / 删除 ----

    def _find_key(self, file_id: str) -> str:
        validate_file_id(file_id)
        resp = self.client.list_objects_v2(Bucket=self.bucket, Prefix=f"{file_id}.")
        contents = resp.get("Contents", [])
        if not contents:
            raise FileNotFoundError(f"file_id not found in s3: {file_id}")
        return contents[0]["Key"]

    def get_path(self, file_id: str) -> Path:
        key = self._find_key(file_id)
        local_path = self.local_cache_dir / key
        if not local_path.exists():
            self.client.download_file(self.bucket, key, str(local_path))
        return local_path

    def delete(self, file_id: str) -> None:
        validate_file_id(file_id)
        resp = self.client.list_objects_v2(Bucket=self.bucket, Prefix=f"{file_id}.")
        for obj in resp.get("Contents", []):
            self.client.delete_object(Bucket=self.bucket, Key=obj["Key"])
            (self.local_cache_dir / obj["Key"]).unlink(missing_ok=True)

    # ---- 过期清理（按 S3 LastModified，双数据源之外的兜底同样成立）----

    def cleanup_expired(self) -> int:
        deadline = datetime.now(timezone.utc).timestamp() - self.retention_seconds
        removed = 0
        paginator = self.client.get_paginator("list_objects_v2")
        for page in paginator.paginate(Bucket=self.bucket):
            for obj in page.get("Contents", []):
                if obj["LastModified"].timestamp() < deadline:
                    self.client.delete_object(Bucket=self.bucket, Key=obj["Key"])
                    removed += 1
        # 本地缓存按 mtime 一并清理
        cutoff = time.time() - self.retention_seconds
        for path in self.local_cache_dir.iterdir():
            if path.is_file() and path.stat().st_mtime < cutoff:
                path.unlink(missing_ok=True)
        if removed:
            logger.info("cleanup_expired(s3): removed %d expired object(s)", removed)
        return removed
