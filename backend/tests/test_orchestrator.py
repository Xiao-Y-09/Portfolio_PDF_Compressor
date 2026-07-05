"""Phase 11 编排层测试——不涉及 Celery/Redis（那部分见 test_tasks.py）。

覆盖：run_until_classify/resume_after_review 的正确串联、session 存取往返、
收敛循环的三种结局（达标/微超标接受/CONVERGENCE_FAILED）、工作区清理。
"""

from __future__ import annotations

import time
import uuid
from pathlib import Path

import pymupdf
import pytest

from app.config.settings import CompressionConfig, get_settings
from app.contracts import PageOverride, PhaseError, PipelineContext, UserPreferences
from app.pipeline.orchestrator import (
    SESSION_REF,
    cleanup_stale_workspaces,
    cleanup_workspace,
    load_session,
    resume_after_review,
    run_until_classify,
    save_session,
)
from app.storage import get_storage

from test_assemble import _noise_jpeg, build_pdf_with_images

CFG = CompressionConfig()


@pytest.fixture(autouse=True)
def _isolated_storage(tmp_path, monkeypatch):
    """把 storage.tmp_dir 隔离到本测试的 tmp_path，避免污染项目真实 ./tmp。"""
    monkeypatch.setenv("STORAGE__TMP_DIR", str(tmp_path / "uploads"))
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


def _upload(src: Path) -> str:
    storage = get_storage()
    return storage.save_upload(src.read_bytes(), "pdf")


def _new_ctx(tmp_path: Path, suffix: str = "ws") -> PipelineContext:
    ws = tmp_path / suffix
    ws.mkdir(parents=True, exist_ok=True)
    return PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))


# ---------------------------------------------------------------- 前半段

def test_run_until_classify_produces_session_and_progress(tmp_path):
    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=2)
    file_id = _upload(src)
    ctx = _new_ctx(tmp_path)
    prefs = UserPreferences(target_size_mb=10.0, compression_target="screen")

    calls = []
    classified, session_data = run_until_classify(
        file_id, prefs, ctx, on_progress=lambda stage, pct: calls.append((stage, pct))
    )

    assert len(classified) == 2
    assert [c[0] for c in calls] == ["split", "extract", "classify"]
    assert [c[1] for c in calls] == [0.10, 0.30, 0.40]
    assert len(session_data.pages) == 2
    assert session_data.classified == classified


def test_session_save_load_roundtrip(tmp_path):
    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=1)
    file_id = _upload(src)
    ctx = _new_ctx(tmp_path)
    prefs = UserPreferences(target_size_mb=10.0, compression_target="screen")

    _classified, session_data = run_until_classify(file_id, prefs, ctx)
    save_session(session_data)

    assert (Path(ctx.tmp_workspace) / SESSION_REF).is_file()
    restored = load_session(ctx.tmp_workspace)
    assert restored.pages[0].page_number == session_data.pages[0].page_number
    assert restored.classified == session_data.classified


def test_load_session_missing_raises_session_expired(tmp_path):
    with pytest.raises(PhaseError) as exc_info:
        load_session(str(tmp_path / "nonexistent"))
    assert exc_info.value.code == "SESSION_EXPIRED"


# ---------------------------------------------------------------- 后半段：收敛循环

def _run_full_to_classify(tmp_path, n_pages=2, target_mb=10.0, ctarget="screen"):
    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=n_pages)
    file_id = _upload(src)
    ctx = _new_ctx(tmp_path)
    prefs = UserPreferences(target_size_mb=target_mb, compression_target=ctarget)
    classified, session_data = run_until_classify(file_id, prefs, ctx)
    return classified, session_data, prefs


def test_resume_after_review_reaches_acceptance_and_produces_output(tmp_path):
    classified, session_data, prefs = _run_full_to_classify(tmp_path, target_mb=10.0)

    out_path = resume_after_review(session_data, classified, prefs)

    assert Path(out_path).is_file()
    out = pymupdf.open(out_path)
    try:
        assert out.page_count == 2
    finally:
        out.close()
    # 收敛轨迹已记录（可观测性，不参与决策）；铁律 7 的硬上限：
    # 不允许静默输出超出目标 15% 以上的文件（欠冲多少都可接受，见 §8.4）
    assert len(session_data.ctx.convergence_history) >= 1
    last = session_data.ctx.convergence_history[-1]
    assert last.gap_ratio <= CFG.convergence_tolerance


