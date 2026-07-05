"""Phase 9（压缩执行）输出类型。

消费方有二：Orchestrator（判断 gap 是否达标）+ 下一轮 Phase 8
（previous_result 反馈调整激进度）。
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from app.contracts.plan import CompressionTier


class ImageResult(BaseModel):
    page_number: int = Field(ge=1)
    image_index: int = Field(ge=0)
    compressed_bytes: int = Field(ge=0)  # 压缩失败用原图凑数时 = original_bytes
    compressed_data_ref: str  # compressed/img_{page}_{index}.{ext}
    actual_dpi: float = Field(gt=0)
    actual_quality: int = Field(ge=0, le=100)


class CompressionResult(BaseModel):
    total_raster_bytes: int = Field(ge=0)  # Σ compressed_bytes
    per_image_results: List[ImageResult] = Field(default_factory=list)
    round_number: int = Field(ge=1)
    # 修订 2026-07-04：Phase 9 从 CompressionPlan.base_aggressiveness 原样回带
    # （不做任何计算），供下一轮 decide 读取为 prev_aggressiveness
    base_aggressiveness: float = Field(ge=0.0, le=1.0)
    # 修订 2026-07-04（Tier 系统）：Phase 9 从 CompressionPlan.tier 原样回带
    # （同 base_aggressiveness 通路）。Consumer = orchestrator（任务返回 dict 的
    # tier_used/warning）→ Phase 12 API 透传 → Phase 14 前端警告展示
    tier_used: CompressionTier = "in_place"
