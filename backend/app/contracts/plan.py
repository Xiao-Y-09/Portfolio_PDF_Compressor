"""Phase 8（决策引擎）输出类型。

输出约束（《压缩决策引擎.md》第 3 章）：
- skip=true 的页面 raster_plans / vector_plans 为空
- target_dpi ≤ original_dpi（只降不升，铁律 3；由 decide.py 逻辑保证）
- quality ∈ [0,100]（契约层）；决策层再收紧到 [quality_floor, quality_ceiling]
- has_alpha 图片 output_format="png"
- Σ estimated_bytes ≤ raster_budget × 1.1（预算守恒）
"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field


class RasterPlan(BaseModel):
    image_index: int = Field(ge=0)  # 对应 PageElements.raster_images 下标
    area_ratio: float = Field(ge=0.0, le=1.0)
    original_dpi: float = Field(gt=0)
    original_bytes: int = Field(ge=0)
    target_dpi: int = Field(gt=0)
    quality: int = Field(ge=0, le=100)
    max_dimension: int = Field(gt=0)  # 最长边像素上限（像素天花板兜底）
    output_format: Literal["jpeg", "png"]
    estimated_bytes: int = Field(ge=0)


class VectorPlan(BaseModel):
    path_group_index: int = Field(ge=0)  # 对应 PageElements.vector_paths 下标
    area_ratio: float = Field(ge=0.0, le=1.0)
    original_bytes: int = Field(ge=0)
    strategy: Literal["keep", "simplify", "rasterize"]
    rasterize_dpi: Optional[int] = Field(default=None, gt=0)        # 仅 rasterize
    simplify_tolerance: Optional[float] = Field(default=None, gt=0)  # 仅 simplify


class PagePlan(BaseModel):
    page_number: int = Field(ge=1)
    skip: bool = False  # 用户标记不压缩 → true，两个 plan 列表为空
    raster_plans: List[RasterPlan] = Field(default_factory=list)
    vector_plans: List[VectorPlan] = Field(default_factory=list)


class CompressionPlan(BaseModel):
    raster_budget: int = Field(gt=0)  # = target_bytes - total_fixed_bytes（≤0 时 decide 抛 TARGET_TOO_SMALL，不会构造本对象）
    pages: List[PagePlan] = Field(default_factory=list)
    round_number: int = Field(ge=1)  # 第几轮收敛循环
    # 修订 2026-07-04：本轮决策使用的全局激进度。Phase 9 原样回带至
    # CompressionResult，闭合收敛反馈环（下一轮 decide 的 prev_aggressiveness 来源）
    base_aggressiveness: float = Field(ge=0.0, le=1.0)
