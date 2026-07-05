"""Phase 10 原地手术主干测试（架构决策 A，2026-07-04）。

原地语义的核心不变量：文字/矢量/书签/链接逐字节保留；位图经 sidecar xref
定位替换；页数守恒；黑纱/白底等目视回归继续锁死。
"""

from __future__ import annotations

import io
import random
import uuid
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from app.config.settings import CompressionConfig
from app.contracts import PipelineContext, UserPreferences
from app.pipeline.assemble import assemble_pdf
from app.pipeline.classify import classify_pages
from app.pipeline.compress import execute_compression
from app.pipeline.decide import decide_compression
from app.pipeline.extract import extract_elements
from app.pipeline.preprocess import preprocess
from app.pipeline.split import split_pdf

from test_extract import make_linked_pdf

CFG = CompressionConfig()


def run_full_pipeline(src_pdf: Path, base: Path, target_mb: float = 10.0):
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
    out_path = assemble_pdf(pages, plan, result, pre, meta, ctx)
    return ctx, pages, plan, result, out_path


def _noise_jpeg(seed: int, size=(800, 600)) -> bytes:
    rnd = random.Random(seed)
    im = Image.new("RGB", size)
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                for _ in range(size[0] * size[1])])
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=90)
    return buf.getvalue()


def build_pdf_with_images(path: Path, n_pages: int = 3) -> Path:
    doc = pymupdf.open()
    for i in range(n_pages):
        doc.new_page(width=595, height=842)
    for i in range(n_pages):
        page = doc[i]
        page.insert_image(pymupdf.Rect(50, 80, 545, 450), stream=_noise_jpeg(i))
        page.insert_text((50, 500), f"Portfolio page {i + 1}", fontsize=14)
    doc.save(str(path))
    doc.close()
    return path


def build_pdf_with_tiled_image(path: Path, placements: int = 3) -> Path:
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    page = doc[0]
    data = _noise_jpeg(99, size=(600, 400))
    for k in range(placements):
        y = 60 + k * 250
        page.insert_image(pymupdf.Rect(60, y, 360, y + 200), stream=data)
    doc.save(str(path))
    doc.close()
    return path


def _mean_luma(page) -> float:
    pix = page.get_pixmap(matrix=pymupdf.Matrix(0.4, 0.4), colorspace=pymupdf.csGRAY)
    s = pix.samples
    return sum(s) / len(s) if s else 0.0


def _corner_luma(page) -> float:
    pix = page.get_pixmap(clip=pymupdf.Rect(2, 2, 12, 12), colorspace=pymupdf.csGRAY)
    s = pix.samples
    return sum(s) / len(s) if s else 0.0


# ---------------------------------------------------------------- 铁律级不变量

def test_page_count_conservation(tmp_path):
    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=3)
    *_rest, out_path = run_full_pipeline(src, tmp_path)
    out = pymupdf.open(out_path)
    try:
        assert out.is_pdf and out.page_count == 3
        for i in range(3):
            assert out[i].get_images()
    finally:
        out.close()


def test_output_dimensions_match_source(tmp_path):
    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=2)
    *_rest, out_path = run_full_pipeline(src, tmp_path)
    out = pymupdf.open(out_path)
    try:
        for i in range(2):
            assert out[i].rect.width == pytest.approx(595, abs=1)
            assert out[i].rect.height == pytest.approx(842, abs=1)
    finally:
        out.close()


# ---------------------------------------------------------------- 原地手术核心语义

def test_images_actually_replaced_in_place(tmp_path):
    """位图被真实替换（xref 定位），输出小于源；文字层逐字节不动。"""
    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=2)
    src_doc = pymupdf.open(str(src))
    src_text = [p.get_text() for p in src_doc]
    src_img_bytes = src_doc.extract_image(src_doc[0].get_images()[0][0])["image"]
    src_doc.close()

    *_rest, out_path = run_full_pipeline(src, tmp_path, target_mb=1.0)

    assert Path(out_path).stat().st_size < src.stat().st_size
    out = pymupdf.open(out_path)
    try:
        out_img_bytes = out.extract_image(out[0].get_images()[0][0])["image"]
        assert out_img_bytes != src_img_bytes          # 图像流确实换掉了
        assert len(out_img_bytes) < len(src_img_bytes)  # 且更小
        for i, text in enumerate(src_text):
            assert out[i].get_text() == text            # 文字一个字节不动
    finally:
        out.close()


