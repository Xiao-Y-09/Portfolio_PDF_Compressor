"""Tier 系统测试（决策/契约级）：选页纯函数、tier3 边界、整页原语、
《压缩决策引擎.md》第 10 章分级压缩验证清单的决策层条目。

编排级（降级链/边界真实判定/诊断）见 test_orchestrator.py；
任务级（tier_used/warning 透传）见 test_tasks.py；
真实样本 18 场景验收在步骤 5（独立脚本）。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pymupdf
import pytest

from app.config.settings import CompressionConfig
from app.contracts import PageOverride, PipelineContext, UserPreferences
from app.pipeline.assemble import assemble_pdf
from app.pipeline.classify import classify_pages
from app.pipeline.compress import execute_compression
from app.pipeline.decide import decide_compression, select_whole_pages
from app.pipeline.extract import extract_elements
from app.pipeline.preprocess import preprocess
from app.pipeline.split import split_pdf

from test_decide import (
    PAGE_AREA,
    make_image,
    make_page,
    make_preprocess,
    make_vector,
    prefs,
)

CFG = CompressionConfig()


# ---------------------------------------------------------------- 选页纯函数（§8.6）

def test_select_in_place_always_empty():
    pages = [make_page(1, vectors=[make_vector(500_000, PAGE_AREA * 0.9,
                                               original_bytes=5_000_000)])]
    assert select_whole_pages(pages, make_preprocess(pages), prefs(), CFG, "in_place") == set()


def test_select_full_raster_selects_all_nonskip():
    pages = [make_page(n) for n in (1, 2, 3)]
    user = prefs(overrides=[PageOverride(page_number=2, action="skip")])
    selected = select_whole_pages(pages, make_preprocess(pages), user, CFG, "full_raster")
    assert selected == {1, 3}  # skip 页永不入选（铁律 4）


def test_select_hybrid_requires_byte_savings():
    """资格判定用字节说话：重矢量页入选，照片页（光栅化不省钱）不入选。"""
    vector_heavy = make_page(1, vectors=[
        make_vector(2_000_000, PAGE_AREA * 0.9, original_bytes=9_000_000)])
    photo = make_page(2, rasters=[make_image(PAGE_AREA * 0.9, dpi=300.0,
                                             data_ref="images/photo.bin")])
    pages = [vector_heavy, photo]
    # 目标足够小，贪心必须选页才能把下限压进停止线
    selected = select_whole_pages(pages, make_preprocess(pages),
                                  prefs(target_mb=5.0), CFG, "hybrid")
    assert 1 in selected        # 矢量页：整页 JPEG 远小于 9MB 矢量下限 → 入选
    assert 2 not in selected    # 照片页：整页渲染估算 ≥ 位图下限估算 → 无资格


def test_select_hybrid_skip_excluded_even_if_heavy():
    pages = [make_page(1, vectors=[
        make_vector(2_000_000, PAGE_AREA * 0.9, original_bytes=9_000_000)])]
    user = prefs(target_mb=5.0, overrides=[PageOverride(page_number=1, action="skip")])
    assert select_whole_pages(pages, make_preprocess(pages), user, CFG, "hybrid") == set()


def test_select_hybrid_greedy_stops_at_margin():
    """贪心最小化：下限已在停止线内时，一页都不选。"""
    small_vec = make_page(1, vectors=[
        make_vector(50_000, PAGE_AREA * 0.5, original_bytes=100_000)])
    pages = [small_vec]
    # 目标宽裕（100MB）：floor 远低于 target × margin → 无需选页
    selected = select_whole_pages(pages, make_preprocess(pages),
                                  prefs(target_mb=100.0), CFG, "hybrid")
    assert selected == set()


def test_select_hybrid_greedy_minimality():
    """清单条目"贪心：选页数最小化"：净节省降序选入，选够即停——
    去掉任一选中页则下限回到停止线之上（最小性），未选页确实不需要。"""
    pages = [
        make_page(1, vectors=[make_vector(500_000, PAGE_AREA * 0.9,
                                          original_bytes=5_000_000)]),
        make_page(2, vectors=[make_vector(300_000, PAGE_AREA * 0.9,
                                          original_bytes=3_000_000)]),
        make_page(3, vectors=[make_vector(100_000, PAGE_AREA * 0.9,
                                          original_bytes=1_000_000)]),
    ]
    pre = make_preprocess(pages)
    user = prefs(target_mb=5.0)
    selected = select_whole_pages(pages, pre, user, CFG, "hybrid")
    assert selected == {1, 2}  # 5MB+3MB 页入选后 floor 已进停止线，1MB 页不选

    # 最小性验证：任一"少选一页"的组合下 floor 仍超停止线
    # floor(W) = irreducible + Σ_{未选页} vec保留 + Σ_{选中页} whole_est
    from app.pipeline.decide import _initial_aggressiveness, _make_whole_page_plan

    target_bytes = 5.0 * 1024 * 1024
    stop_line = target_bytes * CFG.tier2_budget_margin
    irreducible = 200_000  # make_preprocess 默认
    vec = {1: 5_000_000, 2: 3_000_000, 3: 1_000_000}
    preset = _initial_aggressiveness(user, CFG)
    whole_est = _make_whole_page_plan(pages[0], preset, "hybrid", CFG).estimated_bytes
    for dropped in selected:
        keep_set = selected - {dropped}
        floor = (irreducible
                 + sum(v for pn, v in vec.items() if pn not in keep_set)
                 + whole_est * len(keep_set))
        assert floor > stop_line, f"去掉页 {dropped} 后 floor 仍应超停止线（最小性）"


def test_hybrid_squeeze_respects_standard_floors():
    """清单条目"Tier 1/2 参数仍在标准 floor 内"：极小目标逼出两遍再平衡，
    hybrid 整页计划的 quality/dpi 停在标准 floor（50/150），绝不越入 tier3 区间。"""
    pages = [make_page(1, vectors=[make_vector(500_000, PAGE_AREA * 0.9,
                                               original_bytes=9_300_000)])]
    plan = decide_compression([], pages, prefs(target_mb=0.3),
                              make_preprocess(pages), None, 1, CFG, tier="hybrid")
    wpp = plan.pages[0].whole_page_plan
    assert wpp is not None
    assert wpp.quality >= CFG.quality_floor       # 50，不是 tier3 的 30
    assert wpp.dpi >= CFG.dpi_floor               # 150，不是 tier3 的 72


def test_full_raster_squeeze_uses_tier3_floors_below_standard():
    """清单条目"Tier 3 参数在 tier3 边界内"+ 裁决 2 实证：极小目标下 tier3
    参数突破标准 floor（quality < 50、dpi < 150），且精确停在 tier3 下限。"""
    pages = [make_page(1, vectors=[make_vector(500_000, PAGE_AREA * 0.9,
                                               original_bytes=9_300_000)])]
    # target 0.21MB：预算 ~20KB，逼两遍再平衡把参数压到 tier3 双下限
    # （0.22→0.21 随 2026-07-05 JPEG 系数标定同步——估算变小，钳制点后移）
    plan = decide_compression([], pages, prefs(target_mb=0.21, target="email"),
                              make_preprocess(pages), None, 1, CFG, tier="full_raster")
    wpp = plan.pages[0].whole_page_plan
    assert wpp is not None
    assert wpp.quality == CFG.tier3_quality_floor  # 30：触底且不穿透
    assert wpp.dpi == CFG.tier3_dpi_floor          # 72：触底且不穿透
    assert wpp.quality < CFG.quality_floor         # 确实突破了标准 floor（裁决 2 生效）
    assert wpp.dpi < CFG.dpi_floor


# ---------------------------------------------------------------- decide + tier3 边界（§8.7）

def test_decide_full_raster_plans_within_tier3_bounds():
    pages = [make_page(1, rasters=[make_image(PAGE_AREA * 0.5)]),
             make_page(2, vectors=[make_vector(200_000, PAGE_AREA * 0.5)])]
    plan = decide_compression([], pages, prefs(target_mb=10.0, target="email"),
                              make_preprocess(pages), None, 1, CFG, tier="full_raster")
    assert plan.tier == "full_raster"
    for pp in plan.pages:
        assert pp.whole_page_plan is not None
        assert pp.raster_plans == [] and pp.vector_plans == []
        assert CFG.tier3_quality_floor <= pp.whole_page_plan.quality <= CFG.quality_ceiling
        assert CFG.tier3_dpi_floor <= pp.whole_page_plan.dpi <= CFG.tier3_dpi_ceiling


def test_decide_in_place_unchanged_by_default_tier():
    """tier 缺省 = in_place：行为与 Tier 系统引入前完全一致（无整页计划）。"""
    pages = [make_page(1, rasters=[make_image(PAGE_AREA * 0.5)])]
    plan = decide_compression([], pages, prefs(), make_preprocess(pages), None, 1, CFG)
    assert plan.tier == "in_place"
    assert all(pp.whole_page_plan is None for pp in plan.pages)


def test_decide_hybrid_mixed_page_plans():
    """hybrid：入选页出整页计划（标准 floor），未入选页照常元素级计划。"""
    vector_heavy = make_page(1, vectors=[
        make_vector(2_000_000, PAGE_AREA * 0.9, original_bytes=9_000_000)])
    photo = make_page(2, rasters=[make_image(PAGE_AREA * 0.9, dpi=300.0,
                                             data_ref="images/photo.bin")])
    pages = [vector_heavy, photo]
    plan = decide_compression([], pages, prefs(target_mb=5.0),
                              make_preprocess(pages), None, 1, CFG, tier="hybrid")
    by_number = {pp.page_number: pp for pp in plan.pages}
    assert by_number[1].whole_page_plan is not None
    # Tier 2 用标准 floor（不许被 tier3 独立下限污染）
    assert by_number[1].whole_page_plan.dpi >= CFG.dpi_floor
    assert by_number[1].whole_page_plan.quality >= CFG.quality_floor
    assert by_number[2].whole_page_plan is None
    assert by_number[2].raster_plans


def test_whole_page_pages_have_empty_element_plans_both_tiers():
    """清单条目"whole_page_plan 置位的页 raster_plans/vector_plans 为空"——
    hybrid 与 full_raster 两条产线的实际输出逐页断言。"""
    vector_heavy = make_page(1, rasters=[make_image(PAGE_AREA * 0.2,
                                                    data_ref="images/small.bin")],
                             vectors=[make_vector(2_000_000, PAGE_AREA * 0.9,
                                                  original_bytes=9_000_000)])
    pages = [vector_heavy, make_page(2)]
    for tier in ("hybrid", "full_raster"):
        plan = decide_compression([], pages, prefs(target_mb=5.0),
                                  make_preprocess(pages), None, 1, CFG, tier=tier)
        for pp in plan.pages:
            if pp.whole_page_plan is not None:
                assert pp.raster_plans == [] and pp.vector_plans == [], (
                    f"tier={tier} page={pp.page_number}")


def test_hybrid_plan_contract_roundtrip():
    """清单条目"Tier 2 选页后的 PagePlan 构造成功"：真实决策产物经契约
    validator 构造成功且 JSON round-trip 恒等（互斥约束不误伤合法输出）。"""
    from app.contracts import CompressionPlan

    pages = [make_page(1, vectors=[make_vector(2_000_000, PAGE_AREA * 0.9,
                                               original_bytes=9_000_000)]),
             make_page(2, rasters=[make_image(PAGE_AREA * 0.5,
                                              data_ref="images/p2.bin")])]
    plan = decide_compression([], pages, prefs(target_mb=5.0),
                              make_preprocess(pages), None, 1, CFG, tier="hybrid")
    restored = CompressionPlan.model_validate(plan.model_dump())
    assert restored == plan


def test_pageplan_exclusivity_rejects_mixed_construction():
    """清单条目"模拟异常输入确实抛 ValidationError"：伪造"整页计划 + 元素级
    计划并存"的畸形 PagePlan（下游代码若绕过 decide 直接拼装会撞上契约墙）。"""
    from pydantic import ValidationError

    from app.contracts import PagePlan, RasterPlan, WholePageRasterPlan

    with pytest.raises(ValidationError):
        PagePlan(
            page_number=1,
            whole_page_plan=WholePageRasterPlan(dpi=150, quality=75,
                                                estimated_bytes=100_000),
            raster_plans=[RasterPlan(
                image_index=0, area_ratio=0.5, original_dpi=300.0,
                original_bytes=100, target_dpi=150, quality=80,
                max_dimension=1000, output_format="jpeg", estimated_bytes=100)],
        )


