"""横切类型：运行时上下文 PipelineContext 与错误契约 PhaseErrorData / PhaseError。

data_ref 路径规范（《数据契约.md》§1.6，决策记录 2026-07-04）：
所有 *_ref 字段是**相对 tmp_workspace 的 POSIX 风格相对路径**（分隔符 `/`）。
resolve_ref() 是唯一合法解析入口——任何 Phase 禁止手工拼接 ref 路径。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import List

from pydantic import BaseModel, Field


class ConvergenceStep(BaseModel):
    """收敛轨迹单步记录（可观测性，修订 2026-07-04）。

    Producer：Phase 9 每完成一轮压缩后追加。
    Consumer：Phase 11 进度日志/错误诊断、Phase 13 收敛性能统计、
    Phase 14 前端可选"压缩过程图表"。
    ⚠ 不参与 decide 决策逻辑（decide 只看 previous_result 一个）。
    """

    round_number: int = Field(ge=1)
    base_aggressiveness: float = Field(ge=0.0, le=1.0)
    total_raster_bytes: int = Field(ge=0)
    total_bytes: int = Field(ge=0)  # = total_raster_bytes + fixed_bytes
    target_bytes: int = Field(gt=0)
    gap_ratio: float  # = (total_bytes - target_bytes) / target_bytes，可为负


class PipelineContext(BaseModel):
    """所有 Phase 共享的运行时上下文。"""

    file_id: str
    tmp_workspace: str
    convergence_history: List[ConvergenceStep] = Field(default_factory=list)

    def resolve_ref(self, ref: str) -> Path:
        """把 data_ref 类引用解析为 tmp_workspace 下的真实路径。

        安全校验（防路径穿越）：拒绝空串、`\\` 分隔符、绝对路径、盘符、
        `..` 分段；解析结果必须仍位于 tmp_workspace 内。
        只做路径数学，不做文件 I/O（存在性由调用方检查）。
        """
        if not ref or "\\" in ref:
            raise ValueError(f"illegal ref: {ref!r}")
        pure = PurePosixPath(ref)
        if pure.is_absolute() or ":" in ref or ".." in pure.parts:
            raise ValueError(f"illegal ref: {ref!r}")
        workspace = Path(self.tmp_workspace).resolve()
        resolved = (workspace / pure).resolve()
        if not resolved.is_relative_to(workspace):
            raise ValueError(f"ref escapes workspace: {ref!r}")
        return resolved


class PhaseErrorData(BaseModel):
    """错误的数据契约（可 JSON round-trip，进 Celery meta / API 响应体）。"""

    phase: str    # "split" / "extract" / "decide" / "orchestrator" / "assemble" ...
    code: str     # "INVALID_PDF" / "ENCRYPTED_PDF" / "TARGET_TOO_SMALL" / ...
    message: str
    recoverable: bool = False


class PhaseError(Exception):
    """异常载体：raise PhaseError(phase=..., code=..., message=..., recoverable=...)。

    Pydantic BaseModel 不能同时继承 Exception，故拆为双层：
    数据进 PhaseErrorData（本异常的 .data），.model_dump() 委托给它，
    使 `except PhaseError as e: meta={"error": e.model_dump()}`（手册 Phase 11 原文）成立。
    """

    def __init__(self, phase: str, code: str, message: str, recoverable: bool = False):
        self.data = PhaseErrorData(
            phase=phase, code=code, message=message, recoverable=recoverable
        )
        super().__init__(f"[{phase}:{code}] {message}")

    @property
    def phase(self) -> str:
        return self.data.phase

    @property
    def code(self) -> str:
        return self.data.code

    @property
    def recoverable(self) -> bool:
        return self.data.recoverable

    def model_dump(self) -> dict:
        return self.data.model_dump()