def test_shared_xref_single_stream_multi_placement(tmp_path):
    """§3.1.1：3 处摆放共享 1 个 xref → 替换一次，输出仍 1 流 3 摆放。"""
    src = build_pdf_with_tiled_image(tmp_path / "tiled.pdf", placements=3)
    _ctx, pages, _plan, result, out_path = run_full_pipeline(src, tmp_path)

    assert len(pages[0].raster_images) == 3
    assert len({(r.page_number, r.compressed_data_ref)
                for r in result.per_image_results}) == 1
    out = pymupdf.open(out_path)
    try:
        xrefs = {img[0] for img in out[0].get_images(full=True)}
        assert len(xrefs) == 1
        assert len(out[0].get_image_rects(next(iter(xrefs)))) == 3
    finally:
        out.close()


def test_translucent_replacement_keeps_alpha(tmp_path):
    """translucent 图经 sidecar xref 替换（不再依赖内容摘要），输出保留 SMask。"""
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    rgba = Image.new("RGBA", (400, 300))
    rgba.putdata([(200, 30, 30, (i % 400) * 255 // 399) for i in range(400 * 300)])
    buf = io.BytesIO(); rgba.save(buf, "PNG")
    doc[0].insert_image(pymupdf.Rect(60, 60, 460, 360), stream=buf.getvalue())
    src = tmp_path / "rgba.pdf"
    doc.save(str(src)); doc.close()

    *_rest, out_path = run_full_pipeline(src, tmp_path)
    out = pymupdf.open(out_path)
    try:
        entry = out[0].get_images(full=True)[0]
        assert entry[1] != 0  # SMask 仍在（透明保留）
    finally:
        out.close()


def test_opaque_alpha_flattened_to_jpeg_visually_equal(tmp_path):
    """优化 1 验证（机制自 Phase 8 §5.4 已生效）：opaque（伪透明）图输出为 JPEG，
    白底 compositing 后与源视觉无差别。"""
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    rnd = random.Random(11)
    rgba = Image.new("RGBA", (400, 300))
    rgba.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256), 255)
                  for _ in range(400 * 300)])
    rgba.putpixel((0, 0), (10, 10, 10, 254))  # 1/120000 非 255 → 保住 SMask，仍 ≥99% opaque
    buf = io.BytesIO(); rgba.save(buf, "PNG")
    doc[0].insert_image(pymupdf.Rect(60, 60, 460, 360), stream=buf.getvalue())
    src = tmp_path / "opaque.pdf"
    doc.save(str(src)); doc.close()

    _ctx, pages, plan, _result, out_path = run_full_pipeline(src, tmp_path)

    assert pages[0].raster_images[0].alpha_type == "opaque"
    assert plan.pages[0].raster_plans[0].output_format == "jpeg"  # 决策层
    out = pymupdf.open(out_path)
    src_doc = pymupdf.open(str(src))
    try:
        info = out.extract_image(out[0].get_images(full=True)[0][0])
        assert info["ext"] in ("jpeg", "jpg")  # 执行+组装层：确实是 JPEG 流
        # 视觉无差别：渲染像素差应在 JPEG 重压缩噪声量级内
        m = pymupdf.Matrix(0.5, 0.5)
        a = src_doc[0].get_pixmap(matrix=m, colorspace=pymupdf.csGRAY).samples
        b = out[0].get_pixmap(matrix=m, colorspace=pymupdf.csGRAY).samples
        mean_diff = sum(abs(a[i] - b[i]) for i in range(min(len(a), len(b)))) / min(len(a), len(b))
        assert mean_diff < 8.0, f"视觉差异过大: {mean_diff}"
    finally:
        out.close(); src_doc.close()