# ---------------------------------------------------------------- 整页原语 E2E 最小验证

def _build_vector_text_pdf(path: Path, n_pages: int = 2) -> Path:
    doc = pymupdf.open()
    for i in range(n_pages):
        doc.new_page(width=595, height=842)
        page = doc[i]
        for k in range(40):
            page.draw_line((30 + k * 13, 80), (40 + k * 13, 700), color=(0, 0, 0.8))
        page.insert_text((60, 760), f"Whole page raster test {i + 1}", fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


def test_whole_page_primitive_e2e_minimal(tmp_path):
    """渲染（compress）→ 替换（assemble）贯通：输出页数守恒、内容变单张图、
    文字与矢量消亡、链接外壳无损（本样本无链接，页数/尺寸为准）。"""
    src = _build_vector_text_pdf(tmp_path / "vec_text.pdf")
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))
    user = UserPreferences(target_size_mb=10.0, compression_target="screen")

    raw = split_pdf(str(src), ctx)
    pages, fonts, meta = extract_elements(raw, str(src), ctx)
    classified = classify_pages(pages)
    pre = preprocess(pages, fonts, meta, user, ctx)
    plan = decide_compression(classified, pages, user, pre, None, 1, CFG,
                              tier="full_raster")
    result = execute_compression(plan, pages, ctx, CFG)
    assert result.tier_used == "full_raster"
    assert result.total_raster_bytes > 0  # 整页流计入预算口径

    out_path = assemble_pdf(pages, plan, result, pre, meta, ctx)
    out = pymupdf.open(out_path)
    src_doc = pymupdf.open(str(src))
    try:
        assert out.page_count == src_doc.page_count
        for i in range(out.page_count):
            assert out[i].rect.width == pytest.approx(595, abs=1)
            assert out[i].get_text().strip() == ""            # 文字变图片
            assert len(out[i].get_drawings()) == 0            # 矢量消亡
            assert len(out[i].get_images(full=True)) == 1     # 单张整页图
    finally:
        out.close()
        src_doc.close()


