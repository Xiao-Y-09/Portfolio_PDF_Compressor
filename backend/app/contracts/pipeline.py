"""横切类型：运行时上下文 PipelineContext 与错误契约 PhaseErrorData / PhaseError。

data_ref 路径规范（《数据契约.md》§1.6，决策记录 2026-07-04）：
所有 *_ref 字段是**相对 tmp_workspace 的 POSIX 风格相对路径**（分隔符 `/`）。
resolve_ref() 是唯一合法解析入口——任何 Phase 禁止手工拼接 ref 路径。
"""

from __future__ import annotations

from pathlib import Path, PurePosixPath
from typing import List, Optional

from pydantic import BaseModel, Field

from app.contracts.plan import CompressionTier


class ConvergenceStep(BaseModel):
    """收敛轨迹单步记录（可观测性，修订 2026-07-04）。

    Producer：orchestrator 每完成一轮 decide+compress 后追加（Phase 11 落地时
    确认：gap 计算需要 target/fixed_bytes，orchestrator 是唯一同时掌握全部输入
    的位置）。
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
    # 修订 2026-07-04（Tier 系统）：本步所属压缩层级，轨迹可区分降级前后
    # （Phase 13 统计 / Phase 14 图表）
    tier: CompressionTier = "in_place"


class ConvergenceDiagnostics(BaseModel):
    """最终失败的可行动诊断（修订 2026-07-04，Tier 系统用户补强）。

    Producer = orchestrator（Tier 3 失败时构造）；Consumer =
    PhaseErrorData.diagnostics → Phase 12 API 错误响应体透传 → Phase 14 前端
    渲染可行动建议（look-ahead 预纳入）。文案模板见《压缩决策引擎.md》§8.4。
    """

    tier_reached: CompressionTier  # 实际到达的最深层级（失败时 = "full_raster"）
    best_achievable_bytes: int = Field(ge=0)  # 末 Tier 末轮 assemble 真实文件大小（非估算）
    skip_page_count: int = Field(ge=0)        # 用户 skip 的页数
    skip_raster_bytes: int = Field(ge=0)      # skip 页位图原样保留成本（唯一流口径）
    skip_budget_ratio: float = Field(ge=0.0)  # = skip_raster_bytes / target_bytes，可 >1


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
    code: str     # "INVALID_PDF" / "ENCRYPTED_PDF" / "TARGET_TOO_SMALL" /
                  # "CONVERGENCE_FAILED" / "ASSEMBLY_FAILED" / "SESSION_EXPIRED" ...
    message: str
    recoverable: bool = False
    # 修订 2026-07-04（Tier 系统）：仅 CONVERGENCE_FAILED 置位，其余错误码为 None
    diagnostics: Optional[ConvergenceDiagnostics] = None


class PhaseError(Exception):
    """异常载体：raise PhaseError(phase=..., code=..., message=..., recoverable=...)。

    Pydantic BaseModel 不能同时继承 Exception，故拆为双层：
    数据进 PhaseErrorData（本异常的 .data），.model_dump() 委托给它，
    使 `except PhaseError as e: meta={"error": e.model_dump()}`（手册 Phase 11 原文）成立。
    """

    def __init__(
        self,
        phase: str,
        code: str,
        message: str,
        recoverable: bool = False,
        diagnostics=None,  # ConvergenceDiagnostics | dict | None（dict 供 Celery 重建）
    ):
        self.data = PhaseErrorData(
            phase=phase, code=code, message=message, recoverable=recoverable,
            diagnostics=diagnostics,
        )
        super().__init__(f"[{phase}:{code}] {message}")
        # Celery JSON 结果后端重建约定（Phase 12 修订，2026-07-05）：FAILURE 结果
        # 以 exc_type + exc_message(=args) 存储，读回时 cls(*args) 重建。args 必须
        # 与构造签名对齐（diagnostics 转 dict 保 JSON 可序列化，构造时 pydantic
        # 自动校验回模型）。__str__ 保持人类可读格式不受 args 变化影响。
        self.args = (
            phase, code, message, recoverable,
            self.data.diagnostics.model_dump() if self.data.diagnostics else None,
        )

    def __str__(self) -> str:
        return f"[{self.data.phase}:{self.data.code}] {self.data.message}"

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
