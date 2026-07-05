"""Phase 5（元素提取）输出的元素级类型。

data_ref 规范见《数据契约.md》§1.6：相对 tmp_workspace 的 POSIX 风格相对路径，
只能经 PipelineContext.resolve_ref() 解析，禁止手工拼接。
"""

from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

from app.contracts.geometry import BBox


class RasterImage(BaseModel):
    """位图元素。data_ref 指向原始图片字节流（images/img_{page}_{index}.bin）。"""

    data_ref: str  # 不直接存 bytes，避免 Pydantic 模型膨胀
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    dpi: float = Field(gt=0)  # 反算：width / (bbox.w / 72)
    color_space: str
    has_alpha: bool  # ≡ (alpha_type == "translucent")，兼容性快捷字段
    # 修订 2026-07-04（第 2 批）：三态 alpha。none=无 alpha 通道；
    # opaque=有 SMask 但 ≥99% 像素为 255（伪透明，Phase 8 可安全转 JPEG）；
    # translucent=真透明（Phase 8 必须 PNG）。
    # Producer = Phase 5 SMask 像素采样统计（数百点采样，不扫全图）。
    alpha_type: Literal["none", "opaque", "translucent"]
    # 修订 2026-07-04（质量优化 2）：灰度检测。Producer = Phase 5 像素采样
    # （容差 2 吸收 JPEG 色度噪声）；Consumer = Phase 9 转 L/LA 编码（3→1 通道）
    is_grayscale: bool = False
    # 修订 2026-07-04：Producer = Phase 5 写 data_ref 文件时记录 len(bytes)；
    # Consumer = Phase 8 预算页权重 + RasterPlan.original_bytes（decide 纯函数不能 stat 文件）
    original_bytes: int = Field(ge=0)
    bbox: BBox


class VectorPath(BaseModel):
    """矢量路径组。path_data_ref 指向 pickle 序列化的路径数据。"""

    path_data_ref: str
    control_point_count: int = Field(ge=0)
    stroke_color: Optional[str] = None
    fill_color: Optional[str] = None
    # 修订 2026-07-04：Producer = Phase 5 写 path_data_ref 文件时记录；
    # Consumer = Phase 8 VectorPlan.original_bytes / 矢量体积估算基数
    original_bytes: int = Field(ge=0)
    bbox: BBox  # 组内所有路径的最小外接矩形


class TextBlock(BaseModel):
    content: str
    font_ref: str  # 字体名，对应 Phase 5 返回的 fonts_used 字典 key
    font_size: float = Field(gt=0)
    bbox: BBox


class XObject(BaseModel):
    """Form XObject 记录（诊断/重组保真用途）。内部图片已展开进 raster_images。"""

    ref_id: str
    xobject_type: Literal["image", "form"]
    bbox: BBox


class Annotation(BaseModel):
    anno_type: Literal["link", "bookmark", "comment", "form_field"]
    target: str  # link → URI；bookmark → 目标页描述
    bbox: BBox
