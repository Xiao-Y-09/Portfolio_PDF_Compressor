"""Phase 11：流水线编排 + 收敛循环（运行别名跨 A-F，编排层本身无运行别名）。

职责边界：本文件只负责跑 Phase A-F 的顺序编排与《压缩决策引擎.md》§8 的
二分搜索收敛循环，不导入 celery/redis——保持可在无 Celery/Redis 的环境下
直接单元测试。Celery 任务外壳（进度回调绑定、Redis session 指针、下载归档）
在 app/queue/tasks.py。

会话断点机制（《系统架构设计.md》§4）：
- run_until_classify() 跑 Phase A→B→C，产出 SessionData（不落盘——落盘时机
  由调用方 save_session() 决定，便于测试直接检查内存对象）。
- resume_after_review() 从 SessionData 恢复，跑 Phase D0→收敛循环→F。
- SessionData 是工作区内部工件（非跨 Phase 契约，不进 app/contracts/）——
  与 extract.py 的 xref_map sidecar 同性质，Producer=run_until_classify，
  Consumer=resume_after_review，生命周期仅限一次任务。

收敛循环铁律 7 落地（《压缩决策引擎.md》§8.1/§8.4）：
- 每轮 gap_ratio ∈ [-tolerance, 0] → 达标（略保守），跳出接受
- 每轮 gap_ratio ∈ (0, tolerance/2] → 微超标，跳出接受
- 5 轮耗尽仍未跳出：gap_ratio > 0（仍超标）→ CONVERGENCE_FAILED；
  gap_ratio ≤ 0（浪费过多）→ 接受（宁可小一点也不超标）
- 任何情况下不允许静默输出超出目标 15% 以上的文件

工作区生命周期（铁律 5 + 《系统架构设计.md》§5.3）：
cleanup_workspace() 供任务成功/失败后调用；cleanup_stale_workspaces() 是
review 超时的兜底扫描——按 session.pickle mtime 判断过期，不依赖 Redis
（与 storage/local.py 的 cleanup_expired 同一防御性设计原则）。
"""

from __future__ import annotations

import logging
import pickle
import shutil
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Optional, Tuple

from pydantic import BaseModel

from app.config.settings import CompressionConfig, get_settings
from app.contracts import (
    COMPRESSION_TIERS,
    ClassifiedPage,
    CompressionPlan,
    CompressionResult,
    CompressionTier,
    ConvergenceDiagnostics,
    ConvergenceStep,
    FontUsage,
    PageElements,
    PhaseError,
    PipelineContext,
    UserPreferences,
)
from app.pipeline.assemble import assemble_pdf
from app.pipeline.classify import classify_pages
from app.pipeline.compress import execute_compression
from app.pipeline.decide import decide_compression, select_whole_pages
from app.pipeline.extract import extract_elements
from app.pipeline.preprocess import preprocess
from app.pipeline.split import split_pdf
from app.storage import get_storage

logger = logging.getLogger("app.pipeline.orchestrator")

WORKSPACES_DIR = "workspaces"    # tmp_dir 下的任务工作区子目录（结构常量，非可调参数）
SESSION_REF = "session.pickle"   # 相对 tmp_workspace 的 review 断点会话存档

# tier_used ≠ in_place 时任务结果携带的用户警告文案（《压缩决策引擎.md》§8.5；
# 任务层输出约定见《数据契约.md》第 7 章尾注）
TIER_WARNINGS: Dict[str, str] = {
    "hybrid": "部分页面已光栅化以达到目标大小（矢量/文字变为图片）。"
              "如需保留这些页面的原始质量，可选择更大的目标重试。",
    "full_raster": "已使用极限压缩，全部页面已光栅化，部分文字可能模糊。"
                   "如需更好质量，可选择更大的目标重试。",
}

ProgressCallback = Callable[[str, float], None]


def _noop_progress(_stage: str, _percent: float) -> None:
    pass


