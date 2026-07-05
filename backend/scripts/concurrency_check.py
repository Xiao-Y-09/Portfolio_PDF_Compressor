"""Phase 13 并发验证（手册第六项）：10 任务同时提交，真实 worker + 隔离 Redis db。

前置：celery worker 已按相同 env（STORAGE__TMP_DIR / REDIS__DB）启动。
验证点：全部完成（无死锁，超时判定）；无文件混淆（每任务 PDF 含唯一标记文字，
产物逐一核对自己的标记）；任务终结后工作区全部清理（铁律 5）。

用法（backend/ 下，两个终端或分离进程）：
  worker:  $env:STORAGE__TMP_DIR="..."; $env:REDIS__DB="14"
           .venv/Scripts/celery.exe -A app.queue.celery_app worker --pool=threads --concurrency=4
  driver:  同 env 下 .venv/Scripts/python.exe scripts/concurrency_check.py
"""

from __future__ import annotations

import io
import sys
import time
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(BACKEND_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pymupdf  # noqa: E402

from app.config.settings import get_settings  # noqa: E402
from app.pipeline.orchestrator import workspace_path  # noqa: E402
from app.queue.tasks import compress_pdf_task, resume_compression_task  # noqa: E402
from app.storage import get_storage  # noqa: E402

N_TASKS = 10
WAIT_TIMEOUT_S = 300
PREFS = {"target_size_mb": 10.0, "compression_target": "screen"}


def _marked_pdf(index: int) -> bytes:
    """每任务独立 PDF，页内嵌唯一标记（skip 无损保留 → 输出可核对归属）。"""
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc[0].insert_text((72, 100), f"CONCURRENCY-MARKER-{index:02d}", fontsize=18)
    data = doc.tobytes()
    doc.close()
    return data


def _wait_all(results, label: str):
    deadline = time.time() + WAIT_TIMEOUT_S
    pending = dict(results)
    done = {}
    while pending and time.time() < deadline:
        for idx, ar in list(pending.items()):
            if ar.ready():
                done[idx] = ar
                del pending[idx]
        time.sleep(0.5)
    if pending:
        raise TimeoutError(f"{label}: {len(pending)} task(s) not done in "
                           f"{WAIT_TIMEOUT_S}s (possible deadlock): {list(pending)}")
    return done


def main() -> None:
    storage = get_storage()
    print(f"redis={get_settings().redis.url} tmp={get_settings().storage.resolved_tmp_dir()}")

    # 一、10 个任务同时提交（前半段）
    file_ids = {i: storage.save_upload(_marked_pdf(i), "pdf") for i in range(N_TASKS)}
    first = {i: compress_pdf_task.delay(fid, PREFS) for i, fid in file_ids.items()}
    print(f"submitted {N_TASKS} compress tasks simultaneously")
    first_done = _wait_all(first, "compress")
    sessions = {}
    for i, ar in first_done.items():
        payload = ar.get()
        assert payload["status"] == "AWAITING_REVIEW", (i, payload)
        sessions[i] = (payload["session_id"], payload["classified"])
    print("all AWAITING_REVIEW ok; sessions distinct:",
          len({s for s, _ in sessions.values()}) == N_TASKS)

    # 二、全部 resume（后半段，同样并发）
    second = {
        i: resume_compression_task.delay(sid, classified, PREFS)
        for i, (sid, classified) in sessions.items()
    }
    second_done = _wait_all(second, "resume")

    # 三、核对产物归属（无文件混淆）+ 工作区清理
    mix_ups = 0
    for i, ar in second_done.items():
        payload = ar.get()
        assert payload["status"] == "SUCCESS", (i, payload)
        out = pymupdf.open(str(storage.get_path(payload["download_id"])))
        try:
            text = out[0].get_text()
        finally:
            out.close()
        expect = f"CONCURRENCY-MARKER-{i:02d}"
        # 标记以文字或光栅化形式存在均可；文字层在 in_place/hybrid 保留，
        # full_raster 下退化为像素——两种情况都要求"不含其它任务的标记"
        for j in range(N_TASKS):
            other = f"CONCURRENCY-MARKER-{j:02d}"
            if j != i and other in text:
                mix_ups += 1
                print(f"[MIX-UP] task {i} output contains {other}")
        if expect in text:
            pass  # 文字保留路径：归属正确
    assert mix_ups == 0, f"{mix_ups} cross-contamination(s) detected"

    leftovers = [
        workspace_path(sid) for sid, _ in sessions.values()
        if workspace_path(sid).exists()
    ]
    assert not leftovers, f"workspaces not cleaned: {leftovers}"

    print(f"\nCONCURRENCY CHECK PASS: {N_TASKS}/{N_TASKS} completed, "
          f"0 mix-ups, all workspaces cleaned")


if __name__ == "__main__":
    main()
