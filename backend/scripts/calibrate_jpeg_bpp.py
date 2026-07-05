"""§5.5 JPEG 估算系数标定（2026-07-05 用户批准，R7 处理提前）——可复用。

方法：真实样本受控采集——
- 整页上下文：每样本均匀抽 N 页，按 (dpi × quality) 网格渲染+JPEG 编码，
  记录 actual_bytes / pixel_count = bpp_actual；
- 元素级上下文：从样本抽唯一位图，按 quality 网格编码（compress.py 同参数），
  记录 bpp_actual。
两个上下文分开拟合 bpp = base + (q/100) × coeff（最小二乘，公式结构不变），
并与现行系数（0.04 + q/100 × 0.12）逐点对比偏差。

输出：.pytest_tmp/jpeg_bpp_calibration.json + 控制台报告。
用法（backend/ 下）：.venv/Scripts/python.exe scripts/calibrate_jpeg_bpp.py
"""

from __future__ import annotations

import io
import json
import sys
from pathlib import Path

BACKEND_ROOT = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_ROOT.parent
sys.path.insert(0, str(BACKEND_ROOT))
if hasattr(sys.stdout, "reconfigure"):
    sys.stdout.reconfigure(encoding="utf-8")

import pymupdf  # noqa: E402
from PIL import Image  # noqa: E402

FIXTURES = BACKEND_ROOT / "tests" / "fixtures" / "portfolios"
OUT_JSON = PROJECT_ROOT / ".pytest_tmp" / "jpeg_bpp_calibration.json"

DPI_GRID = (72, 96, 126, 150)
Q_GRID = (30, 50, 76, 95)
PAGES_PER_SAMPLE = 6          # 均匀抽页
ELEMENT_SAMPLE_LIMIT = 60     # 元素级抽样上限（p3）
ELEMENT_MIN_EDGE = 200        # 太小的图不采（噪声大）

# 现行系数（config 默认，对照基线）
CUR_BASE, CUR_COEFF = 0.04, 0.12


def _fit_linear(points):
    """最小二乘拟合 bpp = base + coeff × (q/100)。points: [(q, bpp)]"""
    n = len(points)
    xs = [q / 100.0 for q, _ in points]
    ys = [b for _, b in points]
    mean_x, mean_y = sum(xs) / n, sum(ys) / n
    sxx = sum((x - mean_x) ** 2 for x in xs)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    coeff = sxy / sxx if sxx else 0.0
    base = mean_y - coeff * mean_x
    return base, coeff


def _deviation_stats(points, base, coeff):
    devs = [(base + coeff * q / 100.0 - bpp) / bpp * 100 for q, bpp in points]
    devs.sort()
    return {
        "n": len(devs),
        "median_pct": round(devs[len(devs) // 2], 1),
        "p10_pct": round(devs[int(len(devs) * 0.1)], 1),
        "p90_pct": round(devs[int(len(devs) * 0.9)], 1),
    }


def collect_whole_page(sample: str):
    """整页网格渲染：真实页 × DPI_GRID × Q_GRID → (q, bpp) 点集。"""
    doc = pymupdf.open(str(FIXTURES / f"{sample}.pdf"))
    points = []
    try:
        step = max(1, doc.page_count // PAGES_PER_SAMPLE)
        for page_index in range(0, doc.page_count, step):
            page = doc[page_index]
            for dpi in DPI_GRID:
                zoom = dpi / 72.0
                pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
                im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
                pixels = pix.width * pix.height
                for q in Q_GRID:
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=q, optimize=True)
                    points.append((q, buf.tell() / pixels))
    finally:
        doc.close()
    return points


def collect_elements(sample: str):
    """元素级：唯一位图 × Q_GRID（compress.py 同参数编码）→ (q, bpp) 点集。"""
    doc = pymupdf.open(str(FIXTURES / f"{sample}.pdf"))
    points = []
    seen = set()
    try:
        for page_index in range(doc.page_count):
            if len(seen) >= ELEMENT_SAMPLE_LIMIT:
                break
            for item in doc[page_index].get_images(full=True):
                xref = item[0]
                if xref in seen or len(seen) >= ELEMENT_SAMPLE_LIMIT:
                    continue
                seen.add(xref)
                try:
                    info = doc.extract_image(xref)
                    im = Image.open(io.BytesIO(info["image"]))
                    im.load()
                except Exception:
                    continue
                if min(im.width, im.height) < ELEMENT_MIN_EDGE:
                    continue
                if im.mode not in ("RGB", "L"):
                    im = im.convert("RGB")
                pixels = im.width * im.height
                for q in Q_GRID:
                    buf = io.BytesIO()
                    im.save(buf, "JPEG", quality=q, optimize=True)
                    points.append((q, buf.tell() / pixels))
    finally:
        doc.close()
    return points


def main() -> None:
    report = {"current": {"base": CUR_BASE, "coeff": CUR_COEFF}, "contexts": {}}

    whole_points = []
    for sample in ("portfolio_1", "portfolio_2", "portfolio_3"):
        pts = collect_whole_page(sample)
        whole_points.extend(pts)
        print(f"[whole-page] {sample}: {len(pts)} measurements")
    base_w, coeff_w = _fit_linear(whole_points)
    report["contexts"]["whole_page_jpeg"] = {
        "n": len(whole_points),
        "fitted_base": round(base_w, 4),
        "fitted_coeff": round(coeff_w, 4),
        "current_vs_actual": _deviation_stats(whole_points, CUR_BASE, CUR_COEFF),
        "fitted_vs_actual": _deviation_stats(whole_points, base_w, coeff_w),
        "mean_bpp_by_q": {
            q: round(sum(b for qq, b in whole_points if qq == q)
                     / max(1, sum(1 for qq, _ in whole_points if qq == q)), 4)
            for q in Q_GRID
        },
    }

    elem_points = []
    for sample in ("portfolio_1", "portfolio_3"):
        pts = collect_elements(sample)
        elem_points.extend(pts)
        print(f"[element] {sample}: {len(pts)} measurements")
    base_e, coeff_e = _fit_linear(elem_points)
    report["contexts"]["element_jpeg"] = {
        "n": len(elem_points),
        "fitted_base": round(base_e, 4),
        "fitted_coeff": round(coeff_e, 4),
        "current_vs_actual": _deviation_stats(elem_points, CUR_BASE, CUR_COEFF),
        "fitted_vs_actual": _deviation_stats(elem_points, base_e, coeff_e),
        "mean_bpp_by_q": {
            q: round(sum(b for qq, b in elem_points if qq == q)
                     / max(1, sum(1 for qq, _ in elem_points if qq == q)), 4)
            for q in Q_GRID
        },
    }

    OUT_JSON.parent.mkdir(parents=True, exist_ok=True)
    OUT_JSON.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                        encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    print(f"\nwritten: {OUT_JSON}")


if __name__ == "__main__":
    main()
