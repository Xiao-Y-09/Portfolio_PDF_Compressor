"""用户偏好类型（API 层进入，贯穿全流水线）。"""

from __future__ import annotations

from typing import List, Literal, Optional

from pydantic import BaseModel, Field

CompressionTarget = Literal["screen", "print", "email"]


class PageOverride(BaseModel):
    """用户 review 界面对单页的操作。"""

    page_number: int = Field(ge=1)
    action: Literal["skip", "aggressive", "reclassify"]
    new_type: Optional[str] = None  # 仅 action="reclassify" 时使用


class UserPreferences(BaseModel):
    target_size_mb: float = Field(gt=0)  # 5 / 10 / 15 / 20
    compression_target: CompressionTarget
    per_page_overrides: List[PageOverride] = Field(default_factory=list)