def test_whole_page_render_failure_tolerated_page_left_intact(tmp_path, monkeypatch):
    """容错路径：单页整页渲染失败 → warning + 该页原样保留（compress 失败分支
    + assemble 渲染产物缺失分支），其余页正常光栅化，成功率 ≥80% 不熔断。"""
    import app.pipeline.compress as compress_mod

    src = _build_vector_text_pdf(tmp_path / "vec_text.pdf", n_pages=5)
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))
    user = UserPreferences(target_size_mb=10.0, compression_target="screen")

    raw = split_pdf(str(src), ctx)
    pages, fonts, meta = extract_elements(raw, str(src), ctx)
    pre = preprocess(pages, fonts, meta, user, ctx)
    plan = decide_compression([], pages, user, pre, None, 1, CFG, tier="full_raster")

    real_render = compress_mod._render_whole_page

    def flaky_render(ctx_, page_number, wpp):
        if page_number == 1:
            raise RuntimeError("injected render failure")
        return real_render(ctx_, page_number, wpp)

    monkeypatch.setattr(compress_mod, "_render_whole_page", flaky_render)
    result = execute_compression(plan, pages, ctx, CFG)  # 1/5 失败 → 80%，不熔断

    out_path = assemble_pdf(pages, plan, result, pre, meta, ctx)
    out = pymupdf.open(out_path)
    try:
        assert "Whole page raster test 1" in out[0].get_text()  # 失败页原样保留
        for i in range(1, 5):
            assert out[i].get_text().strip() == ""              # 其余页正常光栅化
    finally:
        out.close()


