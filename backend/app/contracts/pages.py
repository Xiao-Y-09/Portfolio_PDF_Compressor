"""页级类型：RawPage（Phase 4 输出）、PageElements（Phase 5 输出）、
ClassifiedPage（Phase 6 输出）。"""

from __future__ import annotations

from typing import List, Literal

from pydantic import BaseModel, Field

from app.contracts.elements import Annotation, RasterImage, TextBlock, VectorPath, XObject


class RawPage(BaseModel):
    """Phase 4 拆页输出。页号从 1 开始（对齐 PDF 传统）。"""

    page_number: int = Field(ge=1)
    page_width: float = Field(gt=0)   # PDF 用户单位（points）
    page_height: float = Field(gt=0)
    page_bytes_ref: str  # pages/page_{N}.pdf（相对 tmp_workspace）


class PageElements(BaseModel):
    """Phase 5 提取输出。单页提取失败 → 各列表为空（不阻塞流水线）。"""

    page_number: int = Field(ge=1)
    page_width: float = Field(gt=0)
    page_height: float = Field(gt=0)
    raster_images: List[RasterImage] = Field(default_factory=list)
    vector_paths: List[VectorPath] = Field(default_factory=list)
    text_blocks: List[TextBlock] = Field(default_factory=list)
    xobjects: List[XObject] = Field(default_factory=list)
    annotations: List[Annotation] = Field(default_factory=list)


class ClassifiedPage(BaseModel):
    """Phase 6 分类输出（V1 启发式 / V2 AI，契约不变）。"""

    page_number: int = Field(ge=1)
    page_type: Literal["photo_heavy", "text_heavy", "chart", "mixed"]
    dominant_element: Literal["raster", "vector", "text"]
    raster_area_ratio: float = Field(ge=0.0, le=1.0)
    vector_area_ratio: float = Field(ge=0.0, le=1.0)
    text_area_ratio: float = Field(ge=0.0, le=1.0)
    complexity_score: float = Field(ge=0.0, le=1.0)  # 0=分类明确，1=非常模糊（前端提示用户确认）
