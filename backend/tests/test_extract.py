"""Phase 5 元素提取测试。

样本策略（规则 6）：主体测试使用用户提供的真实作品集
（tests/fixtures/portfolios/*.pdf，混合类型）。链接/书签专项用 pymupdf
程序生成的真实带链接+TOC 的迷你 PDF（结构真实，非 mock）。
"""

from __future__ import annotations

import logging
import uuid
from pathlib import Path

import pymupdf
import pytest

from app.contracts import PipelineContext
from app.pipeline.extract import extract_elements
from app.pipeline.split import split_pdf

PORTFOLIOS = Path(__file__).parent / "fixtures" / "portfolios"
SAMPLES = sorted(PORTFOLIOS.glob("*.pdf"))


def _new_ctx(base: Path) -> PipelineContext:
    ws = base / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))


@pytest.fixture(scope="module")
def extracted(tmp_path_factory):
    """对每份真实样本跑一次 split → extract 全链路（module 级缓存，只跑一遍）。"""
    assert len(SAMPLES) >= 3, (
        f"需要至少 3 份真实作品集样本于 {PORTFOLIOS}（规则 6，不得用假 PDF）"
    )
    results = {}
    for src in SAMPLES:
        ctx = _new_ctx(tmp_path_factory.mktemp(src.stem))
        raw = split_pdf(str(src), ctx)
        pages, fonts, meta = extract_elements(raw, str(src), ctx)
        results[src.name] = {"ctx": ctx, "raw": raw, "pages": pages,
                             "fonts": fonts, "meta": meta}
    return results


# ---------------------------------------------------------------- 真实样本主体断言

def test_each_sample_extracts_some_content(extracted):
    """每份 PDF 至少提取出 raster / vector / text 之一（混合类型样本的宽松断言）。"""
    for name, r in extracted.items():
        total_raster = sum(len(p.raster_images) for p in r["pages"])
        total_vector = sum(len(p.vector_paths) for p in r["pages"])
        total_text = sum(len(p.text_blocks) for p in r["pages"])
        assert total_raster + total_vector + total_text > 0, f"{name}: 未提取到任何元素"


def test_fonts_used_at_least_one_with_chars(extracted):
    for name, r in extracted.items():
        assert len(r["fonts"]) >= 1, f"{name}: fonts_used 为空"
        # 至少一个字体累计到了字符集（作品集必有文字）
        assert any(f.chars_used for f in r["fonts"].values()), \
            f"{name}: 无任何字体累计到 chars_used"


def test_all_data_refs_exist_and_readable(extracted):
    for name, r in extracted.items():
        ctx = r["ctx"]
        for p in r["pages"]:
            for img in p.raster_images:
                f = ctx.resolve_ref(img.data_ref)
                assert f.is_file() and f.stat().st_size > 0, f"{name} p{p.page_number}: {img.data_ref}"
            for vec in p.vector_paths:
                f = ctx.resolve_ref(vec.path_data_ref)
                assert f.is_file() and f.stat().st_size > 0
        for font in r["fonts"].values():
            if font.data_ref is not None:
                f = ctx.resolve_ref(font.data_ref)
                assert f.is_file() and f.stat().st_size > 0, f"{name}: {font.data_ref}"


def test_original_bytes_producer_landed(extracted):
    """修订 2026-07-04 的 Producer 首次落地验证（关注点 1）：
    original_bytes == 实际写入文件的字节数，且 > 0。"""
    checked_raster = checked_vector = 0
    for name, r in extracted.items():
        ctx = r["ctx"]
        for p in r["pages"]:
            for img in p.raster_images:
                assert img.original_bytes == ctx.resolve_ref(img.data_ref).stat().st_size
                assert img.original_bytes > 0
                checked_raster += 1
            for vec in p.vector_paths:
                assert vec.original_bytes == ctx.resolve_ref(vec.path_data_ref).stat().st_size
                assert vec.original_bytes > 0
                checked_vector += 1
    assert checked_raster > 0, "三份样本竟无一张位图，请检查样本"
    # 矢量不强制存在（取决于样本内容），但存在即必须已验证


def test_raster_fields_sane(extracted):
    for name, r in extracted.items():
        for p in r["pages"]:
            for img in p.raster_images:
                assert img.dpi > 0
                assert img.width > 0 and img.height > 0
                assert img.bbox.w >= 0 and img.bbox.h >= 0
                assert isinstance(img.has_alpha, bool)


def test_page_alignment_with_rawpages(extracted):
    for name, r in extracted.items():
        assert len(r["pages"]) == len(r["raw"])
        assert [p.page_number for p in r["pages"]] == [rp.page_number for rp in r["raw"]]


def test_metadata_is_dict(extracted):
    for name, r in extracted.items():
        assert isinstance(r["meta"], dict)


# ---------------------------------------------------------------- 链接/书签专项（真实迷你 PDF）

def make_linked_pdf(path: Path) -> Path:
    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    doc.new_page(width=595, height=842)
    # 注意：new_page() 会使旧的 Page 对象失效，必须建完所有页后重新获取
    p1, p2 = doc[0], doc[1]
    p1.insert_text((72, 72), "Page 1 with link")
    p2.insert_text((72, 72), "Page 2 target")
    p1.insert_link({"kind": pymupdf.LINK_URI,
                    "from": pymupdf.Rect(72, 60, 220, 90),
                    "uri": "https://example.com/portfolio"})
    p1.insert_link({"kind": pymupdf.LINK_GOTO,
                    "from": pymupdf.Rect(72, 100, 220, 130),
                    "page": 1})
    doc.set_toc([[1, "Chapter 1", 1], [2, "Section 1.1", 2]])
    doc.save(str(path))
    doc.close()
    return path


