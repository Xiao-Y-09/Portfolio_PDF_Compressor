"""Phase 13 性能基准（Phase 15 部署后的对照基线）——可复用。

对每份真实样本按 10MB/screen 单轮走完整流水线，逐阶段计时：
split / extract / classify / preprocess / decide / compress / assemble。
（decide+compress 只计首轮——基线关注单阶段成本；多轮总耗时见
tier_acceptance 的 elapsed_s。）

输出：.pytest_tmp/perf_baseline.json + 控制台表格。
用法（backend/ 下）：.venv/Scripts/python.exe scripts/perf_baseline.py
注意：跑基线时避免并行其它重负载，数字才有对照意义。
"""

from __future__ import annotations

import json
import os
import sys
import time
import uuid
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

os.environ.setdefault(
    "STORAGE__TMP_DIR", str(PROJECT_ROOT / ".pytest_tmp" / "perf_baseline_storage")
)

from app.config.settings import get_settings  # noqa: E402

get_settings.cache_clear()
CFG = get_settings().compression

from app.contracts import PipelineContext, UserPreferences  # noqa: E402
from app.pipeline.assemble import assemble_pdf  # noqa: E402
from app.pipeline.classify import classify_pages  # noqa: E402
from app.pipeline.compress import execute_compression  # noqa: E402
from app.pipeline.decide import decide_compression  # noqa: E402
from app.pipeline.extract import extract_elements  # noqa: E402
from app.pipeline.orchestrator import cleanup_workspace  # noqa: E402
from app.pipeline.preprocess import preprocess  # noqa: E402
from app.pipeline.split import split_pdf  # noqa: E402

FIXTURES = BACKEND_ROOT / "tests" / "fixtures" / "portfolios"
WORKROOT = PROJECT_ROOT / ".pytest_tmp" / "perf_baseline_ws"
OUT_JSON = PROJECT_ROOT / ".pytest_tmp" / "perf_baseline.json"

STAGES = ("split", "extract", "classify", "preprocess", "decide", "compress", "assemble")


def profile_sample(sample: str) -> dict:
    src = FIXTURES / f"{sample}.pdf"
    ws = WORKROOT / sample
    ws.mkdir(parents=True, exist_ok=True)
    ctx = PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))
    prefs = UserPreferences(target_size_mb=10.0, compression_target="screen")
    timing: dict = {"sample": sample, "source_mb": round(src.stat().st_size / 1e6, 2)}

    try:
        t = time.perf_counter()
        raw = split_pdf(str(src), ctx)
        timing["split"] = round(time.perf_counter() - t, 2)

        t = time.perf_counter()
        pages, fonts, meta = extract_elements(raw, str(src), ctx)
        timing["extract"] = round(time.perf_counter() - t, 2)

        t = time.perf_counter()
        classified = classify_pages(pages)
        timing["classify"] = round(time.perf_counter() - t, 2)

        t = time.perf_counter()
        pre = preprocess(pages, fonts, meta, prefs, ctx)
        timing["preprocess"] = round(time.perf_counter() - t, 2)

        t = time.perf_counter()
        plan = decide_compression(classified, pages, prefs, pre, None, 1, CFG)
        timing["decide"] = round(time.perf_counter() - t, 2)

        t = time.perf_counter()
        result = execute_compression(plan, pages, ctx, CFG)
        timing["compress"] = round(time.perf_counter() - t, 2)

        t = time.perf_counter()
        assemble_pdf(pages, plan, result, pre, meta, ctx)
        timing["assemble"] = round(time.perf_counter() - t, 2)

        timing["total"] = round(sum(timing[s] for s in STAGES), 2)
        timing["pages"] = len(pages)
    finally:
        cleanup_workspace(str(ws))
    return timing


def main() -> None:
    results = []
    for pdf in sorted(FIXTURES.glob("portfolio_*.pdf")):
        print(f"profiling {pdf.stem} ...", flush=True)
        results.append(profile_sample(pdf.stem))

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(results, ensure_ascii=False, indent=2),
                        encoding="utf-8")

    header = f"{'sample':<14}{'MB':>7}{'pages':>6}" + "".join(f"{s:>11}" for s in STAGES) + f"{'total':>9}"
    print("\n" + header)
    for r in results:
        row = f"{r['sample']:<14}{r['source_mb']:>7.1f}{r['pages']:>6}"
        row += "".join(f"{r[s]:>11.2f}" for s in STAGES)
        row += f"{r['total']:>9.2f}"
        print(row)
    print(f"\nwritten: {OUT_JSON}")


if __name__ == "__main__":
    main()
