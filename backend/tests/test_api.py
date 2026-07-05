"""Phase 12 REST API 测试（httpx.AsyncClient + ASGITransport，Celery eager）。

覆盖手册 Phase 12 验证点：完整流程 upload → compress → 查询（AWAITING_REVIEW）
→ resume → 查询（SUCCESS）→ download；上传三关校验；错误映射（404/410/413/415）；
health 探针。

Celery 说明：eager 模式 + task_store_eager_result=True——GET /tasks/{id} 走
AsyncResult 需要结果后端；eager 结果写入 celery_app 配置的 redis 后端
（import 时绑定 dev db0，key celery-task-meta-* 随 result_expires=3600 过期，
测试残留可接受且自清理）。
"""

from __future__ import annotations

import io
import uuid

import httpx
import pytest
import redis as redis_lib

from app.config.settings import get_settings
from app.main import app
from app.queue.celery_app import celery_app
from app.storage import get_storage

from test_assemble import build_pdf_with_images

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend():
    return "asyncio"


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    monkeypatch.setenv("STORAGE__TMP_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("REDIS__DB", "15")
    get_settings.cache_clear()
    yield
    try:
        redis_lib.Redis.from_url(get_settings().redis.url).flushdb()
    except Exception:
        pass
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _eager_celery():
    old = (celery_app.conf.task_always_eager,
           celery_app.conf.task_eager_propagates,
           celery_app.conf.task_store_eager_result)
    celery_app.conf.task_always_eager = True
    # API 层要把任务异常转成 FAILURE 状态查询，不能让 eager 直接向上抛
    celery_app.conf.task_eager_propagates = False
    celery_app.conf.task_store_eager_result = True
    yield
    (celery_app.conf.task_always_eager,
     celery_app.conf.task_eager_propagates,
     celery_app.conf.task_store_eager_result) = old


@pytest.fixture
async def client():
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport,
                                 base_url="http://test") as c:
        yield c


def _pdf_bytes(tmp_path, n_pages=2) -> bytes:
    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=n_pages)
    return src.read_bytes()


async def _upload(client, tmp_path) -> str:
    resp = await client.post("/api/v1/upload", files={
        "file": ("portfolio.pdf", io.BytesIO(_pdf_bytes(tmp_path)), "application/pdf")})
    assert resp.status_code == 200, resp.text
    return resp.json()["file_id"]


# ---------------------------------------------------------------- 完整流程

async def test_full_flow_upload_compress_review_resume_download(client, tmp_path):
    file_id = await _upload(client, tmp_path)

    # 触发压缩（前半段）
    resp = await client.post("/api/v1/compress", json={
        "file_id": file_id, "target_size_mb": 10.0, "compression_target": "screen"})
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]

    # 查询 → AWAITING_REVIEW（eager 下已完成前半段）
    resp = await client.get(f"/api/v1/tasks/{task_id}")
    assert resp.status_code == 200
    body = resp.json()
    assert body["state"] == "AWAITING_REVIEW"
    assert len(body["meta"]["classified"]) == 2
    session_id = body["meta"]["session_id"]

    # review 后继续（后半段）
    resp = await client.post(f"/api/v1/tasks/{task_id}/resume", json={
        "session_id": session_id,
        "modified_classified": body["meta"]["classified"],
        "user_prefs": {"file_id": file_id, "target_size_mb": 10.0,
                       "compression_target": "screen"},
    })
    assert resp.status_code == 200
    resume_task_id = resp.json()["task_id"]

    resp = await client.get(f"/api/v1/tasks/{resume_task_id}")
    body = resp.json()
    assert body["state"] == "SUCCESS"
    assert body["meta"]["tier_used"] in ("in_place", "hybrid", "full_raster")
    assert body["meta"]["final_size_mb"] > 0
    download_id = body["meta"]["download_id"]

    # 下载产物
    resp = await client.get(f"/api/v1/download/{download_id}")
    assert resp.status_code == 200
    assert resp.headers["content-type"] == "application/pdf"
    assert "attachment" in resp.headers["content-disposition"]
    assert resp.content.startswith(b"%PDF-")


