"""Phase 10 重建路线（已退役主干，保留备选）。

2026-07-04 架构决策 A：主干切换为原地手术（assemble.py）。保留原因：未来
选项 C（双轨：默认原地保真 / 用户显式选"极限压缩"档走重建）可能启用。
已知结构性限制：无 ToUnicode 的字体（设计软件导出的中文字体常见）文字层
码点不可恢复，重建路线无法正确还原此类文字——这是主干切换的直接原因。

目视验收修复（2026-07-04，五项问题）：
① 白底变黑 / ② 整页全黑 / ⑤ 矢量被盖：根因在 extract（SMask 未合并，黑基底
   实心化），本模块另加**白色垫底矩形**双保险（用户指定）；
③ 中文乱码：字体注册成功 ≠ 字形可用（无 cmap 字体画方块且不抛异常）——
   注册前用 pymupdf.Font 做 **glyph 覆盖检查**，不覆盖则按内容回退
   china-s（内置 CJK）/ helv；
④ 空白页：**逐页插入计数日志**；本页零成功插入时回退——用 show_pdf_page
   把 split 的原始单页盖回（未压缩但绝不空白，log 记 fallback）。

共享 data_ref 配合（§3.1.1）：同一 compressed_data_ref 只嵌一次字节流，
后续摆放（含跨页）以 xref 复用。

V1 保真近似（其余维持）：层序固定 位图→矢量→文字；文字颜色统一黑、基线近似；
书签压平一级；form_field/comment 不重建。

保存参数（手册指定）：garbage=4, clean=True, deflate=True, deflate_images=False。
"""

from __future__ import annotations

import logging
import pickle
import re
from typing import Any, Dict, List, Optional, Tuple

import pymupdf

from app.contracts import (
    CompressionPlan,
    CompressionResult,
    PageElements,
    PhaseError,
    PipelineContext,
    PreprocessResult,
)

logger = logging.getLogger("app.pipeline.assemble")

OUTPUT_REF = "output.pdf"
SIZE_DEVIATION_WARN = 0.3
_SAFE_FONT_ALIAS = re.compile(r"[^A-Za-z0-9]+")
_CJK_RE = re.compile(r"[⺀-鿿぀-ヿ가-힯豈-﫿]")


