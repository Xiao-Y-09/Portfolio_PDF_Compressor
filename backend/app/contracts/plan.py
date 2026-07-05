"""Phase 8（决策引擎）输出类型。

输出约束（《压缩决策引擎.md》第 3 章）：
- skip=true 的页面 raster_plans / vector_plans 为空、whole_page_plan 为 None
- target_dpi ≤ original_dpi（只降不升，铁律 3；由 decide.py 逻辑保证）
- quality ∈ [0,100]（契约层）；决策层再收紧到 [quality_floor, quality_ceiling]，
  Tier 3 收紧到 [tier3_quality_floor, quality_ceiling]（config 相关界一律决策层负责，
  契约层只管通用界——《数据契约.md》§1.5 约定）
- 所有位图基底 output_format="jpeg"（修订 2026-07-04 架构决策 A2：真透明的 SMask
  保真由 assemble 层原地替换保证，见《压缩决策引擎.md》§5.4）
- whole_page_plan 置位的页面 raster_plans / vector_plans 为空（model_validator 强制，
  修订 2026-07-04 Tier 系统，《压缩决策引擎.md》§8.6）
- Σ estimated_bytes ≤ raster_budget × 1.1（预算守恒）
"""

from __future__ import annotations

from typing import List, Literal, Optional, Tuple

from pydantic import BaseModel, Field, model_validator

# 修订 2026-07-04（Tier 系统，《压缩决策引擎.md》§8.5，用户裁决 3：语义命名）。
# 顺序即降级方向：in_place = 最保真，full_raster = 最极限。
# 索引可比较：判断"当前 Tier 是否已至最深"等用 COMPRESSION_TIERS.index(tier)，
# 不做硬编码字符串比较。COMPRESSION_TIERS 与 Literal 同源同序（测试断言同步）。
CompressionTier = Literal["in_place", "hybrid", "full_raster"]
COMPRESSION_TIERS: Tuple[CompressionTier, ...] = ("in_place", "hybrid", "full_raster")


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


class WholePageRasterPlan(BaseModel):
    """整页光栅化计划（修订 2026-07-04，Tier 系统，《压缩决策引擎.md》§8.6）。

    契约层为通用界；Tier 相关界（Tier 2: [dpi_floor, dpi_ceiling] / Tier 3:
    [tier3_dpi_floor, tier3_dpi_ceiling]）依赖 config 值，按《数据契约.md》§1.5
    既有约定由决策层收紧 + 测试断言（与 RasterPlan.quality 的处理方式一致）。
    """

    dpi: int = Field(gt=0)
    quality: int = Field(ge=0, le=100)
    estimated_bytes: int = Field(ge=0)  # 预算口径估算


class PagePlan(BaseModel):
    page_number: int = Field(ge=1)
    skip: bool = False  # 用户标记不压缩 → true，所有 plan 字段为空/None
    raster_plans: List[RasterPlan] = Field(default_factory=list)
    vector_plans: List[VectorPlan] = Field(default_factory=list)
    # 修订 2026-07-04（Tier 系统）：置位 → 本页整页光栅化。
    # Producer = decide（Tier 2 选页 / Tier 3 全选）；
    # Consumer = Phase 9 渲染整页 JPEG + Phase 10 替换页内容。
    whole_page_plan: Optional[WholePageRasterPlan] = None

    @model_validator(mode="after")
    def _check_whole_page_exclusivity(self) -> "PagePlan":
        """契约层强约束（构造即校验，不靠下游代码自觉）：
        ① whole_page_plan 置位时 raster_plans/vector_plans 必须为空（全页由单计划覆盖）；
        ② skip 页在任何 Tier 下都不被触碰（铁律 4 在契约层的落点）。"""
        if self.whole_page_plan is not None:
            if self.raster_plans or self.vector_plans:
                raise ValueError(
                    "whole_page_plan 置位时 raster_plans/vector_plans 必须为空"
                    "（整页由单计划覆盖，不允许混用元素级计划）"
                )
            if self.skip:
                raise ValueError(
                    "skip 页在任何 Tier 下都不被触碰，whole_page_plan 必须为 None"
                )
        return self


class CompressionPlan(BaseModel):
    raster_budget: int = Field(gt=0)  # = target_bytes - total_fixed_bytes（≤0 时 decide 抛 TARGET_TOO_SMALL，不会构造本对象）
    pages: List[PagePlan] = Field(default_factory=list)
    round_number: int = Field(ge=1)  # 第几轮收敛循环
    # 修订 2026-07-04：本轮决策使用的全局激进度。Phase 9 原样回带至
    # CompressionResult，闭合收敛反馈环（下一轮 decide 的 prev_aggressiveness 来源）
    base_aggressiveness: float = Field(ge=0.0, le=1.0)
    # 修订 2026-07-04（Tier 系统）：本 Plan 所属层级。Producer = orchestrator 传入
    # decide；Consumer = Phase 9（执行整页计划时判定）+ 回带 CompressionResult.tier_used
    tier: CompressionTier = "in_place"