# ---------------------------------------------------------------- 上传三关

async def test_upload_rejects_wrong_content_type(client, tmp_path):
    resp = await client.post("/api/v1/upload", files={
        "file": ("x.txt", io.BytesIO(b"plain text"), "text/plain")})
    assert resp.status_code == 415


async def test_upload_rejects_missing_pdf_magic(client, tmp_path):
    resp = await client.post("/api/v1/upload", files={
        "file": ("fake.pdf", io.BytesIO(b"not a pdf at all"), "application/pdf")})
    assert resp.status_code == 415


async def test_upload_rejects_oversize(client, tmp_path, monkeypatch):
    monkeypatch.setenv("API__MAX_UPLOAD_MB", "1")
    get_settings.cache_clear()
    resp = await client.post("/api/v1/upload", files={
        "file": ("big.pdf", io.BytesIO(b"%PDF-" + b"0" * (2 * 1024 * 1024)),
                 "application/pdf")})
    assert resp.status_code == 413


# ---------------------------------------------------------------- 错误映射

async def test_compress_unknown_file_id_404(client):
    resp = await client.post("/api/v1/compress", json={
        "file_id": str(uuid.uuid4()), "target_size_mb": 10.0,
        "compression_target": "screen"})
    assert resp.status_code == 404


async def test_unknown_task_reports_pending(client):
    resp = await client.get(f"/api/v1/tasks/{uuid.uuid4()}")
    assert resp.status_code == 200
    assert resp.json()["state"] == "PENDING"


async def test_resume_expired_session_maps_to_410(client, tmp_path):
    resp = await client.post(f"/api/v1/tasks/{uuid.uuid4()}/resume", json={
        "session_id": str(uuid.uuid4()),
        "modified_classified": [],
        "user_prefs": {"file_id": "x", "target_size_mb": 10.0,
                       "compression_target": "screen"},
    })
    # eager + propagates=False：SESSION_EXPIRED 在 delay 内被捕获进结果后端，
    # resume 端点本身仍 200 返回 task_id，随后查询该任务 → FAILURE + 错误体
    assert resp.status_code == 200
    task_id = resp.json()["task_id"]
    status = (await client.get(f"/api/v1/tasks/{task_id}")).json()
    assert status["state"] == "FAILURE"
    assert status["meta"]["error"]["code"] == "SESSION_EXPIRED"


async def test_download_unknown_id_404(client):
    resp = await client.get(f"/api/v1/download/{uuid.uuid4()}")
    assert resp.status_code == 404


async def test_failure_state_carries_phase_error_body(client, tmp_path):
    """非 PDF 内容混过上传关（魔数对但内部损坏）→ split INVALID_PDF →
    任务 FAILURE，meta 携带完整 PhaseErrorData。"""
    corrupt = b"%PDF-1.7 garbage garbage"
    file_id = get_storage().save_upload(corrupt, "pdf")
    resp = await client.post("/api/v1/compress", json={
        "file_id": file_id, "target_size_mb": 10.0, "compression_target": "screen"})
    task_id = resp.json()["task_id"]
    status = (await client.get(f"/api/v1/tasks/{task_id}")).json()
    assert status["state"] == "FAILURE"
    assert status["meta"]["error"]["code"] == "INVALID_PDF"
    assert status["meta"]["error"]["phase"] == "split"


# ---------------------------------------------------------------- health

async def test_health_reports_probes(client):
    resp = await client.get("/api/v1/health")
    assert resp.status_code == 200
    body = resp.json()
    assert body["storage_writable"] is True
    assert isinstance(body["redis_connected"], bool)
    assert body["status"] in ("ok", "degraded")