def _actual_size(path: str) -> int:
    """Tier 边界判定用的真实文件大小（裁决 1）。独立成函数便于测试注入。"""
    return Path(path).stat().st_size


THUMBNAIL_DPI = 40  # review 缩略图渲染 DPI（低清即可，结构常量非压缩参数）


def render_page_thumbnail(tmp_workspace: str, page_number: int) -> bytes:
    """review 界面页面缩略图（Phase 14 规格倒逼的 Phase 12 追加路由之渲染侧，
    2026-07-05）。读 AWAITING_REVIEW 期间存活的工作区 source.pdf，渲染单页 PNG。
    页号越界/文件缺失 → ValueError（API 层转 404）。"""
    import pymupdf

    source = Path(tmp_workspace) / "source.pdf"
    if not source.is_file():
        raise ValueError(f"source.pdf missing under {tmp_workspace}")
    doc = pymupdf.open(str(source))
    try:
        if not 1 <= page_number <= doc.page_count:
            raise ValueError(f"page {page_number} out of range 1..{doc.page_count}")
        zoom = THUMBNAIL_DPI / 72.0
        pix = doc[page_number - 1].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
        return pix.tobytes("png")
    finally:
        doc.close()


class SessionData(BaseModel):
    """review 断点会话状态（工作区内部工件，非契约，见模块 docstring）。"""

    ctx: PipelineContext
    pages: List[PageElements]
    fonts_used: Dict[str, FontUsage]
    metadata: Dict[str, Any]
    classified: List[ClassifiedPage]


# ---------------------------------------------------------------- 工作区 + 会话存取

def workspace_path(task_id: str) -> Path:
    """task_id（= Celery session_id）→ 工作区绝对路径。目录名即 task_id，
    定位会话无需额外映射表；Redis 只承担存在性 + TTL 判断。"""
    return get_settings().storage.resolved_tmp_dir() / WORKSPACES_DIR / task_id


def save_session(session_data: SessionData) -> None:
    ref = session_data.ctx.resolve_ref(SESSION_REF)
    ref.write_bytes(pickle.dumps(session_data))


def load_session(tmp_workspace: str) -> SessionData:
    path = Path(tmp_workspace) / SESSION_REF
    if not path.is_file():
        raise PhaseError(
            phase="orchestrator", code="SESSION_EXPIRED",
            message=f"session file missing under {tmp_workspace}（已过期或已被清理）",
            recoverable=False,
        )
    return pickle.loads(path.read_bytes())


def cleanup_workspace(tmp_workspace: str) -> None:
    """铁律 5 兜底：任务终结（成功/失败）后清空工作区目录。幂等。"""
    shutil.rmtree(tmp_workspace, ignore_errors=True)


def cleanup_stale_workspaces(
    workspace_root: Optional[Path] = None, max_age_seconds: Optional[int] = None
) -> int:
    """review 超时扫描（《系统架构设计.md》§4.4）：按 session.pickle mtime
    判断过期，不依赖 Redis TTL 是否真正触发——与 storage/local.py 的
    cleanup_expired 同一防御性设计（外部记录源故障也能兜底清理）。"""
    settings = get_settings()
    root = workspace_root or (settings.storage.resolved_tmp_dir() / WORKSPACES_DIR)
    max_age = (
        max_age_seconds if max_age_seconds is not None
        else settings.session.review_session_ttl
    )
    if not root.is_dir():
        return 0
    deadline = time.time() - max_age
    removed = 0
    for entry in root.iterdir():
        if not entry.is_dir():
            continue
        marker = entry / SESSION_REF
        try:
            mtime = marker.stat().st_mtime if marker.is_file() else entry.stat().st_mtime
        except OSError:
            continue
        if mtime < deadline:
            shutil.rmtree(entry, ignore_errors=True)
            removed += 1
    if removed:
        logger.info("cleanup_stale_workspaces: removed %d stale workspace(s)", removed)
    return removed


# ---------------------------------------------------------------- 前半段：Phase A→B→C

