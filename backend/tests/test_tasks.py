"""Phase 11 Celery 任务链路测试（eager 模式，需本机 Redis 可达）。

覆盖手册 Phase 11 四项验证点：完整链路（上传→AWAITING_REVIEW→resume→SUCCESS）、
进度回调阶段、review 修改后继续、session 超时/不存在的处理。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pymupdf
import pytest
import redis as redis_lib
from celery.app.task import Task

from app.config.settings import get_settings
from app.contracts import PhaseError
from app.pipeline.orchestrator import workspace_path
from app.queue.celery_app import celery_app
from app.storage import get_storage

from test_assemble import build_pdf_with_images

pytestmark = pytest.mark.filterwarnings("ignore::DeprecationWarning")


@pytest.fixture(autouse=True)
def _isolated_env(tmp_path, monkeypatch):
    """隔离 storage.tmp_dir 与 Redis db（用独立 db 15，避免污染开发用 db 0）。"""
    monkeypatch.setenv("STORAGE__TMP_DIR", str(tmp_path / "uploads"))
    monkeypatch.setenv("REDIS__DB", "15")
    get_settings.cache_clear()
    yield
    # 清掉本轮在 db15 留下的任何 key（正常路径已自行删除，这里兜底）
    try:
        redis_lib.Redis.from_url(get_settings().redis.url).flushdb()
    except Exception:
        pass
    get_settings.cache_clear()


@pytest.fixture(autouse=True)
def _eager_celery():
    old_eager, old_propagate = celery_app.conf.task_always_eager, celery_app.conf.task_eager_propagates
    celery_app.conf.task_always_eager = True
    celery_app.conf.task_eager_propagates = True
    yield
    celery_app.conf.task_always_eager = old_eager
    celery_app.conf.task_eager_propagates = old_propagate


def _reimport_tasks():
    """tasks.py 的 _redis_client 是模块级单例，且 celery task 在 import 时已绑定
    到 celery_app；env 隔离在每个测试重设，这里直接复用同一模块即可（每次都会
    通过 get_settings() 重新取 url，不受单例影响——见 tasks._redis()）。"""
    from app.queue import tasks
    return tasks


def _make_prefs(target_mb: float = 10.0, compression_target: str = "screen") -> dict:
    return {"target_size_mb": target_mb, "compression_target": compression_target}


def _upload_small_pdf(tmp_path: Path, n_pages: int = 2) -> str:
    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=n_pages)
    return get_storage().save_upload(src.read_bytes(), "pdf")


# ---------------------------------------------------------------- 完整链路

def test_full_chain_upload_to_download(tmp_path):
    tasks = _reimport_tasks()
    file_id = _upload_small_pdf(tmp_path)

    first = tasks.compress_pdf_task.apply(args=(file_id, _make_prefs())).get()
    assert first["status"] == "AWAITING_REVIEW"
    assert len(first["classified"]) == 2
    session_id = first["session_id"]

    ws = workspace_path(session_id)
    assert ws.is_dir()
    assert (ws / "session.pickle").is_file()

    r = redis_lib.Redis.from_url(get_settings().redis.url, decode_responses=True)
    assert r.get(f"session:{session_id}") is not None

    second = tasks.resume_compression_task.apply(
        args=(session_id, first["classified"], _make_prefs())
    ).get()

    assert second["status"] == "SUCCESS"
    assert second["final_size_mb"] > 0
    assert "download_id" in second
    # Tier 系统：理想路径首层通过 → 无警告（清单条目"tier_used ≠ in_place 时携带警告"反面）
    assert second["tier_used"] == "in_place"
    assert second["warning"] is None

    # 任务终结：工作区清理 + redis session 删除
    assert not ws.exists()
    assert r.get(f"session:{session_id}") is None

    # 下载产物确实是有效 PDF
    storage = get_storage()
    downloaded = storage.get_path(second["download_id"])
    out = pymupdf.open(str(downloaded))
    try:
        assert out.page_count == 2
    finally:
        out.close()


def test_progress_callback_reports_expected_stages(tmp_path, monkeypatch):
    calls = []
    original = Task.update_state

    def spy(self, *args, **kwargs):
        calls.append(kwargs.get("meta"))
        return original(self, *args, **kwargs)

    monkeypatch.setattr(Task, "update_state", spy)

    tasks = _reimport_tasks()
    file_id = _upload_small_pdf(tmp_path)
    first = tasks.compress_pdf_task.apply(args=(file_id, _make_prefs())).get()

    stages = [c["stage"] for c in calls if c]
    assert stages == ["split", "extract", "classify"]

    calls.clear()
    tasks.resume_compression_task.apply(
        args=(first["session_id"], first["classified"], _make_prefs())
    ).get()
    resume_stages = [c["stage"] for c in calls if c]
    assert resume_stages[0] == "preprocess"
    assert resume_stages[-1] == "assemble"
    assert "converge" in resume_stages


# ---------------------------------------------------------------- review 修改生效

def test_resume_honors_review_modified_overrides(tmp_path):
    tasks = _reimport_tasks()
    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=2)
    src_doc = pymupdf.open(str(src))
    src_img_bytes = src_doc.extract_image(src_doc[0].get_images()[0][0])["image"]
    src_doc.close()
    file_id = get_storage().save_upload(src.read_bytes(), "pdf")

    first = tasks.compress_pdf_task.apply(
        args=(file_id, _make_prefs(target_mb=1.0, compression_target="email"))
    ).get()

    prefs_with_skip = _make_prefs(target_mb=1.0, compression_target="email")
    prefs_with_skip["per_page_overrides"] = [{"page_number": 1, "action": "skip"}]
    second = tasks.resume_compression_task.apply(
        args=(first["session_id"], first["classified"], prefs_with_skip)
    ).get()
    assert second["status"] == "SUCCESS"

    storage = get_storage()
    out = pymupdf.open(str(storage.get_path(second["download_id"])))
    try:
        out_img_bytes = out.extract_image(out[0].get_images()[0][0])["image"]
        assert out_img_bytes == src_img_bytes  # skip 页字节不变
    finally:
        out.close()


def test_task_reports_tier_used_and_warning_on_escalation(tmp_path, monkeypatch):
    """清单条目"tier_used ≠ in_place 时结果携带警告文案"：降级到 full_raster
    后任务 SUCCESS 载荷带 tier_used + 非空 warning（Phase 12 API 将原样透传）。"""
    import app.pipeline.orchestrator as orch

    tasks = _reimport_tasks()
    file_id = _upload_small_pdf(tmp_path)
    first = tasks.compress_pdf_task.apply(args=(file_id, _make_prefs())).get()

    target_bytes = 10 * 1024 * 1024
    sizes = iter([int(target_bytes * 1.5),   # in_place 边界：超标
                  int(target_bytes * 0.5)])  # full_raster 边界：达标（hybrid 无资格被跳过）
    monkeypatch.setattr(orch, "_actual_size", lambda path: next(sizes))

    second = tasks.resume_compression_task.apply(
        args=(first["session_id"], first["classified"], _make_prefs())
    ).get()

    assert second["status"] == "SUCCESS"
    assert second["tier_used"] == "full_raster"
    assert second["warning"] is not None and "极限压缩" in second["warning"]


# ---------------------------------------------------------------- 异常路径

def test_resume_with_unknown_session_raises_session_expired(tmp_path):
    tasks = _reimport_tasks()
    bogus_session_id = str(uuid.uuid4())

    with pytest.raises(PhaseError) as exc_info:
        tasks.resume_compression_task.apply(
            args=(bogus_session_id, [], _make_prefs())
        ).get()
    assert exc_info.value.code == "SESSION_EXPIRED"


def test_convergence_failed_surfaces_as_failure_and_still_cleans_up(tmp_path, monkeypatch):
    import app.pipeline.orchestrator as orch
    from app.contracts import CompressionResult

    tasks = _reimport_tasks()
    file_id = _upload_small_pdf(tmp_path)
    first = tasks.compress_pdf_task.apply(args=(file_id, _make_prefs())).get()
    session_id = first["session_id"]
    ws = workspace_path(session_id)

    def fake_execute(plan, pages, ctx, config):
        return CompressionResult(
            total_raster_bytes=int(10 * 1024 * 1024 * 1.3),
            per_image_results=[], round_number=plan.round_number,
            base_aggressiveness=plan.base_aggressiveness,
            tier_used=plan.tier,
        )

    monkeypatch.setattr(orch, "execute_compression", fake_execute)
    # Tier 边界真实判定也固定为超标 → 三级全部耗尽
    monkeypatch.setattr(orch, "_actual_size",
                        lambda path: int(10 * 1024 * 1024 * 1.3))

    with pytest.raises(PhaseError) as exc_info:
        tasks.resume_compression_task.apply(
            args=(session_id, first["classified"], _make_prefs())
        ).get()
    assert exc_info.value.code == "CONVERGENCE_FAILED"
    # 诊断随错误进 Celery meta（e.model_dump() 含 diagnostics）
    assert exc_info.value.data.diagnostics is not None
    assert exc_info.value.data.diagnostics.tier_reached == "full_raster"

    # finally 分支保证：即使失败也清理工作区 + redis session
    assert not ws.exists()
    r = redis_lib.Redis.from_url(get_settings().redis.url, decode_responses=True)
    assert r.get(f"session:{session_id}") is None


def test_session_ttl_expiry_then_resume_fails_and_workspace_sweepable(tmp_path):
    """边界（Phase 13）：review 超时——Redis TTL 到期（用删 key 模拟触发）后
    resume → SESSION_EXPIRED；遗留工作区可被 cleanup_stale_workspaces 扫掉
    （max_age=0 模拟 30 分钟已过）。"""
    from app.pipeline.orchestrator import cleanup_stale_workspaces

    tasks = _reimport_tasks()
    file_id = _upload_small_pdf(tmp_path)
    first = tasks.compress_pdf_task.apply(args=(file_id, _make_prefs())).get()
    session_id = first["session_id"]
    ws = workspace_path(session_id)
    assert ws.is_dir()

    r = redis_lib.Redis.from_url(get_settings().redis.url, decode_responses=True)
    assert r.ttl(f"session:{session_id}") > 0     # TTL 已按 config 设置
    r.delete(f"session:{session_id}")             # 模拟 TTL 到期

    with pytest.raises(PhaseError) as exc_info:
        tasks.resume_compression_task.apply(
            args=(session_id, first["classified"], _make_prefs())
        ).get()
    assert exc_info.value.code == "SESSION_EXPIRED"

    removed = cleanup_stale_workspaces(workspace_root=ws.parent, max_age_seconds=0)
    assert removed >= 1 and not ws.exists()       # 超时扫描兜底（§4.4）


def test_concurrent_same_file_id_requests_are_isolated(tmp_path):
    """边界（Phase 13）：同一 file_id 的多个 compress 请求——工作区按 task_id
    隔离（orchestrator.workspace_path 设计），两任务全链路互不污染。"""
    tasks = _reimport_tasks()
    file_id = _upload_small_pdf(tmp_path)

    first_a = tasks.compress_pdf_task.apply(args=(file_id, _make_prefs())).get()
    first_b = tasks.compress_pdf_task.apply(args=(file_id, _make_prefs())).get()
    assert first_a["session_id"] != first_b["session_id"]

    results = []
    for first in (first_a, first_b):
        second = tasks.resume_compression_task.apply(
            args=(first["session_id"], first["classified"], _make_prefs())
        ).get()
        assert second["status"] == "SUCCESS"
        results.append(second)

    assert results[0]["download_id"] != results[1]["download_id"]
    storage = get_storage()
    for second in results:  # 两份产物各自有效
        out = pymupdf.open(str(storage.get_path(second["download_id"])))
        try:
            assert out.page_count == 2
        finally:
            out.close()


def test_compress_task_cleans_workspace_on_split_failure(tmp_path):
    tasks = _reimport_tasks()
    non_pdf_id = get_storage().save_upload(b"not a pdf at all", "pdf")
    task_id = str(uuid.uuid4())
    ws = workspace_path(task_id)

    # task_eager_propagates=True 下 .apply() 本身就抛出（不必等 .get()）
    with pytest.raises(PhaseError) as exc_info:
        tasks.compress_pdf_task.apply(args=(non_pdf_id, _make_prefs()), task_id=task_id)
    assert exc_info.value.code == "INVALID_PDF"
    assert not ws.exists()
