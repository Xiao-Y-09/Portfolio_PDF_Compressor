"""Phase 2 存储层测试：保存、读取、删除、过期清理、安全校验。"""

from __future__ import annotations

import os
import time
import uuid

import pytest

from app.storage import get_storage
from app.storage.base import StorageBackend, validate_extension, validate_file_id
from app.storage.local import LocalStorage

PDF_BYTES = b"%PDF-1.7 fake content for storage tests"


@pytest.fixture
def storage(tmp_path) -> LocalStorage:
    return LocalStorage(tmp_dir=tmp_path, retention_seconds=300)


# ---------- 保存 / 读取 ----------

def test_save_upload_returns_uuid4_and_persists(storage, tmp_path):
    file_id = storage.save_upload(PDF_BYTES, "pdf")
    assert uuid.UUID(file_id).version == 4  # 不可枚举的 UUID v4（安全默认）
    saved = tmp_path / f"{file_id}.pdf"
    assert saved.exists()
    assert saved.read_bytes() == PDF_BYTES


def test_get_path_roundtrip(storage):
    file_id = storage.save_upload(PDF_BYTES, ".PDF")  # 带点大写也应规范化
    path = storage.get_path(file_id)
    assert path.suffix == ".pdf"
    assert path.read_bytes() == PDF_BYTES


def test_get_path_unknown_id_raises(storage):
    with pytest.raises(FileNotFoundError):
        storage.get_path(str(uuid.uuid4()))


def test_save_from_path(storage, tmp_path):
    source = tmp_path / "source.pdf"
    source.write_bytes(PDF_BYTES)
    file_id = storage.save_from_path(source)
    assert storage.get_path(file_id).read_bytes() == PDF_BYTES


# ---------- 删除 ----------

def test_delete_removes_file_and_is_idempotent(storage):
    file_id = storage.save_upload(PDF_BYTES, "pdf")
    storage.delete(file_id)
    with pytest.raises(FileNotFoundError):
        storage.get_path(file_id)
    storage.delete(file_id)  # 第二次删除不抛错（幂等）


# ---------- 安全校验 ----------

@pytest.mark.parametrize("bad_id", ["../../etc/passwd", "..\\..\\evil", "1", "", "*"])
def test_illegal_file_id_rejected(storage, bad_id):
    with pytest.raises(ValueError):
        storage.get_path(bad_id)
    with pytest.raises(ValueError):
        storage.delete(bad_id)


@pytest.mark.parametrize("bad_ext", ["p/../df", "pdf.exe", "a" * 11, "", "p df"])
def test_illegal_extension_rejected(storage, bad_ext):
    with pytest.raises(ValueError):
        storage.save_upload(PDF_BYTES, bad_ext)


def test_validators_accept_legal_values():
    assert validate_extension(".PDF") == "pdf"
    fid = str(uuid.uuid4())
    assert validate_file_id(fid) == fid


# ---------- 过期清理（按 mtime，防御性设计：不依赖外部记录）----------

def test_cleanup_expired_removes_only_stale_files(tmp_path):
    st = LocalStorage(tmp_dir=tmp_path, retention_seconds=100)
    stale_id = st.save_upload(b"stale", "pdf")
    fresh_id = st.save_upload(b"fresh", "pdf")

    stale_path = st.get_path(stale_id)
    past = time.time() - 101  # 刚过 retention 线
    os.utime(stale_path, (past, past))

    removed = st.cleanup_expired()

    assert removed == 1
    with pytest.raises(FileNotFoundError):
        st.get_path(stale_id)
    assert st.get_path(fresh_id).exists()  # 未过期文件不受影响


def test_cleanup_expired_ignores_directories(tmp_path):
    st = LocalStorage(tmp_dir=tmp_path, retention_seconds=0)
    workspace = tmp_path / "workspace_of_running_task"
    workspace.mkdir()
    assert st.cleanup_expired() == 0
    assert workspace.exists()  # 工作区目录由 orchestrator 管理，定时清理不碰


# ---------- 工厂 ----------

def test_get_storage_returns_local_backend_when_s3_disabled():
    # config.yaml 中 aws.s3_bucket 为空 → 本地后端
    backend = get_storage()
    assert isinstance(backend, LocalStorage)
    assert isinstance(backend, StorageBackend)
