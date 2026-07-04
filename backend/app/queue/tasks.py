"""任务定义。

Phase 2 阶段只有 stub：compress_pdf_task 睡 3 秒返回 dummy result，
用于验证 Celery + Redis 链路。真实流水线逻辑在 Phase 11 替换填入。
"""

from __future__ import annotations

import time

from app.queue.celery_app import celery_app


@celery_app.task(bind=True, name="app.queue.tasks.compress_pdf_task")
def compress_pdf_task(self, file_id: str, options: dict) -> dict:
    """[STUB] 完整压缩流水线任务——Phase 11 填入真实逻辑。"""
    self.update_state(state="PROGRESS", meta={"stage": "stub", "percent": 0.5})
    time.sleep(3)
    return {"status": "STUB_OK", "file_id": file_id, "options": options}
