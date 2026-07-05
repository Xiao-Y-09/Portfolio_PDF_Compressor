"""Tier 系统真实样本验收脚本（2026-07-04 步骤 5 首建；未来回归可复用）。

场景矩阵：样本 × 目标大小，**不强制指定 Tier**——走 orchestrator 完整降级
状态机（run_until_classify → resume_after_review，跳过人工 review）。

每场景报告：最终大小/±15% 达标判定/tier_used/各 Tier 轮数/各 Tier 边界真实
大小/失败时完整 ConvergenceDiagnostics 与错误文案/耗时。
产出 PDF：preview_output/tier_final_{sample}_{target}mb.pdf
JSON 摘要：.pytest_tmp/tier_acceptance_results.json（增量写入，中断可恢复已完成部分）

用法（backend/ 目录下）：
  .venv/Scripts/python.exe scripts/tier_acceptance.py                     # 全矩阵 3×2
  .venv/Scripts/python.exe scripts/tier_acceptance.py --samples portfolio_3 --targets 5
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import re
import sys
import time
import uuid
from itertools import groupby
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

# storage 隔离必须先于任何 get_settings() 调用
os.environ.setdefault(
    "STORAGE__TMP_DIR", str(PROJECT_ROOT / ".pytest_tmp" / "tier_acceptance_storage")
)

from app.config.settings import get_settings  # noqa: E402

get_settings.cache_clear()

from app.contracts import PhaseError, PipelineContext, UserPreferences  # noqa: E402
from app.pipeline.orchestrator import (  # noqa: E402
    cleanup_workspace,
    resume_after_review,
    run_until_classify,
)
from app.storage import get_storage  # noqa: E402

FIXTURES = BACKEND_ROOT / "tests" / "fixtures" / "portfolios"
OUT_DIR = PROJECT_ROOT / "preview_output"
WORKROOT = PROJECT_ROOT / ".pytest_tmp" / "tier_acceptance_ws"
RESULTS_JSON = PROJECT_ROOT / ".pytest_tmp" / "tier_acceptance_results.json"

# 目标大小 → 压缩场景预设（《压缩决策引擎.md》§1.3 用户体验层映射）
PRESET_FOR_TARGET = {5: "email", 10: "screen", 15: "screen", 20: "print"}

_BOUNDARY_RE = re.compile(
    r"tier (\w+) boundary: actual=([\d.]+)MB .* gap=([-\d.]+)%")


class _BoundaryCapture(logging.Handler):
    """截获 orchestrator 的 Tier 边界 INFO 日志（每 Tier 的真实装配大小）。"""

    def __init__(self):
        super().__init__(level=logging.INFO)
        self.boundaries = []

    def emit(self, record):
        m = _BOUNDARY_RE.search(record.getMessage())
        if m:
            self.boundaries.append({
                "tier": m.group(1),
                "actual_mb": float(m.group(2)),
                "gap_pct": float(m.group(3)),
            })


def run_scenario(sample: str, target_mb: float) -> dict:
    src = FIXTURES / f"{sample}.pdf"
    entry = {
        "sample": sample,
        "target_mb": target_mb,
        "compression_target": PRESET_FOR_TARGET.get(int(target_mb), "screen"),
        "source_mb": round(src.stat().st_size / 1e6, 2),
    }
    storage = get_storage()
    file_id = storage.save_upload(src.read_bytes(), "pdf")
    ws = WORKROOT / f"{sample}_{int(target_mb)}mb"
    ws.mkdir(parents=True, exist_ok=True)
    ctx = PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))
    user_prefs = UserPreferences(
        target_size_mb=target_mb,
        compression_target=entry["compression_target"],
    )

    capture = _BoundaryCapture()
    logging.getLogger("app.pipeline.orchestrator").addHandler(capture)
    t0 = time.time()
    try:
        classified, session_data = run_until_classify(file_id, user_prefs, ctx)
        out_path = resume_after_review(session_data, classified, user_prefs)

        actual = Path(out_path).stat().st_size
        target_bytes = target_mb * 1024 * 1024
        history = ctx.convergence_history
        dest = OUT_DIR / f"tier_final_{sample}_{int(target_mb)}mb.pdf"
        dest.write_bytes(Path(out_path).read_bytes())

        entry.update({
            "status": "SUCCESS",
            "tier_used": history[-1].tier if history else "in_place",
            "final_size_mb": round(actual / 1e6, 3),
            "within_15pct": abs(actual - target_bytes) / target_bytes <= 0.15,
            "gap_pct": round((actual - target_bytes) / target_bytes * 100, 1),
            "rounds_per_tier": {
                tier: len(list(steps))
                for tier, steps in groupby(history, key=lambda s: s.tier)
            },
            "tier_boundaries": capture.boundaries,
            "output": str(dest),
        })
    except PhaseError as exc:
        entry.update({
            "status": "PHASE_ERROR",
            "code": exc.code,
            "phase": exc.phase,
            "message": exc.data.message,
            "diagnostics": (exc.data.diagnostics.model_dump()
                            if exc.data.diagnostics else None),
            "rounds_per_tier": {
                tier: len(list(steps))
                for tier, steps in groupby(ctx.convergence_history,
                                           key=lambda s: s.tier)
            },
            "tier_boundaries": capture.boundaries,
        })
    finally:
        logging.getLogger("app.pipeline.orchestrator").removeHandler(capture)
        entry["elapsed_s"] = round(time.time() - t0, 1)
        cleanup_workspace(str(ws))          # 铁律 5：场景结束即清理工作区
        storage.delete(file_id)             # 上传副本同样清理
    return entry


def main() -> None:
    parser = argparse.ArgumentParser(description="Tier 系统真实样本验收")
    parser.add_argument("--samples", default="portfolio_1,portfolio_2,portfolio_3")
    parser.add_argument("--targets", default="5,10")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO,
                        format="%(asctime)s %(name)s %(message)s")
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    RESULTS_JSON.parent.mkdir(parents=True, exist_ok=True)

    results = []
    if RESULTS_JSON.is_file():  # 增量恢复：跳过已完成场景
        results = json.loads(RESULTS_JSON.read_text(encoding="utf-8"))
    done = {(r["sample"], r["target_mb"]) for r in results}

    for sample in args.samples.split(","):
        for target in (float(t) for t in args.targets.split(",")):
            if (sample, target) in done:
                print(f"[skip] {sample} @ {target}MB already done")
                continue
            print(f"\n===== {sample} @ {target}MB =====", flush=True)
            entry = run_scenario(sample.strip(), target)
            results.append(entry)
            RESULTS_JSON.write_text(
                json.dumps(results, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps(entry, ensure_ascii=False, indent=2), flush=True)

    print("\n===== SUMMARY =====")
    for r in results:
        if r["status"] == "SUCCESS":
            mark = "PASS" if r["within_15pct"] else "OVER"
            print(f"{r['sample']} @ {r['target_mb']}MB: {r['final_size_mb']}MB "
                  f"[{mark}] tier={r['tier_used']} rounds={r['rounds_per_tier']} "
                  f"{r['elapsed_s']}s")
        else:
            print(f"{r['sample']} @ {r['target_mb']}MB: {r['code']} "
                  f"rounds={r.get('rounds_per_tier')} {r['elapsed_s']}s")
    print(f"\nresults json: {RESULTS_JSON}")


if __name__ == "__main__":
    main()