def test_resume_after_review_honors_skip_override(tmp_path):
    """review 断点跨过之后追加的 skip override 必须生效——page 1 位图字节不变。"""
    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=2)
    src_doc = pymupdf.open(str(src))
    src_img_bytes = src_doc.extract_image(src_doc[0].get_images()[0][0])["image"]
    src_doc.close()

    file_id = _upload(src)
    ctx = _new_ctx(tmp_path)
    prefs = UserPreferences(target_size_mb=1.0, compression_target="email")
    classified, session_data = run_until_classify(file_id, prefs, ctx)

    resume_prefs = UserPreferences(
        target_size_mb=1.0, compression_target="email",
        per_page_overrides=[PageOverride(page_number=1, action="skip")],
    )
    out_path = resume_after_review(session_data, classified, resume_prefs)

    out = pymupdf.open(out_path)
    try:
        out_img_bytes = out.extract_image(out[0].get_images()[0][0])["image"]
        assert out_img_bytes == src_img_bytes  # skip 页一个字节不动
    finally:
        out.close()


def test_resume_after_review_raises_convergence_failed_when_target_too_tiny(tmp_path):
    classified, session_data, _prefs = _run_full_to_classify(tmp_path, target_mb=10.0)
    tiny_prefs = UserPreferences(target_size_mb=0.001, compression_target="email")

    with pytest.raises(PhaseError) as exc_info:
        resume_after_review(session_data, classified, tiny_prefs)
    assert exc_info.value.code in ("TARGET_TOO_SMALL", "CONVERGENCE_FAILED")
    assert exc_info.value.phase in ("decide", "orchestrator")


def test_resume_after_review_convergence_failed_after_exhausting_all_tiers(tmp_path, monkeypatch):
    """Tier 系统（2026-07-04）：全部 Tier 耗尽仍超标 → CONVERGENCE_FAILED +
    ConvergenceDiagnostics。monkeypatch 固定 execute_compression 的估算输出与
    Tier 边界的真实大小（恒定超标 30%），与真实压缩的不确定性解耦。"""
    import app.pipeline.orchestrator as orch
    from app.contracts import CompressionResult

    classified, session_data, prefs = _run_full_to_classify(tmp_path, target_mb=10.0)
    target_bytes = prefs.target_size_mb * 1024 * 1024

    def fake_execute(plan, pages, ctx, config):
        return CompressionResult(
            total_raster_bytes=int(target_bytes * 1.3),
            per_image_results=[], round_number=plan.round_number,
            base_aggressiveness=plan.base_aggressiveness,
            tier_used=plan.tier,
        )

    monkeypatch.setattr(orch, "execute_compression", fake_execute)
    # 裁决 1：边界判定看真实 assemble 大小——固定为超标 30%，逼所有 Tier 失败
    monkeypatch.setattr(orch, "_actual_size", lambda path: int(target_bytes * 1.3))

    with pytest.raises(PhaseError) as exc_info:
        resume_after_review(session_data, classified, prefs)

    err = exc_info.value
    assert err.code == "CONVERGENCE_FAILED"
    assert err.phase == "orchestrator"
    assert err.recoverable is False
    # 可行动诊断（用户补强）：真实数字 + skip 统计
    diag = err.data.diagnostics
    assert diag is not None
    assert diag.tier_reached == "full_raster"
    assert diag.best_achievable_bytes == int(target_bytes * 1.3)
    assert diag.skip_page_count == 0 and diag.skip_raster_bytes == 0
    # 轨迹覆盖了多个 Tier（合成样本无矢量 → hybrid 无资格被跳过 → in_place + full_raster）
    tiers_seen = {s.tier for s in session_data.ctx.convergence_history}
    assert "in_place" in tiers_seen and "full_raster" in tiers_seen


def test_resume_after_review_convergence_failed_message_and_history(tmp_path):
    """构造一个不可能收敛到目标以内、但预算又不会被 decide 直接判 TARGET_TOO_SMALL
    的场景很难在合成测试里稳定复现（依赖真实图片压缩比），改为直接验证收敛循环的
    结构性不变量：单 Tier 内 5 轮为止步、每轮记录单调推进的 round_number。"""
    classified, session_data, prefs = _run_full_to_classify(tmp_path, target_mb=10.0)
    resume_after_review(session_data, classified, prefs)
    # Tier 系统下轨迹可能跨 Tier；按 Tier 分组后各组内 round 单调
    from itertools import groupby
    for tier, steps in groupby(session_data.ctx.convergence_history, key=lambda s: s.tier):
        rounds = [s.round_number for s in steps]
        assert rounds == list(range(1, len(rounds) + 1))
        assert len(rounds) <= CFG.max_convergence_rounds