def assemble_pdf(
    pages: List[PageElements],
    plans: CompressionPlan,
    results: CompressionResult,
    preprocess: PreprocessResult,
    metadata: Dict[str, Any],
    ctx: PipelineContext,
) -> str:
    """返回最终 PDF 的绝对路径（tmp_workspace/output.pdf）。"""
    compressed_by_key: Dict[Tuple[int, int], str] = {
        (r.page_number, r.image_index): r.compressed_data_ref
        for r in results.per_image_results
    }
    subset_font_files: Dict[str, str] = {
        s.font_name: s.subsetted_data_ref for s in preprocess.subsetted_fonts
    }
    pages_by_number = {p.page_number: p for p in pages}
    font_checker = _FontChecker(ctx)

    new_doc = pymupdf.open()
    try:
        # 先建全部页面（pymupdf：new_page 会使旧 Page 对象失效，建完再逐页取用）
        for page_plan in plans.pages:
            src = pages_by_number[page_plan.page_number]
            new_doc.new_page(width=src.page_width, height=src.page_height)

        xref_by_stream: Dict[str, int] = {}
        toc: List[List] = []

        for index, page_plan in enumerate(plans.pages):
            src = pages_by_number[page_plan.page_number]
            out_page = new_doc[index]

            # 修复②：白色垫底（PDF 未着色区多数阅读器显白，但透明合成器可能显黑）
            out_page.draw_rect(out_page.rect, color=None, fill=(1, 1, 1), overlay=True)

            counts = {"raster": 0, "vector": 0, "text": 0, "link": 0}
            if page_plan.skip:
                counts["raster"] += _place_skip_page(out_page, src, ctx, xref_by_stream)
            else:
                counts["raster"] += _place_rasters(
                    out_page, src, page_plan, compressed_by_key, ctx, xref_by_stream)
                counts["vector"] += _place_vectors(out_page, src, page_plan, ctx,
                                                   xref_by_stream)
            counts["text"] += _place_text(out_page, src, subset_font_files,
                                          font_checker, ctx)
            counts["link"] += _place_links(out_page, src)
            for anno in src.annotations:
                if anno.anno_type == "bookmark":
                    toc.append([1, anno.target, page_plan.page_number])

            # 修复④：零插入 → 盖回原始单页（未压缩但绝不空白）
            total_inserted = counts["raster"] + counts["vector"] + counts["text"]
            fallback = ""
            if total_inserted == 0:
                if _stamp_original_page(out_page, ctx, page_plan.page_number):
                    fallback = " FALLBACK=original-page-stamped"
                else:
                    fallback = " FALLBACK-FAILED(page left blank)"
                logger.warning("page %d: zero elements inserted%s",
                               page_plan.page_number, fallback)
            logger.info(
                "page %d: raster %d/%d, vector %d/%d, text %d/%d, link %d%s",
                page_plan.page_number,
                counts["raster"], len(src.raster_images),
                counts["vector"], len(src.vector_paths),
                counts["text"], len(src.text_blocks),
                counts["link"], fallback,
            )

        if toc:
            new_doc.set_toc(toc)
        new_doc.set_metadata(_cleaned_metadata(metadata, preprocess))

        out_path = ctx.resolve_ref(OUTPUT_REF)
        try:
            new_doc.save(str(out_path), garbage=4, clean=True,
                         deflate=True, deflate_images=False)
        except Exception as exc:
            raise PhaseError(
                phase="assemble", code="ASSEMBLY_FAILED",
                message=f"save failed: {exc}", recoverable=False,
            ) from exc
    finally:
        new_doc.close()

    actual = out_path.stat().st_size
    expected = results.total_raster_bytes + preprocess.total_fixed_bytes
    if expected > 0 and abs(actual - expected) / expected > SIZE_DEVIATION_WARN:
        logger.warning(
            "output size %.2fMB deviates >%.0f%% from expectation %.2fMB",
            actual / 1e6, SIZE_DEVIATION_WARN * 100, expected / 1e6,
        )
    logger.info("assembled %d pages -> %s (%.2fMB)",
                len(plans.pages), OUTPUT_REF, actual / 1e6)
    return str(out_path)


# ---------------------------------------------------------------- 位图

def _insert_shared_image(out_page, rect: pymupdf.Rect, stream_ref: str,
                         ctx: PipelineContext, xref_by_stream: Dict[str, int]) -> None:
    """§3.1.1：首次嵌字节流并记录 xref，后续（含跨页）用 xref 复用。"""
    known = xref_by_stream.get(stream_ref)
    if known:
        out_page.insert_image(rect, xref=known)
        return
    data = ctx.resolve_ref(stream_ref).read_bytes()
    xref_by_stream[stream_ref] = out_page.insert_image(rect, stream=data)


def _bbox_rect(bbox) -> pymupdf.Rect:
    return pymupdf.Rect(bbox.x, bbox.y, bbox.x + bbox.w, bbox.y + bbox.h)


def _place_rasters(out_page, src, page_plan, compressed_by_key, ctx,
                   xref_by_stream) -> int:
    inserted = 0
    for rp in page_plan.raster_plans:
        image = src.raster_images[rp.image_index]
        stream_ref = compressed_by_key.get(
            (src.page_number, rp.image_index), image.data_ref)
        try:
            _insert_shared_image(out_page, _bbox_rect(image.bbox),
                                 stream_ref, ctx, xref_by_stream)
            inserted += 1
        except Exception:
            logger.warning("page %d image %d: insert failed, skipped",
                           src.page_number, rp.image_index, exc_info=True)
    return inserted