def run_until_classify(
    file_id: str,
    user_prefs: UserPreferences,
    ctx: PipelineContext,
    on_progress: Optional[ProgressCallback] = None,
) -> Tuple[List[ClassifiedPage], SessionData]:
    """Phase A（split）→ B（extract）→ C（classify），随后暂停等待用户 review。"""
    progress = on_progress or _noop_progress
    storage = get_storage()
    source_path = str(storage.get_path(file_id))

    raw_pages = split_pdf(source_path, ctx)
    progress("split", 0.10)

    pages, fonts_used, metadata = extract_elements(raw_pages, source_path, ctx)
    progress("extract", 0.30)

    classified = classify_pages(pages)
    progress("classify", 0.40)

    session_data = SessionData(
        ctx=ctx, pages=pages, fonts_used=fonts_used, metadata=metadata,
        classified=classified,
    )
    return classified, session_data


# ---------------------------------------------------------------- 后半段：D0 + 收敛循环 + F

def resume_after_review(
    session_data: SessionData,
    modified_classified: List[ClassifiedPage],
    user_prefs: UserPreferences,
    on_progress: Optional[ProgressCallback] = None,
    config: Optional[CompressionConfig] = None,
) -> str:
    """Phase D0（preprocess，只跑一次）→ 分级降级状态机（§8.5）→ 返回 output.pdf。

    每个 Tier 内部跑完整收敛循环（环内用估算求快）；Tier 边界用真实 assemble
    大小判定接受/降级（裁决 1）。被接受 Tier 的 assemble 产物即最终输出。
    全部 Tier 耗尽仍超标 → CONVERGENCE_FAILED + ConvergenceDiagnostics（§8.4）。
    """
    progress = on_progress or _noop_progress
    config = config or get_settings().compression
    ctx = session_data.ctx
    pages = session_data.pages

    pre = preprocess(pages, session_data.fonts_used, session_data.metadata, user_prefs, ctx)
    progress("preprocess", 0.50)

    target_bytes = user_prefs.target_size_mb * 1024 * 1024
    best_actual: Optional[int] = None

    for tier in COMPRESSION_TIERS:
        if tier == "hybrid":
            # 选页为空集（无一页有资格）→ 跳过 hybrid 直上 full_raster（§8.5），
            # 避免跑一轮与 in_place 完全等价的循环
            if not select_whole_pages(pages, pre, user_prefs, config, tier):
                logger.info("tier hybrid: no eligible page, skipping to full_raster")
                continue
        try:
            output_path, actual = _run_tier(
                tier, session_data, modified_classified, user_prefs, pre, config, progress,
            )
        except PhaseError as exc:
            if exc.code == "TARGET_TOO_SMALL":
                # Tier 系统下不再直达用户：作为降级信号进入下一 Tier（§3.5/§5.1 修订）
                logger.info("tier %s: TARGET_TOO_SMALL, escalating (%s)", tier, exc)
                continue
            raise
        actual_gap = (actual - target_bytes) / target_bytes
        best_actual = actual if best_actual is None else min(best_actual, actual)
        logger.info(
            "tier %s boundary: actual=%.2fMB target=%.2fMB gap=%.1f%%",
            tier, actual / 1e6, target_bytes / 1e6, actual_gap * 100,
        )
        if actual_gap <= config.convergence_tolerance / 2:
            # 达标（含任意欠冲——宁可小不超标）；被接受 Tier 的产物即最终输出
            progress("assemble", 1.0)
            return output_path

    # ---- 三级全部耗尽（§8.4：可行动诊断，用真实数字）----
    skip_pages = {o.page_number for o in user_prefs.per_page_overrides if o.action == "skip"}
    skip_raster_bytes = 0
    for page in pages:
        if page.page_number in skip_pages:
            seen: Dict[str, int] = {}
            for img in page.raster_images:
                seen[img.data_ref] = img.original_bytes
            skip_raster_bytes += sum(seen.values())
    if best_actual is None:
        # 极端：所有 Tier 都 TARGET_TOO_SMALL，从未产出装配件——用下限估算兜底
        # （典型场景：skip 页开销本身已超目标）
        best_actual = pre.total_fixed_bytes + skip_raster_bytes
    diagnostics = ConvergenceDiagnostics(
        tier_reached="full_raster",
        best_achievable_bytes=best_actual,
        skip_page_count=len(skip_pages),
        skip_raster_bytes=skip_raster_bytes,
        skip_budget_ratio=skip_raster_bytes / target_bytes,
    )
    raise PhaseError(
        phase="orchestrator", code="CONVERGENCE_FAILED",
        message=(
            f"已尝试三种压缩策略仍无法达到 {user_prefs.target_size_mb:.1f} MB。"
            f"可行动建议：减少 skip 页数量（当前 skip 了 {diagnostics.skip_page_count} 页，"
            f"占预算的 {diagnostics.skip_budget_ratio:.0%}）；"
            f"或选择更大目标（预估最优可达 {best_actual / 1e6:.1f} MB）。"
        ),
        recoverable=False,
        diagnostics=diagnostics,
    )


