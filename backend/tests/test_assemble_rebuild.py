"""重建路线（assemble_rebuild.py）保留覆盖——未来双轨选项 C 的可用性保险。

只保留 3 个核心用例（页数守恒 / 黑纱回归 / 有 ToUnicode 源的 CJK 往返），
不承担主干职责。主干测试见 test_assemble.py（原地手术）。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pymupdf

from app.config.settings import CompressionConfig
from app.contracts import PipelineContext, UserPreferences
from app.pipeline.assemble_rebuild import assemble_pdf as assemble_pdf_rebuild
from app.pipeline.classify import classify_pages
from app.pipeline.compress import execute_compression
from app.pipeline.decide import decide_compression
from app.pipeline.extract import extract_elements
from app.pipeline.preprocess import preprocess
from app.pipeline.split import split_pdf

from test_assemble import (
    _mean_luma,
    build_pdf_with_images,
)

CFG = CompressionConfig()


def run_rebuild_pipeline(src_pdf: Path, base: Path, target_mb: float = 10.0):
    ws = base / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    ctx = PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))
    prefs = UserPreferences(target_size_mb=target_mb, compression_target="screen")
    raw = split_pdf(str(src_pdf), ctx)
    pages, fonts, meta = extract_elements(raw, str(src_pdf), ctx)
    classified = classify_pages(pages)
    pre = preprocess(pages, fonts, meta, prefs, ctx)
    plan = decide_compression(classified, pages, prefs, pre, None, 1, CFG)
    result = execute_compression(plan, pages, ctx, CFG)
    return assemble_pdf_rebuild(pages, plan, result, pre, meta, ctx)


def test_rebuild_page_count_conservation(tmp_path):
    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=3)
    out_path = run_rebuild_pipeline(src, tmp_path)
    out = pymupdf.open(out_path)
    try:
        assert out.page_count == 3
    finally:
        out.close()


def test_rebuild_black_scrim_regression(tmp_path):
    import io

    from PIL import Image

    from test_assemble import _corner_luma, _noise_jpeg

    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc[0].insert_image(pymupdf.Rect(60, 80, 535, 440), stream=_noise_jpeg(5))
    scrim = Image.new("RGBA", (256, 256), (0, 0, 0, 50))
    buf = io.BytesIO(); scrim.save(buf, "PNG")
    doc[0].insert_image(pymupdf.Rect(0, 0, 595, 842), stream=buf.getvalue())
    src = tmp_path / "scrim.pdf"
    doc.save(str(src)); doc.close()

    out_path = run_rebuild_pipeline(src, tmp_path)
    out = pymupdf.open(out_path)
    try:
        assert _corner_luma(out[0]) > 150
        assert _mean_luma(out[0]) > 100
    finally:
        out.close()


def test_rebuild_cjk_roundtrip_with_tounicode_source(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc[0].insert_text((72, 100), "作品集测试", fontname="china-s", fontsize=14)
    src = tmp_path / "cjk.pdf"
    doc.save(str(src)); doc.close()

    out_path = run_rebuild_pipeline(src, tmp_path)
    out = pymupdf.open(out_path)
    try:
        assert "作品集" in out[0].get_text()
    finally:
        out.close()
