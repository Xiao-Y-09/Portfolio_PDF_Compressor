"""Phase 6 分类器测试。

分类器是纯算术函数（消费 PageElements 契约对象），主体用可控的合成契约对象
做确定性断言（这不是"假 PDF"——分类逻辑不接触 PDF 本身）；另用最小的真实
作品集样本做一次 split→extract→classify 集成冒烟。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.config.settings import ClassifierConfig
from app.contracts import BBox, PageElements, PipelineContext, RasterImage, TextBlock, VectorPath
from app.pipeline.classify import classify_pages

# 测试用 config 显式注入（不依赖全局 config.yaml；阈值全部经字段引用，无硬编码）
CFG = ClassifierConfig()

PAGE_W, PAGE_H = 595.0, 842.0  # 面积 500,990 pt²


def make_raster(area_pt2: float, dpi: float = 300.0) -> RasterImage:
    w = area_pt2 ** 0.5
    return RasterImage(
        data_ref="images/img_001_000.bin", width=2000, height=2000, dpi=dpi,
        color_space="RGB", has_alpha=False, alpha_type="none",
        original_bytes=1_000_000, bbox=BBox(x=0, y=0, w=w, h=area_pt2 / w),
    )


def make_vector(control_points: int, area_pt2: float) -> VectorPath:
    w = area_pt2 ** 0.5
    return VectorPath(
        path_data_ref="vectors/vec_001_000.bin", control_point_count=control_points,
        stroke_color="#000000", fill_color=None, original_bytes=50_000,
        bbox=BBox(x=0, y=0, w=w, h=area_pt2 / w),
    )


def make_texts(n: int) -> list[TextBlock]:
    return [
        TextBlock(content=f"line {i}", font_ref="F", font_size=12.0,
                  bbox=BBox(x=72, y=72 + i * 14, w=200.0, h=12.0))
        for i in range(n)
    ]


def make_page(number: int, rasters=(), vectors=(), texts=()) -> PageElements:
    return PageElements(
        page_number=number, page_width=PAGE_W, page_height=PAGE_H,
        raster_images=list(rasters), vector_paths=list(vectors), text_blocks=list(texts),
    )


PAGE_AREA = PAGE_W * PAGE_H


# ---------------------------------------------------------------- 确定性规则（手册 5 页样本：4 页有确定标签 + 1 页边界）

def test_five_page_sample_classification():
    hi_dpi = CFG.photo_min_dpi * 3
    pages = [
        # 1: 大图（photo 阈值 +0.06）+ 高 DPI → photo_heavy
        make_page(1, rasters=[make_raster(
            PAGE_AREA * (CFG.photo_area_threshold + 0.06), dpi=hi_dpi)]),
        # 2: 矢量（chart 阈值 +0.12）、复杂度 4× 阈值、无位图 → chart
        make_page(2, vectors=[make_vector(
            CFG.chart_complexity_threshold * 4,
            PAGE_AREA * (CFG.chart_vector_area_threshold + 0.12))]),
        # 3: 文字块数超阈值 5 个、无位图、无矢量 → text_heavy
        make_page(3, texts=make_texts(CFG.text_block_count_threshold + 5)),
        # 4: 位图占比 = photo 阈值 × 0.6（不达标）+ 少量文字 → mixed，dominant=raster
        make_page(4, rasters=[make_raster(
            PAGE_AREA * CFG.photo_area_threshold * 0.6, dpi=hi_dpi)], texts=make_texts(5)),
        # 5: 位图贴近 photo 阈值（-0.02）→ mixed 且 complexity_score 高
        make_page(5, rasters=[make_raster(
            PAGE_AREA * (CFG.photo_area_threshold - 0.02), dpi=hi_dpi)]),
    ]
    result = classify_pages(pages, config=CFG)

    assert [c.page_type for c in result[:4]] == [
        "photo_heavy", "chart", "text_heavy", "mixed"]  # ≥4/5 正确（手册验收线）
    assert [c.dominant_element for c in result[:4]] == [
        "raster", "vector", "text", "raster"]
    # 第 5 页：边界页,分数应提示"请确认"（手册示例：距阈值 0.02 → 1 - 0.02×slope）
    assert result[4].page_type == "mixed"
    expected_score = 1.0 - 0.02 * CFG.complexity_score_slope
    assert result[4].complexity_score >= expected_score - 0.05


def test_photo_rule_requires_high_dpi():
    """面积够大但 DPI ≤ 阈值 → 不算 photo_heavy（规则含 DPI 条件）。"""
    low_dpi = make_page(1, rasters=[make_raster(
        PAGE_AREA * (CFG.photo_area_threshold + 0.2), dpi=CFG.photo_min_dpi * 0.7)])
    assert classify_pages([low_dpi], config=CFG)[0].page_type == "mixed"


def test_chart_rule_rejected_when_raster_heavy():
    """矢量达标但位图占比 ≥ chart_max_raster_ratio → 不算 chart。"""
    page = make_page(
        1,
        vectors=[make_vector(CFG.chart_complexity_threshold * 4,
                             PAGE_AREA * (CFG.chart_vector_area_threshold + 0.2))],
        rasters=[make_raster(PAGE_AREA * (CFG.chart_max_raster_ratio + 0.05),
                             dpi=CFG.photo_min_dpi * 3)])
    assert classify_pages([page], config=CFG)[0].page_type == "mixed"


def test_empty_page_is_mixed_with_zero_ratios():
    result = classify_pages([make_page(1)], config=CFG)[0]
    assert result.page_type == "mixed"
    assert result.raster_area_ratio == 0.0
    assert result.vector_area_ratio == 0.0
    assert result.text_area_ratio == 0.0


def test_ratios_clamped_when_elements_overlap():
    """重叠元素面积和可 >1，占比必须封顶 1.0（契约上限）。"""
    page = make_page(1, rasters=[make_raster(PAGE_AREA * 0.8, dpi=300)] * 3)
    result = classify_pages([page], config=CFG)[0]
    assert result.raster_area_ratio == 1.0


def test_scores_and_fields_always_valid():
    pages = [
        make_page(1, rasters=[make_raster(PAGE_AREA * r, dpi=200)])
        for r in (0.05, 0.15, 0.3, 0.5, 0.9)
    ]
    for c in classify_pages(pages, config=CFG):
        assert 0.0 <= c.complexity_score <= 1.0
        assert c.dominant_element in ("raster", "vector", "text")
        assert c.page_type in ("photo_heavy", "text_heavy", "chart", "mixed")


# ---------------------------------------------------------------- 真实样本集成冒烟

def test_classify_on_smallest_real_sample(tmp_path):
    from app.pipeline.extract import extract_elements
    from app.pipeline.split import split_pdf

    samples = sorted(Path(__file__).parent.glob("fixtures/portfolios/*.pdf"))
    assert samples, "缺少真实样本（规则 6）"
    src = min(samples, key=lambda p: p.stat().st_size)

    ws = tmp_path / "ws"; ws.mkdir()
    ctx = PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))
    raw = split_pdf(str(src), ctx)
    pages, _fonts, _meta = extract_elements(raw, str(src), ctx)
    classified = classify_pages(pages)

    assert len(classified) == len(pages)
    assert [c.page_number for c in classified] == [p.page_number for p in pages]
    for c in classified:
        assert 0.0 <= c.complexity_score <= 1.0
        assert 0.0 <= c.raster_area_ratio <= 1.0