def _place_skip_page(out_page, src, ctx, xref_by_stream) -> int:
    inserted = 0
    for i, image in enumerate(src.raster_images):
        try:
            _insert_shared_image(out_page, _bbox_rect(image.bbox),
                                 image.data_ref, ctx, xref_by_stream)
            inserted += 1
        except Exception:
            logger.warning("skip page %d image %d: insert failed",
                           src.page_number, i, exc_info=True)
    return inserted


# ---------------------------------------------------------------- 矢量

def _place_vectors(out_page, src, page_plan, ctx, xref_by_stream) -> int:
    inserted = 0
    for vp in page_plan.vector_plans:
        vector = src.vector_paths[vp.path_group_index]
        try:
            if vp.strategy == "rasterize":
                ref = (f"vectors_rasterized/vec_{src.page_number:03d}"
                       f"_{vp.path_group_index:03d}.png")
                if ctx.resolve_ref(ref).is_file():
                    _insert_shared_image(out_page, _bbox_rect(vector.bbox),
                                         ref, ctx, xref_by_stream)
                    inserted += 1
                    continue
                logger.warning("page %d vec %d: rasterized file missing, redrawing",
                               src.page_number, vp.path_group_index)
                data_ref = vector.path_data_ref
            elif vp.strategy == "simplify":
                simplified = (f"vectors_simplified/vec_{src.page_number:03d}"
                              f"_{vp.path_group_index:03d}.bin")
                data_ref = (simplified if ctx.resolve_ref(simplified).is_file()
                            else vector.path_data_ref)
            else:  # keep
                data_ref = vector.path_data_ref
            primitives = pickle.loads(ctx.resolve_ref(data_ref).read_bytes())
            _redraw_primitives(out_page, primitives)
            inserted += 1
        except Exception:
            logger.warning("page %d vec %d (%s): placement failed, skipped",
                           src.page_number, vp.path_group_index, vp.strategy,
                           exc_info=True)
    return inserted


def _redraw_primitives(out_page, primitives: List[dict]) -> None:
    shape = out_page.new_shape()
    for prim in primitives:
        drew = False
        for op, pts in prim.get("items", []):
            if op == "l" and len(pts) == 2:
                shape.draw_line(pts[0], pts[1]); drew = True
            elif op == "c" and len(pts) == 4:
                shape.draw_bezier(pts[0], pts[1], pts[2], pts[3]); drew = True
            elif op == "re" and len(pts) == 4:
                xs = [p[0] for p in pts]; ys = [p[1] for p in pts]
                shape.draw_rect(pymupdf.Rect(min(xs), min(ys), max(xs), max(ys)))
                drew = True
            elif op == "qu" and len(pts) == 4:
                shape.draw_quad(pymupdf.Quad(pts[0], pts[1], pts[2], pts[3]))
                drew = True
        if not drew:
            continue
        shape.finish(
            color=prim.get("color"),
            fill=prim.get("fill"),
            width=prim.get("width") if prim.get("width") is not None else 1.0,
            even_odd=bool(prim.get("even_odd")),
            closePath=bool(prim.get("close_path")),
        )
    shape.commit()


# ---------------------------------------------------------------- 文字（修复③）

class _FontChecker:
    """字体 glyph 覆盖检查缓存：注册成功 ≠ 字形可用（无 cmap 字体画方块不报错）。"""

    def __init__(self, ctx: PipelineContext):
        self._ctx = ctx
        self._fonts: Dict[str, Optional[pymupdf.Font]] = {}

    def covers(self, file_ref: Optional[str], text: str) -> bool:
        if not file_ref:
            return False
        if file_ref not in self._fonts:
            try:
                self._fonts[file_ref] = pymupdf.Font(
                    fontfile=str(self._ctx.resolve_ref(file_ref)))
            except Exception:
                logger.warning("font file %s unusable for glyph check", file_ref)
                self._fonts[file_ref] = None
        font = self._fonts[file_ref]
        if font is None:
            return False
        sample = [ch for ch in text if not ch.isspace()][:8]
        return bool(sample) and all(font.has_glyph(ord(ch)) > 0 for ch in sample)


