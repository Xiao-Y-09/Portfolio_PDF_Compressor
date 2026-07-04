"""Phase 7（确定性预处理）输出类型。

total_fixed_bytes 是 Phase 8 预算公式的直接输入：
raster_budget = target_bytes - total_fixed_bytes。
估算误差 ±10% 可接受，由收敛循环兜住。
"""

from __future__ import annotations

from typing import List

from pydantic import BaseModel, Field

from app.contracts.fonts import SubsetResult


class PerPageFixedBytes(BaseModel):
    page_number: int = Field(ge=1)
    vector_bytes: int = Field(ge=0)      # 该页矢量序列化数据大小之和
    text_bytes: int = Field(ge=0)        # Σ len(content.utf8) × 1.5（含结构开销）
    annotation_bytes: int = Field(ge=0)  # 每个注释估 100-500 字节


class PreprocessResult(BaseModel):
    subsetted_fonts: List[SubsetResult] = Field(default_factory=list)
    total_fixed_bytes: int = Field(ge=0)
    per_page_fixed_bytes: List[PerPageFixedBytes] = Field(default_factory=list)
    stripped_metadata_fields: List[str] = Field(default_factory=list)  # 默认清 author/creator，保留 title
