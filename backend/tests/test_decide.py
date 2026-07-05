"""Phase 8 决策引擎测试——手册第 8 章 10 个必测用例一个不少，另加扩展断言。

全部使用合成契约对象 + 显式注入 CompressionConfig（决策器是纯函数，
不接触 PDF/文件；真实样本的 decide 实跑见验收报告的专项脚本）。
"""

from __future__ import annotations

import pytest

from app.config.settings import CompressionConfig
from app.contracts import (
    BBox,
    CompressionResult,
    PageElements,
    PageOverride,
    PerPageFixedBytes,
    PhaseError,
    PreprocessResult,
    RasterImage,
    UserPreferences,
    VectorPath,
)
from app.pipeline.decide import decide_compression

CFG = CompressionConfig()
PAGE_W, PAGE_H = 595.0, 842.0
PAGE_AREA = PAGE_W * PAGE_H


def make_image(area_pt2: float, dpi: float = 300.0, alpha_type: str = "none",
               width: int = 3000, height: int = 2000, original_bytes: int = 4_000_000,
               data_ref: str = "images/img_001_000.bin") -> RasterImage:
    w = area_pt2 ** 0.5
    return RasterImage(
        data_ref=data_ref, width=width, height=height, dpi=dpi, color_space="RGB",
        has_alpha=(alpha_type == "translucent"), alpha_type=alpha_type,
        original_bytes=original_bytes, bbox=BBox(x=0, y=0, w=w, h=area_pt2 / w),
    )


def make_vector(cp: int, area_pt2: float, original_bytes: int = 300_000) -> VectorPath:
    w = area_pt2 ** 0.5
    return VectorPath(
        path_data_ref="vectors/vec_001_000.bin", control_point_count=cp,
        original_bytes=original_bytes, bbox=BBox(x=0, y=0, w=w, h=area_pt2 / w),
    )


def make_page(number: int, rasters=(), vectors=()) -> PageElements:
    return PageElements(page_number=number, page_width=PAGE_W, page_height=PAGE_H,
                        raster_images=list(rasters), vector_paths=list(vectors))


def make_preprocess(pages, irreducible: int = 200_000) -> PreprocessResult:
    """构造与 pages 一致的 PreprocessResult：vector_bytes 对齐实际矢量，
    irreducible 部分（文字+字体+元数据+结构）单独给定。"""
    per_page = [
        PerPageFixedBytes(
            page_number=p.page_number,
            vector_bytes=sum(v.original_bytes for v in p.vector_paths),
            text_bytes=0, annotation_bytes=0,
        ) for p in pages
    ]
    total = sum(pp.vector_bytes for pp in per_page) + irreducible
    return PreprocessResult(subsetted_fonts=[], total_fixed_bytes=total,
                            per_page_fixed_bytes=per_page, stripped_metadata_fields=[])


def prefs(target_mb: float = 10.0, target: str = "screen", overrides=()) -> UserPreferences:
    return UserPreferences(target_size_mb=target_mb, compression_target=target,
                           per_page_overrides=list(overrides))


def run(pages, user_prefs=None, previous=None, round_number=1, irreducible=200_000):
    user_prefs = user_prefs or prefs()
    return decide_compression([], pages, user_prefs, make_preprocess(pages, irreducible),
                              previous, round_number, CFG)


# ================================================================ 手册 10 必测

def test_01_pure_function_same_input_same_output():
    """必测 1：相同输入调用 3 次，输出完全相等。"""
    pages = [make_page(1,
                       rasters=[make_image(PAGE_AREA * 0.5),
                                make_image(PAGE_AREA * 0.1, alpha_type="translucent",
                                           data_ref="images/img_001_001.bin")],
                       vectors=[make_vector(50_000, PAGE_AREA * 0.4)])]
    user = prefs(overrides=[PageOverride(page_number=1, action="aggressive")])
    previous = CompressionResult(total_raster_bytes=12_000_000, per_image_results=[],
                                 round_number=1, base_aggressiveness=0.5)
    dumps = [run(pages, user, previous, round_number=2).model_dump() for _ in range(3)]
    assert dumps[0] == dumps[1] == dumps[2]


def test_02_only_downgrade_never_upgrade():
    """必测 2：target_dpi ≤ original_dpi；quality ∈ [floor, ceiling]。"""
    pages = [make_page(1, rasters=[
        make_image(PAGE_AREA * r, dpi=d, data_ref=f"images/i{n}.bin")
        for n, (r, d) in enumerate([(0.9, 300), (0.5, 150), (0.1, 80), (0.02, 40)])])]
    plan = run(pages)
    for rp in plan.pages[0].raster_plans:
        assert rp.target_dpi <= rp.original_dpi
        assert CFG.quality_floor <= rp.quality <= CFG.quality_ceiling