def _place_text(out_page, src, subset_font_files, font_checker: _FontChecker,
                ctx) -> int:
    inserted = 0
    registered: Dict[str, str] = {}  # (font_ref|builtin) → 本页 fontname
    for block in src.text_blocks:
        file_ref = subset_font_files.get(block.font_ref)
        if font_checker.covers(file_ref, block.content):
            alias = registered.get(block.font_ref)
            if alias is None:
                alias = "F" + (_SAFE_FONT_ALIAS.sub("", block.font_ref)[:20] or "font")
                try:
                    out_page.insert_font(fontname=alias,
                                         fontfile=str(ctx.resolve_ref(file_ref)))
                except Exception:
                    logger.warning("font %s: register failed despite glyph check",
                                   block.font_ref)
                    alias = _builtin_for(block.content)
                registered[block.font_ref] = alias
        else:
            # 无字体文件 / 无 cmap / 字形缺失 → 按内容选内置字体（CJK 用 china-s）
            alias = _builtin_for(block.content)

        point = pymupdf.Point(block.bbox.x, block.bbox.y + block.bbox.h * 0.8)
        try:
            out_page.insert_text(point, block.content,
                                 fontname=alias, fontsize=block.font_size)
            inserted += 1
        except Exception:
            fallback = _builtin_for(block.content)
            try:
                out_page.insert_text(point, block.content,
                                     fontname=fallback, fontsize=block.font_size)
                inserted += 1
            except Exception:
                logger.warning("page %d: text block skipped (font %s)",
                               src.page_number, block.font_ref)
    return inserted


def _builtin_for(text: str) -> str:
    return "china-s" if _CJK_RE.search(text) else "helv"


# ---------------------------------------------------------------- 回退（修复④）

def _stamp_original_page(out_page, ctx: PipelineContext, page_number: int) -> bool:
    """零插入页回退：把 split 的原始单页作为 Form XObject 盖回（未压缩，绝不空白）。"""
    try:
        src_path = ctx.resolve_ref(f"pages/page_{page_number}.pdf")
        if not src_path.is_file():
            return False
        src_doc = pymupdf.open(str(src_path))
        try:
            out_page.show_pdf_page(out_page.rect, src_doc, 0)
        finally:
            src_doc.close()
        return True
    except Exception:
        logger.warning("page %d: original-page fallback failed", page_number,
                       exc_info=True)
        return False


# ---------------------------------------------------------------- 链接 / 元数据

def _place_links(out_page, src) -> int:
    inserted = 0
    for anno in src.annotations:
        if anno.anno_type != "link":
            continue
        rect = _bbox_rect(anno.bbox)
        try:
            if anno.target.startswith("page:"):
                target_page = int(anno.target.split(":", 1)[1]) - 1
                if target_page >= 0:
                    out_page.insert_link({"kind": pymupdf.LINK_GOTO,
                                          "from": rect, "page": target_page})
                    inserted += 1
            else:
                out_page.insert_link({"kind": pymupdf.LINK_URI,
                                      "from": rect, "uri": anno.target})
                inserted += 1
        except Exception:
            logger.warning("page %d: link to %r failed", src.page_number,
                           anno.target, exc_info=True)
    return inserted


def _cleaned_metadata(metadata: Dict[str, Any],
                      preprocess: PreprocessResult) -> Dict[str, str]:
    keep_keys = ("title", "author", "subject", "keywords", "creator",
                 "producer", "creationDate", "modDate")
    cleaned = {k: str(metadata.get(k) or "") for k in keep_keys}
    for field in preprocess.stripped_metadata_fields:
        cleaned[field] = ""
    return cleaned
