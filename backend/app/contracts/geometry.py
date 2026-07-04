"""基础几何类型（全 Phase 共用）。

坐标单位：PDF 用户单位 point（1/72 英寸），原点与方向遵循 PyMuPDF 提取时的页面坐标系。
"""

from __future__ import annotations

from pydantic import BaseModel, Field


class BBox(BaseModel):
    """元素外接矩形。"""

    x: float
    y: float
    w: float = Field(ge=0)
    h: float = Field(ge=0)

    @property
    def area(self) -> float:
        return self.w * self.h

    def area_ratio(self, page_area: float) -> float:
        """面积占比 ∈ [0, 1]（铁律 4 的基础量）。

        元素可能超出页面边界（出血/裁切外内容），按 1.0 封顶；
        page_area 非正时返回 0.0（防御除零，不让几何异常炸掉流水线）。
        """
        if page_area <= 0:
            return 0.0
        return min(1.0, self.area / page_area)