def test_03_skip_page_has_no_plans():
    """必测 3：skip 页 raster_plans / vector_plans 为空。"""
    pages = [make_page(1, rasters=[make_image(PAGE_AREA * 0.5)],
                       vectors=[make_vector(200_000, PAGE_AREA * 0.5)]),
             make_page(2, rasters=[make_image(PAGE_AREA * 0.5,
                                              data_ref="images/img_002_000.bin")])]
    user = prefs(overrides=[PageOverride(page_number=1, action="skip")])
    plan = run(pages, user)
    assert plan.pages[0].skip is True
    assert plan.pages[0].raster_plans == [] and plan.pages[0].vector_plans == []
    assert plan.pages[1].skip is False and plan.pages[1].raster_plans


def test_04_area_correction_smaller_area_lower_quality():
    """必测 4：同一张图 area_ratio 更小 → quality 更低。"""
    big = make_page(1, rasters=[make_image(PAGE_AREA * 0.8)])
    small = make_page(1, rasters=[make_image(PAGE_AREA * 0.05)])
    q_big = run([big]).pages[0].raster_plans[0].quality
    q_small = run([small]).pages[0].raster_plans[0].quality
    assert q_small < q_big


def test_05_pixel_ceiling_caps_tiny_display():
    """必测 5：6000×4000 原图、bbox 仅 1 英寸 → 输出像素被天花板压死。"""
    img = make_image(72.0 * 72.0, dpi=6000.0, width=6000, height=4000)  # bbox 1in×1in
    plan = run([make_page(1, rasters=[img])])
    rp = plan.pages[0].raster_plans[0]
    # 天花板：1 英寸 × target_dpi × 1.2；必然远小于原图 6000px
    assert rp.max_dimension <= int(1.0 * rp.target_dpi * CFG.pixel_ceiling_ratio) + 1
    assert rp.max_dimension < 500 < img.width
    assert rp.target_dpi <= CFG.dpi_ceiling  # 远低于原图 6000dpi
    assert rp.estimated_bytes < 100_000      # 输出成本极小


def test_06_vector_strategy_switching():
    """必测 6：控制点 5k → keep；50k → simplify；200k → rasterize（面积 0.5）。"""
    strategies = {}
    for cp in (5_000, 50_000, 200_000):
        page = make_page(1, vectors=[make_vector(cp, PAGE_AREA * 0.5)])
        strategies[cp] = run([page]).pages[0].vector_plans[0]
    assert strategies[5_000].strategy == "keep"
    assert strategies[50_000].strategy == "simplify"
    assert strategies[50_000].simplify_tolerance == CFG.simplify_tolerance_aggressive
    assert strategies[200_000].strategy == "rasterize"
    assert strategies[200_000].rasterize_dpi == CFG.rasterize_dpi_medium


def test_07_all_alpha_types_use_jpeg_base():
    """必测 7（架构决策 A2 修订，2026-07-04）：三态 alpha 的位图基底统一 jpeg——
    真透明的 SMask 保真改由 assemble 层原地替换保证（keep=["SMask","Mask"]），
    不再体现为 decide 的 output_format 差异。"""
    pages = [make_page(1, rasters=[
        make_image(PAGE_AREA * 0.3, alpha_type="translucent", data_ref="images/t.bin"),
        make_image(PAGE_AREA * 0.3, alpha_type="opaque", data_ref="images/o.bin"),
        make_image(PAGE_AREA * 0.3, alpha_type="none", data_ref="images/n.bin")])]
    fmts = [rp.output_format for rp in run(pages).pages[0].raster_plans]
    assert fmts == ["jpeg", "jpeg", "jpeg"]


def test_08_budget_conservation_after_rebalance():
    """必测 8：下限不受约束时，唯一流口径 Σestimated ≤ budget × overshoot_tolerance。

    目标按 2026-07-04 目视标定后的下限（q≥50、dpi≥150）校准为可行值：
    8 张 4000×3000@300dpi 图的下限总成本 ≈2.4MB，2.6MB 目标可守恒。"""
    pages = [make_page(1, rasters=[
        make_image(PAGE_AREA * 0.5, dpi=300.0, width=4000, height=3000,
                   data_ref=f"images/i{n}.bin") for n in range(8)])]
    plan = run(pages, prefs(target_mb=2.6), irreducible=100_000)
    from app.pipeline.decide import _unique_stream_total
    assert _unique_stream_total(plan.pages, pages) <= plan.raster_budget * CFG.budget_overshoot_tolerance


