"""数据契约层（Phase 3）——整个系统的"宪法"。

铁律 6：本包中的类型一旦定版，Phase 4-10 只能消费不能修改。字段不足时必须
回到本包 + docs/数据契约.md 显式修订（先修文档再改代码），禁止下游"顺手加字段"。

全部类型为 Pydantic v2 BaseModel（model_dump / model_validate），禁止裸 dict
与 dataclass。data_ref 路径规范见 pipeline.py 模块 docstring 与《数据契约.md》§1.6。
"""

from app.contracts.elements import (
    Annotation,
    RasterImage,
    TextBlock,
    VectorPath,
    XObject,
)
from app.contracts.fonts import FontUsage, SubsetResult
from app.contracts.geometry import BBox
from app.contracts.pages import ClassifiedPage, PageElements, RawPage
from app.contracts.pipeline import (
    ConvergenceStep,
    PhaseError,
    PhaseErrorData,
    PipelineContext,
)
from app.contracts.plan import CompressionPlan, PagePlan, RasterPlan, VectorPlan
from app.contracts.preferences import CompressionTarget, PageOverride, UserPreferences
from app.contracts.preprocess import PerPageFixedBytes, PreprocessResult
from app.contracts.result import CompressionResult, ImageResult

__all__ = [
    # geometry
    "BBox",
    # elements (Phase 5)
    "RasterImage",
    "VectorPath",
    "TextBlock",
    "XObject",
    "Annotation",
    # pages (Phase 4/5/6)
    "RawPage",
    "PageElements",
    "ClassifiedPage",
    # fonts (Phase 5/7)
    "FontUsage",
    "SubsetResult",
    # preferences (user input)
    "CompressionTarget",
    "PageOverride",
    "UserPreferences",
    # plan (Phase 8)
    "RasterPlan",
    "VectorPlan",
    "PagePlan",
    "CompressionPlan",
    # result (Phase 9)
    "ImageResult",
    "CompressionResult",
    # preprocess (Phase 7)
    "PerPageFixedBytes",
    "PreprocessResult",
    # cross-cutting
    "PipelineContext",
    "ConvergenceStep",
    "PhaseErrorData",
    "PhaseError",
]