def test_tier_escalation_accepts_at_full_raster(tmp_path, monkeypatch):
    """降级状态机最小验证：in_place 真实大小超标 → hybrid 无资格跳过 →
    full_raster 达标接受，tier_used 反映在轨迹与任务层。"""
    import app.pipeline.orchestrator as orch

    classified, session_data, prefs = _run_full_to_classify(tmp_path, target_mb=10.0)
    target_bytes = prefs.target_size_mb * 1024 * 1024

    sizes = iter([int(target_bytes * 1.5),   # tier in_place 边界：超标
                  int(target_bytes * 0.8)])  # tier full_raster 边界：达标
    monkeypatch.setattr(orch, "_actual_size", lambda path: next(sizes))

    out_path = resume_after_review(session_data, classified, prefs)

    assert Path(out_path).is_file()
    assert session_data.ctx.convergence_history[-1].tier == "full_raster"
    # 输出确实是整页光栅化产物：无矢量、无文字、每页单图
    out = pymupdf.open(out_path)
    try:
        for pg in out:
            assert pg.get_text().strip() == ""       # 文字变图片
            assert len(pg.get_images(full=True)) == 1
    finally:
        out.close()


# ---------------------------------------------------------------- Tier 降级链（步骤 4 补齐）

def _classify_vec_pdf(tmp_path, target_mb, ctarget="screen", heavy_vec_bytes=None):
    """矢量+文字合成 PDF 走到 classify；可选把页 1 矢量成本改成重矢量
    （模拟真实作品集的 CAD 页——合成 PDF 画不出几十万控制点，直接改契约
    对象的 original_bytes 等价且快）。"""
    from test_tier import _build_vector_text_pdf

    src = _build_vector_text_pdf(tmp_path / "vec_text.pdf", n_pages=2)
    file_id = _upload(src)
    ctx = _new_ctx(tmp_path)
    user_prefs = UserPreferences(target_size_mb=target_mb, compression_target=ctarget)
    classified, session_data = run_until_classify(file_id, user_prefs, ctx)
    if heavy_vec_bytes is not None:
        session_data.pages[0].vector_paths[0].original_bytes = heavy_vec_bytes
    return classified, session_data, user_prefs


def test_escalation_all_three_tiers_strict_order(tmp_path, monkeypatch):
    """清单条目"降级链逐级触发，不跳级不回退"：三个 Tier 全部实跑
    （页 1 重矢量使 hybrid 有资格），轨迹 tier 索引单调非降、三层齐全。"""
    import app.pipeline.orchestrator as orch
    from app.contracts import COMPRESSION_TIERS

    # vec 9.8MB：in_place 下限 < 10.49MB 目标（tier1 能跑）；> 9.44MB 停止线（hybrid 选页）
    classified, session_data, user_prefs = _classify_vec_pdf(
        tmp_path, target_mb=10.0, heavy_vec_bytes=9_800_000)
    target_bytes = user_prefs.target_size_mb * 1024 * 1024

    sizes = iter([int(target_bytes * 1.5),   # in_place 边界：超标
                  int(target_bytes * 1.4),   # hybrid 边界：仍超标
                  int(target_bytes * 0.7)])  # full_raster 边界：达标
    monkeypatch.setattr(orch, "_actual_size", lambda path: next(sizes))

    out_path = resume_after_review(session_data, classified, user_prefs)

    assert Path(out_path).is_file()
    history = session_data.ctx.convergence_history
    tier_indices = [COMPRESSION_TIERS.index(s.tier) for s in history]
    assert tier_indices == sorted(tier_indices)          # 不回退
    assert {s.tier for s in history} == {"in_place", "hybrid", "full_raster"}  # 不跳级
    assert history[-1].tier == "full_raster"