def test_vectors_untouched(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    for k in range(20):
        doc[0].draw_line((50 + k * 20, 100), (60 + k * 20, 700), color=(0, 0, 0.8))
    doc[0].insert_image(pymupdf.Rect(60, 60, 260, 210), stream=_noise_jpeg(3))
    src = tmp_path / "vec.pdf"
    doc.save(str(src)); doc.close()

    src_doc = pymupdf.open(str(src))
    n_src_drawings = len(src_doc[0].get_drawings())
    src_doc.close()

    *_rest, out_path = run_full_pipeline(src, tmp_path)
    out = pymupdf.open(out_path)
    try:
        assert len(out[0].get_drawings()) == n_src_drawings  # 矢量原封不动
    finally:
        out.close()


# ---------------------------------------------------------------- 链接/书签/元数据

def test_links_bookmarks_metadata_preserved(tmp_path):
    src = make_linked_pdf(tmp_path / "linked.pdf")
    *_rest, out_path = run_full_pipeline(src, tmp_path)
    out = pymupdf.open(out_path)
    try:
        assert out.page_count == 2
        links = out[0].get_links()
        assert "https://example.com/portfolio" in {
            l.get("uri") for l in links if l.get("uri")}
        assert [l for l in links if l.get("page") == 1]
        toc = out.get_toc()
        assert {e[1] for e in toc} == {"Chapter 1", "Section 1.1"}
        assert [e[0] for e in sorted(toc, key=lambda e: e[2])] == [1, 2]  # 层级原生保留
        meta = out.metadata or {}
        assert not meta.get("author") and not meta.get("creator")
    finally:
        out.close()


# ---------------------------------------------------------------- 目视回归（继续锁死）

def test_black_scrim_regression(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    page = doc[0]
    page.insert_image(pymupdf.Rect(60, 80, 535, 440), stream=_noise_jpeg(5))
    scrim = Image.new("RGBA", (256, 256), (0, 0, 0, 50))
    buf = io.BytesIO(); scrim.save(buf, "PNG")
    page.insert_image(pymupdf.Rect(0, 0, 595, 842), stream=buf.getvalue())
    src = tmp_path / "scrim.pdf"
    doc.save(str(src)); doc.close()

    *_rest, out_path = run_full_pipeline(src, tmp_path)
    out = pymupdf.open(out_path)
    try:
        assert _corner_luma(out[0]) > 150
        assert _mean_luma(out[0]) > 100
    finally:
        out.close()


def test_white_background_preserved(tmp_path):
    src = build_pdf_with_images(tmp_path / "src.pdf", n_pages=1)
    *_rest, out_path = run_full_pipeline(src, tmp_path)
    out = pymupdf.open(out_path)
    try:
        assert _corner_luma(out[0]) > 240
    finally:
        out.close()


def test_cjk_text_roundtrip(tmp_path):
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc[0].insert_text((72, 100), "作品集：建筑设计与空间研究",
                       fontname="china-s", fontsize=14)
    doc[0].insert_text((72, 130), "SCAFFOLD LIVING", fontname="helv", fontsize=14)
    src = tmp_path / "cjk.pdf"
    doc.save(str(src)); doc.close()

    *_rest, out_path = run_full_pipeline(src, tmp_path)
    out = pymupdf.open(out_path)
    try:
        text = out[0].get_text()
        assert "作品集" in text and "建筑设计" in text
        assert "SCAFFOLD LIVING" in text
    finally:
        out.close()


def test_extraction_failure_page_stays_intact(tmp_path, monkeypatch):
    """提取失败页（空 PageElements）：原地主干下页面天然完好（源即基底）。"""
    src = make_linked_pdf(tmp_path / "linked.pdf")

    real_get_text = pymupdf.Page.get_text
    state = {"calls": 0}

    def flaky_get_text(self, *args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:
            raise RuntimeError("injected extraction failure")
        return real_get_text(self, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_text", flaky_get_text)
    *_rest, out_path = run_full_pipeline(src, tmp_path)
    monkeypatch.undo()

    out = pymupdf.open(out_path)
    try:
        assert "Page 1" in out[0].get_text()
        assert _mean_luma(out[0]) > 100
    finally:
        out.close()