def test_08b_floor_bound_budget_still_emits_plan():
    """下限触底时（质量优先语义）：Σ 可超预算但必须仍产出 plan（收敛环裁决），
    且参数精确停在下限，不越界。"""
    pages = [make_page(1, rasters=[
        make_image(PAGE_AREA * 0.5, dpi=300.0, width=4000, height=3000,
                   data_ref=f"images/i{n}.bin") for n in range(8)])]
    plan = run(pages, prefs(target_mb=1.0), irreducible=100_000)  # 下限总成本 > 1MB
    for rp in plan.pages[0].raster_plans:
        assert rp.quality >= CFG.quality_floor
        assert rp.target_dpi >= min(CFG.dpi_floor, int(rp.original_dpi))
    from app.pipeline.decide import _unique_stream_total
    assert _unique_stream_total(plan.pages, pages) > plan.raster_budget  # 触底证据


def test_09_convergence_adjustment_on_overshoot():
    """必测 9：上一轮超标 >30% → aggressiveness 上升 step_large。"""
    pages = [make_page(1, rasters=[make_image(PAGE_AREA * 0.5)])]
    plan1 = run(pages)  # 得到本轮 budget 与首轮激进度
    previous = CompressionResult(
        total_raster_bytes=int(plan1.raster_budget * 1.4),  # gap 40% > 30%
        per_image_results=[], round_number=1,
        base_aggressiveness=plan1.base_aggressiveness)
    plan2 = run(pages, previous=previous, round_number=2)
    assert plan2.base_aggressiveness == pytest.approx(
        min(1.0, plan1.base_aggressiveness + CFG.aggressiveness_step_large))
    assert plan2.round_number == 2


def test_10_target_too_small_raises():
    """必测 10：不可削减开销超过目标 → TARGET_TOO_SMALL。"""
    pages = [make_page(1, rasters=[make_image(PAGE_AREA * 0.5)])]
    with pytest.raises(PhaseError) as excinfo:
        run(pages, prefs(target_mb=0.1), irreducible=2_000_000)  # 2MB 不可削减 vs 0.1MB 目标
    assert excinfo.value.code == "TARGET_TOO_SMALL"
    assert excinfo.value.phase == "decide"
    assert excinfo.value.recoverable is False


# ================================================================ 扩展断言

def test_budget_uses_irreducible_floor_not_total_fixed():
    """§7.1 修订核心：重矢量页在 rasterize 可用时不得被误判 TARGET_TOO_SMALL。

    矢量 keep 成本 21MB > 15MB 目标，但 rasterize 下限远小 → 必须能出 Plan。"""
    heavy_vec = make_vector(7_000_000, PAGE_AREA * 0.7, original_bytes=21_000_000)
    pages = [make_page(1, rasters=[make_image(PAGE_AREA * 0.2)], vectors=[heavy_vec])]
    plan = run(pages, prefs(target_mb=15.0), irreducible=300_000)  # 不抛 TARGET_TOO_SMALL
    vp = plan.pages[0].vector_plans[0]
    assert vp.strategy == "rasterize"
    assert plan.raster_budget > 0


def test_vector_tiny_area_always_keep():
    """§6.3：area_ratio < 0.1 无论复杂度多高永远 keep。"""
    page = make_page(1, vectors=[make_vector(5_000_000, PAGE_AREA * 0.05)])
    assert run([page]).pages[0].vector_plans[0].strategy == "keep"


def test_vector_midarea_high_cp_only_simplify():
    """§6.3：0.1 ≤ area < 0.3 时即使控制点 ≥ rasterize 阈值也只能 simplify。"""
    page = make_page(1, vectors=[make_vector(500_000, PAGE_AREA * 0.2)])
    assert run([page]).pages[0].vector_plans[0].strategy == "simplify"


def test_aggressive_override_lowers_quality():
    pages_normal = [make_page(1, rasters=[make_image(PAGE_AREA * 0.5)])]
    q_normal = run(pages_normal).pages[0].raster_plans[0].quality
    user = prefs(overrides=[PageOverride(page_number=1, action="aggressive")])
    q_aggr = run(pages_normal, user).pages[0].raster_plans[0].quality
    assert q_aggr < q_normal


def test_shared_data_ref_counted_once_in_budget():
    """共享 data_ref 的 N 处摆放在预算中只计一次（取最大需求）。"""
    tile = [make_image(PAGE_AREA * 0.02, data_ref="images/tile.bin",
                       width=256, height=256, original_bytes=30_000, dpi=200.0)
            for _ in range(50)]  # 同一贴图平铺 50 次
    pages = [make_page(1, rasters=tile)]
    plan = run(pages)
    from app.pipeline.decide import _unique_stream_total
    unique = _unique_stream_total(plan.pages, pages)
    per_placement_sum = sum(rp.estimated_bytes for rp in plan.pages[0].raster_plans)
    assert unique == max(rp.estimated_bytes for rp in plan.pages[0].raster_plans)
    assert per_placement_sum >= unique * 50  # 逐摆放口径会放大 50 倍


