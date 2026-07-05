"""Phase 11：真实压缩流水线任务，替换 Phase 2 的 stub。

Celery 外壳职责（编排逻辑本身在 app/pipeline/orchestrator.py，本文件不重复）：
- 进度回调绑定：self.update_state(state="PROGRESS", meta={stage, percent})
- review session 指针：Redis key "session:{task_id}" → {tmp_workspace}，
  TTL = settings.session.review_session_ttl（工作区目录名即 task_id，见
  orchestrator.workspace_path，Redis 只做存在性 + TTL 判断，不是唯一真源）
- 任务终结后清理：resume_compression_task 无论成功/失败都清空工作区 +
  删除 Redis session（铁律 5，《系统架构设计.md》§5.3 三重保障之一）
"""

from __future__ import annotations

import json
import logging
import os

import redis as redis_lib

from app.config.settings import get_settings
from app.contracts import ClassifiedPage, PhaseError, PipelineContext, UserPreferences
from app.pipeline.orchestrator import (
    TIER_WARNINGS,
    cleanup_workspace,
    load_session,
    resume_after_review,
    run_until_classify,
    save_session,
    workspace_path,
)
from app.queue.celery_app import celery_app
from app.storage import get_storage

logger = logging.getLogger("app.queue.tasks")

def _redis() -> redis_lib.Redis:
    """不缓存单例：get_settings() 在测试中可被 cache_clear() 重新配置
    （不同 db 隔离），缓存客户端会绑定到过期的连接串；redis-py 的连接
    建立本身是惰性的，每次构造成本可忽略。"""
    return redis_lib.Redis.from_url(get_settings().redis.url, decode_responses=True)


def _session_key(task_id: str) -> str:
    return f"session:{task_id}"


@celery_app.task(bind=True, name="app.queue.tasks.compress_pdf_task")
def compress_pdf_task(self, file_id: str, user_prefs_dict: dict) -> dict:
    """完整流水线任务，从 Phase 4 跑到分类完成，随后暂停等待用户 review。"""
    settings = get_settings()
    task_id = self.request.id or "eager"
    workspace = workspace_path(task_id)
    workspace.mkdir(parents=True, exist_ok=True)
    ctx = PipelineContext(file_id=file_id, tmp_workspace=str(workspace))
    user_prefs = UserPreferences(**user_prefs_dict)

    def on_progress(stage: str, percent: float) -> None:
        self.update_state(state="PROGRESS", meta={"stage": stage, "percent": percent})
        # stage-metric：CloudWatch 指标过滤器锚点（Phase 15，2026-07-05）——
        # 相邻两条的时间差即阶段耗时，Logs Insights 可聚合
        logger.info("stage-metric task=%s stage=%s percent=%.2f",
                    task_id, stage, percent)

    try:
        classified, session_data = run_until_classify(file_id, user_prefs, ctx, on_progress)
        save_session(session_data)
        _redis().setex(
            _session_key(task_id),
            settings.session.review_session_ttl,
            json.dumps({"tmp_workspace": str(workspace)}),
        )
        return {
            "status": "AWAITING_REVIEW",
            "classified": [c.model_dump() for c in classified],
            "session_id": task_id,
        }
    except Exception:
        # 失败即清理（铁律 5）。不再手动 update_state(FAILURE)：JSON 结果后端的
        # FAILURE 结果必须是 Celery 自己序列化的异常（exc_type/exc_message），
        # 裸 dict 形状会让 AsyncResult 读回直接崩（Phase 12 实测修订，披露性
        # 偏离手册伪码——PhaseError.args 已按构造签名对齐，raise 后 API 层可
        # 无损重建，含 diagnostics）
        cleanup_workspace(str(workspace))
        raise


@celery_app.task(bind=True, name="app.queue.tasks.resume_compression_task")
def resume_compression_task(
    self, session_id: str, modified_classified: list, user_prefs_dict: dict
) -> dict:
    """用户 review 之后继续的任务：Phase D0 → 收敛循环 → Phase F → 归档下载。"""
    import time as _time
    _t0 = _time.time()
    raw = _redis().get(_session_key(session_id))
    if raw is None:
        raise PhaseError(
            phase="orchestrator", code="SESSION_EXPIRED",
            message=f"review session {session_id} 不存在或已过期，请重新上传",
            recoverable=False,
        )
    workspace = json.loads(raw)["tmp_workspace"]
    session_data = load_session(workspace)
    user_prefs = UserPreferences(**user_prefs_dict)
    classified = [ClassifiedPage(**c) for c in modified_classified]

    def on_progress(stage: str, percent: float) -> None:
        self.update_state(state="PROGRESS", meta={"stage": stage, "percent": percent})

    try:
        output_path = resume_after_review(session_data, classified, user_prefs, on_progress)
        storage = get_storage()
        download_id = storage.save_from_path(output_path)
        # tier_used：被接受 Tier 即收敛轨迹最后一步所属层级（成功路径必有轨迹）
        history = session_data.ctx.convergence_history
        tier_used = history[-1].tier if history else "in_place"
        # task-metrics：CloudWatch 业务指标锚点（Phase 15）——tier 分布/时长/轮数
        logger.info(
            "task-metrics session=%s tier_used=%s final_mb=%.2f rounds=%d elapsed_s=%.1f",
            session_id, tier_used, os.path.getsize(output_path) / 1e6,
            len(history), _time.time() - _t0,
        )
        return {
            "status": "SUCCESS",
            "download_id": download_id,
            "final_size_mb": os.path.getsize(output_path) / 1e6,
            "tier_used": tier_used,
            "warning": TIER_WARNINGS.get(tier_used),  # in_place → None
        }
    finally:
        # 无论成败：清理工作区 + 删除 Redis session（铁律 5）。失败路径同上，
        # 直接 raise 交 Celery 序列化，不手动 update_state(FAILURE)
        cleanup_workspace(workspace)
        _redis().delete(_session_key(session_id))