def test_escalation_tier2_accepts_partial_raster(tmp_path, monkeypatch):
    """Tier 1 失败 → Tier 2 达标：只有入选页被光栅化，其余页文字/矢量完好。"""
    import app.pipeline.orchestrator as orch

    classified, session_data, user_prefs = _classify_vec_pdf(
        tmp_path, target_mb=10.0, heavy_vec_bytes=9_800_000)
    target_bytes = user_prefs.target_size_mb * 1024 * 1024

    sizes = iter([int(target_bytes * 1.5),   # in_place：超标
                  int(target_bytes * 0.6)])  # hybrid：达标
    monkeypatch.setattr(orch, "_actual_size", lambda path: next(sizes))

    out_path = resume_after_review(session_data, classified, user_prefs)

    assert session_data.ctx.convergence_history[-1].tier == "hybrid"
    out = pymupdf.open(out_path)
    try:
        assert out[0].get_text().strip() == ""                    # 入选页：文字变图片
        assert len(out[0].get_drawings()) == 0                    # 入选页：矢量消亡
        assert "Whole page raster test 2" in out[1].get_text()    # 未入选页完好
        assert len(out[1].get_drawings()) > 0
    finally:
        out.close()


def test_boundary_uses_real_size_not_estimates(tmp_path, monkeypatch):
    """清单条目"Tier 边界判定用真实 assemble 大小，不用估算"（裁决 1 核心）：
    伪造估算超标 400%（收敛环 5 轮全部认为失败），但真实装配产物远小于目标
    （不打 _actual_size 桩，走真实 stat）→ 必须在 in_place 被接受。"""
    import app.pipeline.orchestrator as orch
    from app.contracts import CompressionResult

    classified, session_data, prefs = _run_full_to_classify(tmp_path, target_mb=10.0)
    target_bytes = prefs.target_size_mb * 1024 * 1024

    def fake_execute(plan, pages, ctx, config):
        return CompressionResult(
            total_raster_bytes=int(target_bytes * 5),  # 估算严重虚高（对照 p1 实测 +134%）
            per_image_results=[], round_number=plan.round_number,
            base_aggressiveness=plan.base_aggressiveness, tier_used=plan.tier,
        )

    monkeypatch.setattr(orch, "execute_compression", fake_execute)

    out_path = resume_after_review(session_data, classified, prefs)  # 不抛错 = 接受

    history = session_data.ctx.convergence_history
    assert history[-1].tier == "in_place"                    # 首层即接受，无降级
    assert all(s.gap_ratio > CFG.convergence_tolerance / 2   # 估算全程喊失败
               for s in history)
    assert Path(out_path).stat().st_size < target_bytes      # 真实大小才是判据


def test_loop_gap_basis_matches_decide_budget_and_settles_in_hybrid(tmp_path, monkeypatch):
    """架构级防御（修复 2026-07-05，用户批准）：环内 gap 基准必须 = decide 的
    本 Tier floor（target − plan.raster_budget），不得回退到 pre.total_fixed_bytes。

    披露：旧基准的真实病征是"恒高估 gap → 烧满 5 轮 → 激进度钉死上限 →
    参数打到 floor、深度欠冲"（首轮验收 6 场景全落 full_raster、欠冲 34-48%
    的根因），而非"误升 Tier"——边界判定是真实大小，旧基准推高压缩反而更易
    被边界接受。本测试锁真实病征：构造"est gap 恰在接受带内（+5%）"的场景，
    修复后 hybrid 一轮即落点、激进度停在预设并被边界接受；若有人改回
    pre.total_fixed_bytes，过期基准（含 9.8MB 已光栅化矢量）使 gap 虚高 +98%
    → 烧 5 轮 + 激进度攀升，本测试三处断言全部立即失败。"""
    import app.pipeline.orchestrator as orch
    from app.contracts import CompressionResult

    classified, session_data, user_prefs = _classify_vec_pdf(
        tmp_path, target_mb=10.0, heavy_vec_bytes=9_800_000)
    target_bytes = user_prefs.target_size_mb * 1024 * 1024

    budgets: list = []  # 逐轮记录 decide 的预算，供 fixed 基准不变量核对

    def fake_execute(plan, pages, ctx, config):
        budgets.append(plan.raster_budget)
        # 位图消耗 = 预算 + 5% target → 正确基准下 gap 恰为 +5%（微超标接受带内）
        return CompressionResult(
            total_raster_bytes=plan.raster_budget + int(target_bytes * 0.05),
            per_image_results=[], round_number=plan.round_number,
            base_aggressiveness=plan.base_aggressiveness, tier_used=plan.tier,
        )

    monkeypatch.setattr(orch, "execute_compression", fake_execute)
    sizes = iter([int(target_bytes * 1.5),    # in_place 边界：超标 → 升 hybrid
                  int(target_bytes * 1.04)])  # hybrid 边界：带内 → 接受
    monkeypatch.setattr(orch, "_actual_size", lambda path: next(sizes))

    resume_after_review(session_data, classified, user_prefs)

    history = session_data.ctx.convergence_history
    by_tier = {tier: list(steps) for tier, steps in
               __import__("itertools").groupby(history, key=lambda s: s.tier)}
    # ① 修复后：est gap 在带内 → 每 Tier 一轮即落点（旧基准：hybrid 烧满 5 轮）
    assert len(by_tier["in_place"]) == 1
    assert len(by_tier["hybrid"]) == 1
    # ② 激进度停在预设，未被虚高 gap 推升（旧基准：逐轮 +step 攀升）
    preset = CFG.presets["screen"].aggressiveness
    assert by_tier["hybrid"][0].base_aggressiveness == pytest.approx(preset)
    # ③ fixed 基准不变量：total_bytes − raster == target − plan.raster_budget
    for step, budget in zip(history, budgets):
        assert step.total_bytes - step.total_raster_bytes == step.target_bytes - budget
    # ④ 最终停在 hybrid（文字/矢量保留），未被误推向 full_raster
    assert history[-1].tier == "hybrid"


