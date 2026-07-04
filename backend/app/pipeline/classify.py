"""Phase 6（运行别名 Phase C）：页面分类器 V1（启发式规则）。

# V2: replace HeuristicClassifier with AIClassifier
# AIClassifier 应实现 classify_pages(pages) → List[ClassifiedPage]
# 输入输出与启发式版本完全一致

分类不追求百分百准确——用户会在 review 界面手动修正；目标是"大部分时候是对的"。
本模块只做纯算术（面积占比 + 元素计数），不读文件、不依赖 annotations
（真实样本 annotations 普遍为空，不可作为分类信号）。

全部阈值来自 settings.classifier（config.yaml `classifier:` 段，
2026-07-04 用户批准迁入），代码内无魔法数字。
"""

from __future__ import annotations

from typing import List, Optional, Tuple

from app.config.settings import ClassifierConfig, get_settings
from app.contracts import ClassifiedPage, PageElements


def classify_pages(
    pages: List[PageElements],
    config: Optional[ClassifierConfig] = None,
) -> List[ClassifiedPage]:
    """config 缺省时读全局 settings.classifier；测试可显式注入。"""
    cfg = config if config is not None else get_settings().classifier
    return [_classify_page(p, cfg) for p in pages]


def _area_ratios(page: PageElements) -> Tuple[float, float, float]:
    """三类元素的面积占比，各自封顶 1.0（元素可重叠，直接求和可能 >1）。"""
    page_area = page.page_width * page.page_height
    if page_area <= 0:
        return 0.0, 0.0, 0.0
    raster = min(sum(i.bbox.area for i in page.raster_images) / page_area, 1.0)
    vector = min(sum(v.bbox.area for v in page.vector_paths) / page_area, 1.0)
    text = min(sum(t.bbox.area for t in page.text_blocks) / page_area, 1.0)
    return raster, vector, text


def _classify_page(page: PageElements, cfg: ClassifierConfig) -> ClassifiedPage:
    raster_ratio, vector_ratio, text_ratio = _area_ratios(page)
    vector_complexity = sum(v.control_point_count for v in page.vector_paths)
    has_hi_dpi_raster = any(i.dpi > cfg.photo_min_dpi for i in page.raster_images)

    # 规则按优先级判定（手册 Phase 6）
    if raster_ratio > cfg.photo_area_threshold and has_hi_dpi_raster:
        page_type, dominant = "photo_heavy", "raster"
    elif (vector_ratio > cfg.chart_vector_area_threshold
          and vector_complexity > cfg.chart_complexity_threshold
          and raster_ratio < cfg.chart_max_raster_ratio):
        page_type, dominant = "chart", "vector"
    elif (len(page.text_blocks) > cfg.text_block_count_threshold
          and raster_ratio < cfg.text_max_raster_ratio
          and vector_complexity < cfg.text_max_vector_complexity):
        page_type, dominant = "text_heavy", "text"
    else:
        page_type = "mixed"
        # 三者面积占比最大者；并列时按 raster > vector > text 的顺序取先者（确定性）
        dominant = max(
            (("raster", raster_ratio), ("vector", vector_ratio), ("text", text_ratio)),
            key=lambda kv: kv[1],
        )[0]

    # 分类边界模糊度：离任一决策阈值越近分越高（手册示例：|0.48-0.5| → 0.8）
    boundary_distance = min(
        abs(raster_ratio - cfg.photo_area_threshold),
        abs(vector_ratio - cfg.chart_vector_area_threshold),
        abs(raster_ratio - cfg.text_max_raster_ratio),
    )
    complexity_score = max(0.0, min(1.0, 1.0 - boundary_distance * cfg.complexity_score_slope))

    return ClassifiedPage(
        page_number=page.page_number,
        page_type=page_type,
        dominant_element=dominant,
        raster_area_ratio=raster_ratio,
        vector_area_ratio=vector_ratio,
        text_area_ratio=text_ratio,
        complexity_score=complexity_score,
    )