def test_f1_cost_guard_degrades_expensive_rasterize():
    """F1（2026-07-04）：rasterize 估算 ≥ keep 成本 → 降级 simplify。

    大 bbox（0.9 页面）整页级矢量：rasterize@200dpi ≈ 344KB ≥ keep 320KB → 降级。
    （keep 阈值随 2026-07-05 JPEG 系数标定同步：bpp(q80) 0.136 → 0.099）"""
    big_cheap_keep = make_vector(120_000, PAGE_AREA * 0.9, original_bytes=320_000)
    plan = run([make_page(1, vectors=[big_cheap_keep])])
    vp = plan.pages[0].vector_plans[0]
    assert vp.strategy == "simplify"
    assert vp.simplify_tolerance == CFG.simplify_tolerance_conservative  # area>0.6 主图容差


def test_f1_cost_guard_keeps_justified_rasterize():
    """前提成立时（小 bbox 高密度，rasterize 远便宜）守卫不触发。"""
    dense_small = make_vector(1_000_000, PAGE_AREA * 0.35, original_bytes=3_000_000)
    plan = run([make_page(1, vectors=[dense_small])])
    vp = plan.pages[0].vector_plans[0]
    assert vp.strategy == "rasterize"
    assert vp.rasterize_dpi == CFG.rasterize_dpi_medium


# ================================================================ F1 真实样本（portfolio_2）

@pytest.fixture(scope="module")
def p2_prepared(tmp_path_factory):
    """portfolio_2（重矢量样本）split→extract→classify→preprocess，module 级只跑一次。"""
    import uuid
    from pathlib import Path

    from app.contracts import PipelineContext
    from app.pipeline.classify import classify_pages
    from app.pipeline.extract import extract_elements
    from app.pipeline.preprocess import preprocess as run_preprocess
    from app.pipeline.split import split_pdf

    src = Path(__file__).parent / "fixtures" / "portfolios" / "portfolio_2.pdf"
    assert src.exists(), "缺少真实样本 portfolio_2.pdf（规则 6）"
    ws = tmp_path_factory.mktemp("p2_decide") / "ws"
    ws.mkdir()
    ctx = PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))
    raw = split_pdf(str(src), ctx)
    pages, fonts, meta = extract_elements(raw, str(src), ctx)
    classified = classify_pages(pages)
    pre = run_preprocess(pages, fonts, meta, prefs(10.0), ctx)
    return {"pages": pages, "classified": classified, "pre": pre}


def test_f1_p2_guard_fires_on_real_heavy_pages(p2_prepared):
    """实测锚点：p2 存在 cp≥rasterize 阈值且面积达标、却被降级 simplify 的组（守卫触发）。"""
    plan = decide_compression(p2_prepared["classified"], p2_prepared["pages"], prefs(15.0),
                              p2_prepared["pre"], None, 1, CFG)
    guard_hits = sum(
        1
        for pp, pg in zip(plan.pages, p2_prepared["pages"])
        for vp in pp.vector_plans
        if vp.strategy == "simplify"
        and pg.vector_paths[vp.path_group_index].control_point_count >= CFG.vector_rasterize_threshold
        and vp.area_ratio >= CFG.vector_rasterize_min_area_ratio
    )
    assert guard_hits >= 1  # 实测 2026-07-04：3 次
    # 前提成立的组仍应保留 rasterize（实测 3 组）
    assert any(vp.strategy == "rasterize" for pp in plan.pages for vp in pp.vector_plans)


def test_f1_p2_feasible_targets_produce_plan(p2_prepared):
    """实测锚点：F1 后 10/15/20MB 档 plan 生成成功、budget 为正（比预期的 15/20 更好）。"""
    for target in (10.0, 15.0, 20.0):
        plan = decide_compression(p2_prepared["classified"], p2_prepared["pages"],
                                  prefs(target), p2_prepared["pre"], None, 1, CFG)
        assert plan.raster_budget > 0


def test_f1_p2_5mb_honestly_too_small(p2_prepared):
    """铁律 7：5MB 对 650 万控制点 + 77MB 图像的文件不可达 → 如实 TARGET_TOO_SMALL。"""
    with pytest.raises(PhaseError) as excinfo:
        decide_compression(p2_prepared["classified"], p2_prepared["pages"], prefs(5.0),
                           p2_prepared["pre"], None, 1, CFG)
    assert excinfo.value.code == "TARGET_TOO_SMALL"


def test_first_round_without_previous_result_ok():
    """previous_result=None 时使用基准参数表推导，不崩溃（验证清单条目）。
    期望值直接取自 config 预设（2026-07-04 目视标定后 screen=0.3），不硬编码。"""
    pages = [make_page(1, rasters=[make_image(PAGE_AREA * 0.5)])]
    for target, preset in CFG.presets.items():
        plan = run(pages, prefs(target=target))
        assert plan.base_aggressiveness == pytest.approx(preset.aggressiveness)