def test_final_failure_diagnostics_skip_stats_and_message(tmp_path, monkeypatch):
    """清单条目"CONVERGENCE_FAILED 携带诊断"+ skip 重占用场景 + §8.4 文案模板
    占位符与 ConvergenceDiagnostics 字段一一对应（真实数字）。"""
    import app.pipeline.orchestrator as orch

    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=3)
    file_id = _upload(src)
    ctx = _new_ctx(tmp_path)
    user_prefs = UserPreferences(
        target_size_mb=1.0, compression_target="email",
        per_page_overrides=[PageOverride(page_number=1, action="skip"),
                            PageOverride(page_number=2, action="skip")],
    )
    classified, session_data = run_until_classify(file_id, user_prefs, ctx)
    target_bytes = user_prefs.target_size_mb * 1024 * 1024

    fake_actual = int(target_bytes * 2)
    monkeypatch.setattr(orch, "_actual_size", lambda path: fake_actual)

    with pytest.raises(PhaseError) as exc_info:
        resume_after_review(session_data, classified, user_prefs)

    diag = exc_info.value.data.diagnostics
    assert diag is not None and diag.tier_reached == "full_raster"
    # skip 统计 = 会话页数据的真实字节（唯一 data_ref 口径）
    expected_skip_bytes = 0
    for page in session_data.pages:
        if page.page_number in (1, 2):
            expected_skip_bytes += sum(
                {img.data_ref: img.original_bytes for img in page.raster_images}.values()
            )
    assert diag.skip_page_count == 2
    assert diag.skip_raster_bytes == expected_skip_bytes > 0
    assert diag.skip_budget_ratio == pytest.approx(expected_skip_bytes / target_bytes)
    assert diag.best_achievable_bytes == fake_actual
    # §8.4 文案模板占位符 ↔ 诊断字段一一对应
    message = exc_info.value.data.message
    assert f"skip 了 {diag.skip_page_count} 页" in message
    assert f"{diag.skip_budget_ratio:.0%}" in message
    assert f"{diag.best_achievable_bytes / 1e6:.1f} MB" in message


# ---------------------------------------------------------------- 工作区清理

def test_cleanup_workspace_removes_directory_idempotently(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "marker.txt").write_text("x")
    cleanup_workspace(str(ws))
    assert not ws.exists()
    cleanup_workspace(str(ws))  # 幂等：目录已不存在也不报错


def test_cleanup_stale_workspaces_removes_only_expired(tmp_path):
    root = tmp_path / "workspaces"
    stale = root / "stale-task"
    fresh = root / "fresh-task"
    stale.mkdir(parents=True)
    fresh.mkdir(parents=True)
    (stale / SESSION_REF).write_bytes(b"x")
    (fresh / SESSION_REF).write_bytes(b"x")

    old_time = time.time() - 3600
    import os
    os.utime(stale / SESSION_REF, (old_time, old_time))

    removed = cleanup_stale_workspaces(workspace_root=root, max_age_seconds=1800)

    assert removed == 1
    assert not stale.exists()
    assert fresh.exists()


def test_cleanup_stale_workspaces_missing_root_returns_zero(tmp_path):
    assert cleanup_stale_workspaces(workspace_root=tmp_path / "nope") == 0
