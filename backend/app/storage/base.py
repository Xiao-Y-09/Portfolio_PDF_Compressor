"""存储抽象接口（Phase 2 定版）。

接口约定采用**显式抽象基类**（非 duck typing）：local 与 s3 后端必须实现完全
一致的方法签名。本接口是 Phase 4-10 的公共依赖，定版后不得修改；如需调整，
按铁律 6 回到本文件重新定义并评估所有消费方。

安全约定：
- file_id 一律为 UUID v4 字符串。不可枚举（攻击者无法通过 file_id=1,2,3...
  遍历他人文件），且所有实现必须先校验 file_id 格式再拼路径（防路径穿越）。
- cleanup_expired() 以文件自身的时间戳（mtime / LastModified）判断过期，
  不依赖 Redis 等外部记录——即使记录源故障也能兜底清理（防御性设计，
  临时文件清理属于安全行为，不允许依赖单一数据源）。
"""

from __future__ import annotations

import re
import uuid
from abc import ABC, abstractmethod
from pathlib import Path

_EXTENSION_RE = re.compile(r"^[a-z0-9]{1,10}$")


def new_file_id() -> str:
    """生成不可枚举的 file_id（UUID v4）。"""
    return str(uuid.uuid4())


def validate_file_id(file_id: str) -> str:
    """校验 file_id 是合法 UUID，防止路径穿越/注入。非法则 ValueError。"""
    try:
        uuid.UUID(file_id)
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"illegal file_id: {file_id!r}") from exc
    return file_id


def validate_extension(extension: str) -> str:
    """规范化并校验扩展名（仅小写字母数字，防注入）。返回不带点的扩展名。"""
    ext = extension.lower().lstrip(".")
    if not _EXTENSION_RE.match(ext):
        raise ValueError(f"illegal extension: {extension!r}")
    return ext


class StorageBackend(ABC):
    """所有存储后端的统一接口。"""

    @abstractmethod
    def save_upload(self, file_bytes: bytes, extension: str) -> str:
        """保存字节流，返回新分配的 file_id（UUID v4）。"""

    @abstractmethod
    def save_from_path(self, source_path: Path | str) -> str:
        """把已有文件收入存储（如 Phase 11 收纳最终产出），返回新 file_id。"""

    @abstractmethod
    def get_path(self, file_id: str) -> Path:
        """返回 file_id 对应的本地可读路径。不存在则 FileNotFoundError。"""

    @abstractmethod
    def delete(self, file_id: str) -> None:
        """删除 file_id 对应的文件。幂等：不存在时静默返回。"""

    @abstractmethod
    def cleanup_expired(self) -> int:
        """按文件自身时间戳删除超过 retention_seconds 的文件，返回删除数量。"""