def _run_tier(
    tier: CompressionTier,
    session_data: SessionData,
    modified_classified: List[ClassifiedPage],
    user_prefs: UserPreferences,
    pre,
    config: CompressionConfig,
    progress: ProgressCallback,
) -> Tuple[str, int]:
    """单个 Tier：完整收敛循环（≤max_convergence_rounds 轮，环内用估算）→
    真实 assemble → stat。返回 (输出路径, 真实字节数)。每 Tier 独立起步
    （previous_result=None，round 从 1 开始）。"""
    ctx = session_data.ctx
    pages = session_data.pages
    target_bytes = user_prefs.target_size_mb * 1024 * 1024

    previous_result: Optional[CompressionResult] = None
    plan: Optional[CompressionPlan] = None
    result: Optional[CompressionResult] = None

    for round_number in range(1, config.max_convergence_rounds + 1):
        plan = decide_compression(
            modified_classified, pages, user_prefs, pre, previous_result,
            round_number, config, tier=tier,
        )
        result = execute_compression(plan, pages, ctx, config)

        # 修复 2026-07-05（用户裁决：架构一致性，《压缩决策引擎.md》§8.1 修订）：
        # 环内 gap 的 fixed 基准 = target − plan.raster_budget（decide 的本 Tier
        # floor，已随 Tier 扣除整页光栅化页的矢量/文字成本）。原先误用
        # pre.total_fixed_bytes（重建语义全量估算，含已光栅化页面的矢量成本），
        # est gap 恒虚高 → 每 Tier 被推到激进度上限，实测 6 场景全落 full_raster
        # 且欠冲 34-48%。Tier 边界的真实判定保证正确性；此处恢复环内寻优能力。
        fixed_basis = target_bytes - plan.raster_budget
        total_bytes = result.total_raster_bytes + fixed_basis
        gap_ratio = (total_bytes - target_bytes) / target_bytes
        ctx.convergence_history.append(ConvergenceStep(
            round_number=round_number,
            base_aggressiveness=result.base_aggressiveness,
            total_raster_bytes=result.total_raster_bytes,
            total_bytes=total_bytes,
            target_bytes=int(target_bytes),
            gap_ratio=gap_ratio,
            tier=tier,
        ))
        progress("converge", min(0.9, 0.5 + round_number * 0.08))
        logger.info(
            "tier %s round %d: est total=%.2fMB target=%.2fMB gap=%.1f%%",
            tier, round_number, total_bytes / 1e6, target_bytes / 1e6, gap_ratio * 100,
        )
        if -config.convergence_tolerance <= gap_ratio <= 0:
            break   # 达标（略保守）
        if 0 < gap_ratio <= config.convergence_tolerance / 2:
            break   # 微超标，接受
        previous_result = result

    output_path = assemble_pdf(
        pages, plan, result, pre, session_data.metadata, ctx
    )
    return output_path, _actual_size(output_path)