def test_links_and_bookmarks_extracted(tmp_path):
    pdf = make_linked_pdf(tmp_path / "linked.pdf")
    ctx = _new_ctx(tmp_path)
    raw = split_pdf(str(pdf), ctx)
    pages, _fonts, _meta = extract_elements(raw, str(pdf), ctx)

    page1_annos = pages[0].annotations
    links = [a for a in page1_annos if a.anno_type == "link"]
    assert any(a.target == "https://example.com/portfolio" for a in links)
    assert any(a.target == "page:2" for a in links)  # GOTO 链接指向第 2 页
    for a in links:
        assert a.bbox.w > 0 and a.bbox.h > 0  # 链接热区有真实 bbox

    bookmarks = [a for p in pages for a in p.annotations if a.anno_type == "bookmark"]
    assert {b.target for b in bookmarks} == {"Chapter 1", "Section 1.1"}
    # 书签挂在正确的目标页上
    assert any(a.anno_type == "bookmark" and a.target == "Chapter 1"
               for a in pages[0].annotations)
    assert any(a.anno_type == "bookmark" and a.target == "Section 1.1"
               for a in pages[1].annotations)


# ---------------------------------------------------------------- alpha_type 三态（修订 2026-07-04）

def make_alpha_pdf(path: Path) -> Path:
    """嵌入三张图：真半透明 RGBA / 伪透明 RGBA（99.98% 像素 alpha=255）/ 无 alpha JPEG。
    全部是真实图像编码（PIL 生成），非 mock。"""
    import io

    from PIL import Image

    doc = pymupdf.open()
    doc.new_page(width=595, height=842)
    page = doc[0]

    translucent = Image.new("RGBA", (64, 64))
    for y in range(64):
        for x in range(64):
            translucent.putpixel((x, y), (255, 0, 0, int(255 * x / 63)))  # 渐变 alpha
    buf1 = io.BytesIO(); translucent.save(buf1, "PNG")
    page.insert_image(pymupdf.Rect(72, 72, 172, 172), stream=buf1.getvalue())

    pseudo = Image.new("RGBA", (64, 64), (0, 128, 255, 255))
    pseudo.putpixel((0, 0), (0, 128, 255, 254))  # 1/4096 非 255：保住 SMask 且 ≥99% 阈值内
    buf2 = io.BytesIO(); pseudo.save(buf2, "PNG")
    page.insert_image(pymupdf.Rect(200, 72, 300, 172), stream=buf2.getvalue())

    plain = Image.new("RGB", (64, 64), (10, 200, 30))
    buf3 = io.BytesIO(); plain.save(buf3, "JPEG")
    page.insert_image(pymupdf.Rect(330, 72, 430, 172), stream=buf3.getvalue())

    doc.save(str(path))
    doc.close()
    return path


def test_alpha_type_three_way_classification(tmp_path):
    pdf = make_alpha_pdf(tmp_path / "alpha.pdf")
    ctx = _new_ctx(tmp_path)
    raw = split_pdf(str(pdf), ctx)
    pages, _fonts, _meta = extract_elements(raw, str(pdf), ctx)

    types = sorted(i.alpha_type for i in pages[0].raster_images)
    assert types == ["none", "opaque", "translucent"], types
    for img in pages[0].raster_images:
        assert img.has_alpha == (img.alpha_type == "translucent")  # 兼容字段一致性


def test_alpha_type_valid_and_coherent_on_real_samples(extracted):
    """真实样本：alpha_type 全部合法、与 has_alpha 一致、三态均有出现。

    实测记录（2026-07-04）：按摆放计 translucent 占 ~97%（少数真透明贴图被平铺
    数千次）；按唯一 SMask 计 35% 为 opaque@99%。故不断言 opaque 占多数——
    该假设与生产数据不符，详见 Phase 5 验收报告。"""
    from collections import Counter

    counts = Counter()
    for r in extracted.values():
        for p in r["pages"]:
            for img in p.raster_images:
                assert img.alpha_type in ("none", "opaque", "translucent")
                assert img.has_alpha == (img.alpha_type == "translucent")
                counts[img.alpha_type] += 1
    assert counts["none"] > 0 and counts["opaque"] > 0 and counts["translucent"] > 0, counts


# ---------------------------------------------------------------- 单页失败容忍

def test_single_page_failure_yields_empty_elements(tmp_path, caplog, monkeypatch):
    pdf = make_linked_pdf(tmp_path / "linked.pdf")
    ctx = _new_ctx(tmp_path)
    raw = split_pdf(str(pdf), ctx)

    real_get_text = pymupdf.Page.get_text
    state = {"calls": 0}

    def flaky_get_text(self, *args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 1:  # 第 1 页的文字提取抛错 → 该页整体失败
            raise RuntimeError("injected text extraction failure")
        return real_get_text(self, *args, **kwargs)

    monkeypatch.setattr(pymupdf.Page, "get_text", flaky_get_text)

    with caplog.at_level(logging.WARNING, logger="app.pipeline.extract"):
        pages, _fonts, _meta = extract_elements(raw, str(pdf), ctx)

    assert len(pages) == 2  # 页数不减，失败页以空元素占位
    assert pages[0].raster_images == [] and pages[0].text_blocks == []
    assert pages[1].text_blocks  # 第 2 页正常
    warned = [r for r in caplog.records
              if r.name == "app.pipeline.extract" and "page 1" in r.getMessage()]
    assert warned
