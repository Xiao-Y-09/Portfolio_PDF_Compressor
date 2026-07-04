"""字体类型：FontUsage（Phase 5 输出）、SubsetResult（Phase 7 输出）。

关注点（Set 的 JSON 序列化）：chars_used 是 Set[str]，Pydantic v2 在 JSON 模式下
序列化为数组、反序列化时还原为 set。这里显式加 field_serializer 输出**排序后**的
列表——保证相同字符集的序列化结果字节级一致（决策纯函数与 session 存取都受益于
确定性序列化）。
"""

from __future__ import annotations

from typing import List, Optional, Set

from pydantic import BaseModel, Field, field_serializer


class FontUsage(BaseModel):
    """全 PDF 级字体使用表条目（跨页累计字符集）。"""

    font_name: str
    data_ref: Optional[str] = None  # fonts/font_{name}.ttf；提取失败为 None（Phase 7 跳过子集化）
    chars_used: Set[str] = Field(default_factory=set)

    @field_serializer("chars_used")
    def _serialize_chars_sorted(self, chars: Set[str]) -> List[str]:
        return sorted(chars)


class SubsetResult(BaseModel):
    """字体子集化结果。无法子集化时 subsetted_bytes = original_bytes。"""

    font_name: str
    original_bytes: int = Field(ge=0)
    subsetted_bytes: int = Field(ge=0)
    subsetted_data_ref: str  # fonts_subsetted/font_{name}.ttf