def test_whole_page_replace_failure_tolerated(tmp_path, monkeypatch):
    """assemble 替换异常分支：insert_image 抛错 → warning + 该页保留，不炸整体。"""
    src = _build_vector_text_pdf(tmp_path / "vec_text.pdf", n_pages=2)
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))
    user = UserPreferences(target_size_mb=10.0, compression_target="screen")

    raw = split_pdf(str(src), ctx)
    pages, fonts, meta = extract_elements(raw, str(src), ctx)
    pre = preprocess(pages, fonts, meta, user, ctx)
    plan = decide_compression([], pages, user, pre, None, 1, CFG, tier="full_raster")
    result = execute_compression(plan, pages, ctx, CFG)

    real_insert = pymupdf.Page.insert_image
    state = {"calls": 0}

    def flaky_insert(self, *args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("injected insert failure")
        return real_insert(self, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "insert_image", flaky_insert)
    out_path = assemble_pdf(pages, plan, result, pre, meta, ctx)
    monkeypatch.undo()

    out = pymupdf.open(out_path)
    try:
        assert out.page_count == 2                              # 不炸整体
        # 失败原子性：insert 失败的页必须原封不动（先 insert 后清空的顺序保证——
        # 若先清空，失败会留下白页而非"保留"）
        assert "Whole page raster test 1" in out[0].get_text()
        assert out[1].get_text().strip() == ""                  # 第二页替换成功
    finally:
        out.close()


def test_whole_page_skip_page_untouched_e2e(tmp_path):
    """skip 页在 full_raster 下原样保留（文字/矢量都在）。"""
    src = _build_vector_text_pdf(tmp_path / "vec_text.pdf")
    ws = tmp_path / "ws"
    ws.mkdir()
    ctx = PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))
    user = UserPreferences(
        target_size_mb=10.0, compression_target="screen",
        per_page_overrides=[PageOverride(page_number=1, action="skip")],
    )

    raw = split_pdf(str(src), ctx)
    pages, fonts, meta = extract_elements(raw, str(src), ctx)
    pre = preprocess(pages, fonts, meta, user, ctx)
    plan = decide_compression([], pages, user, pre, None, 1, CFG, tier="full_raster")
    assert plan.pages[0].skip is True and plan.pages[0].whole_page_plan is None
    result = execute_compression(plan, pages, ctx, CFG)
    out_path = assemble_pdf(pages, plan, result, pre, meta, ctx)

    out = pymupdf.open(out_path)
    try:
        assert "Whole page raster test 1" in out[0].get_text()  # skip 页文字完好
        assert len(out[0].get_drawings()) > 0                   # skip 页矢量完好
        assert out[1].get_text().strip() == ""                  # 非 skip 页已光栅化
    finally:
        out.close()
