"""Phase 10（运行别名 Phase F）：PDF 重组与输出——原地手术主干。

架构决策 A（2026-07-04，用户裁决）：不再从零重建页面，而是以 split 保存的
原件副本（tmp_workspace/source.pdf）为基底做**原地替换**：
- 位图：按 extract 的 sidecar（images/xref_map.json，data_ref → 源 xref）定位，
  用 xref_copy(new_xref, xref, keep=["SMask","Mask"]) 只替换 base 流本身
  （架构决策 A2，2026-07-04）——原 /SMask 引用原样保留，透明与压缩解耦。
  同内容多 xref 逐个替换；跨页共享的 xref 天然只有一份流（§3.1.1 的目标由
  PDF 结构自身保证）。
- 字体：子集化产物（retain_gids=True，字形 ID 稳定）原地替换 FontFile2 流。
- 文字、矢量、注释、书签、层序：**一个字节不动**——保真由构造保证。
  切换原因：无 ToUnicode 字体的文字层码点不可恢复，重建路线物理上无法
  还原此类中文（QC 实测三样本源文字层 CJK=0 但嵌有中文字体）。
- 元数据：唯一的语义修改点（隐私清理 author/creator）。

选择"最保守流"：同一 data_ref 在多页产生的多份压缩流中取字节数最大者替换。
skip 页限制（已记录）：xref 是文档级对象，若同一 xref 同时被 skip 页与非 skip
页引用，替换会影响 skip 页——sidecar 条目按首见页归属，首见页被 skip 则整条
不替换（保守处理）。

保存参数（手册指定）：garbage=4, clean=True, deflate=True, deflate_images=False。
重建路线保留于 assemble_rebuild.py（未来双轨选项 C 备用）。
"""

from __future__ import annotations

import json
import logging
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
from app.pipeline.extract import XREF_MAP_REF, _font_key

logger = logging.getLogger("app.pipeline.assemble")

OUTPUT_REF = "output.pdf"
SOURCE_REF = "source.pdf"
SIZE_DEVIATION_WARN = 0.3
_XREF_RE = re.compile(r"(\d+) 0 R")


def assemble_pdf(
    pages: List[PageElements],
    plans: CompressionPlan,
    results: CompressionResult,
    preprocess: PreprocessResult,
    metadata: Dict[str, Any],
    ctx: PipelineContext,
) -> str:
    """返回最终 PDF 的绝对路径（tmp_workspace/output.pdf）。"""
    source = ctx.resolve_ref(SOURCE_REF)
    if not source.is_file():
        raise PhaseError(
            phase="assemble", code="ASSEMBLY_FAILED",
            message="source.pdf missing in workspace (split must produce it)",
            recoverable=False,
        )
    xref_map = _load_sidecar(ctx)
    pages_by_number = {p.page_number: p for p in pages}
    skip_pages = {pp.page_number for pp in plans.pages if pp.skip}

    # data_ref → 最保守压缩流（多页多份流时取最大字节，只降不升的执行侧延续）
    best_stream: Dict[str, Tuple[int, str]] = {}
    for r in results.per_image_results:
        if not r.compressed_data_ref.startswith("compressed/"):
            continue  # 压缩失败/没赚到 → 引用原件 → 无需替换
        image = pages_by_number[r.page_number].raster_images[r.image_index]
        current = best_stream.get(image.data_ref)
        if current is None or r.compressed_bytes > current[0]:
            best_stream[image.data_ref] = (r.compressed_bytes, r.compressed_data_ref)

    doc = pymupdf.open(str(source))
    try:
        replaced_images = _replace_images(doc, best_stream, xref_map, skip_pages, ctx)
        replaced_fonts = _replace_fonts(doc, preprocess, ctx)
        doc.set_metadata(_cleaned_metadata(metadata, preprocess))

        out_path = ctx.resolve_ref(OUTPUT_REF)
        try:
            doc.save(str(out_path), garbage=4, clean=True,
                     deflate=True, deflate_images=False)
        except Exception as exc:
            raise PhaseError(
                phase="assemble", code="ASSEMBLY_FAILED",
                message=f"save failed: {exc}", recoverable=False,
            ) from exc
    finally:
        doc.close()

    actual = out_path.stat().st_size
    logger.info(
        "in-place assembled: %d image xrefs + %d font streams replaced -> %s (%.2fMB)",
        replaced_images, replaced_fonts, OUTPUT_REF, actual / 1e6,
    )
    expected = results.total_raster_bytes + preprocess.total_fixed_bytes
    if expected > 0 and abs(actual - expected) / expected > SIZE_DEVIATION_WARN:
        logger.warning(
            "output size %.2fMB deviates >%.0f%% from raster+fixed expectation %.2fMB "
            "(in-place keeps original vectors/fonts where not replaced)",
            actual / 1e6, SIZE_DEVIATION_WARN * 100, expected / 1e6,
        )
    return str(out_path)


# ---------------------------------------------------------------- 位图原地替换

def _load_sidecar(ctx: PipelineContext) -> dict:
    path = ctx.resolve_ref(XREF_MAP_REF)
    if not path.is_file():
        logger.warning("xref sidecar missing (%s) - no image will be replaced",
                       XREF_MAP_REF)
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _replace_images(doc, best_stream, xref_map, skip_pages, ctx) -> int:
    replaced = 0
    done_xrefs: set = set()
    for data_ref, (_nbytes, comp_ref) in sorted(best_stream.items()):
        entry = xref_map.get(data_ref)
        if entry is None:
            logger.warning("%s: no sidecar entry, skipped", data_ref)
            continue
        if entry["page"] in skip_pages:
            continue  # 保守：首见页被用户 skip → 整条不替换
        try:
            data = ctx.resolve_ref(comp_ref).read_bytes()
        except Exception:
            logger.warning("%s: compressed stream unreadable, skipped", comp_ref,
                           exc_info=True)
            continue
        page = doc[entry["page"] - 1]
        for xref in entry["xrefs"]:
            if xref in done_xrefs:
                continue
            try:
                # 只降不升（铁律 3）在替换点的落地：与 PDF 内原 base 对象流比大小
                # （A2 之后 base 与 SMask 一直是分离对象，比较对象天然一致）
                old_stream = doc.xref_stream_raw(xref) or b""
                if old_stream and len(data) >= len(old_stream):
                    logger.info("xref %d: new stream %d >= original %d, kept",
                                xref, len(data), len(old_stream))
                    continue
                _replace_base_stream(page, xref, data)
                done_xrefs.add(xref)
                replaced += 1
            except Exception:
                logger.warning("xref %d: replace_image failed, original kept",
                               xref, exc_info=True)
    return replaced


def _replace_base_stream(page, xref: int, stream: bytes) -> None:
    """替换 base 图像流，保留 /SMask、/Mask 键不变（架构决策 A2，2026-07-04）。

    等价于 pymupdf.Page.replace_image()，但后者内部 xref_copy(new, old) 是全量
    覆盖字典——会把已有的 /SMask 引用一并清空，真透明图会因此丢失透明度。这里
    换成 xref_copy(..., keep=["SMask", "Mask"])：old xref 已有的这两个键在覆盖
    前不会被清空；新插入的 base 图像本身不含 alpha，也不会用新键覆盖它们，原
    透明关系因此原样保留。
    """
    doc = page.parent
    new_xref = page.insert_image(page.rect, stream=stream, alpha=0)
    doc.xref_copy(new_xref, xref, keep=["SMask", "Mask"])
    last_contents_xref = page.get_contents()[-1]
    doc.update_stream(last_contents_xref, b" ")
    page._image_info = None


# ---------------------------------------------------------------- 字体原地替换

def _replace_fonts(doc, preprocess: PreprocessResult, ctx) -> int:
    """子集化字体流替换。仅处理真正子集化成功的（fonts_subsetted/ 前缀），
    仅 TrueType（FontFile2）；替换前校验只降不升。失败逐个跳过、保留原字体。"""
    subs = {
        s.font_name: s
        for s in preprocess.subsetted_fonts
        if s.subsetted_data_ref.startswith("fonts_subsetted/")
        and s.subsetted_bytes < s.original_bytes
    }
    if not subs:
        return 0

    replaced = 0
    seen_font_xrefs: set = set()
    for page_index in range(doc.page_count):
        try:
            font_list = doc[page_index].get_fonts(full=True)
        except Exception:
            continue
        for entry in font_list:
            font_xref, basefont = entry[0], entry[3]
            if font_xref in seen_font_xrefs:
                continue
            seen_font_xrefs.add(font_xref)
            sub = subs.get(_font_key(basefont))
            if sub is None:
                continue
            ff_xref = _find_fontfile2(doc, font_xref)
            if ff_xref is None:
                continue
            try:
                new_bytes = ctx.resolve_ref(sub.subsetted_data_ref).read_bytes()
                old_len = len(doc.xref_stream(ff_xref) or b"")
                if old_len and len(new_bytes) >= old_len:
                    continue  # 只降不升
                doc.update_stream(ff_xref, new_bytes)
                doc.xref_set_key(ff_xref, "Length1", str(len(new_bytes)))
                replaced += 1
                logger.info("font %s: FontFile2 %d bytes -> %d",
                            sub.font_name, old_len, len(new_bytes))
            except Exception:
                logger.warning("font %s: in-place replace failed, original kept",
                               sub.font_name, exc_info=True)
    return replaced


def _find_fontfile2(doc, font_xref: int) -> Optional[int]:
    """Font（含 Type0 → DescendantFonts）→ FontDescriptor → FontFile2 流 xref。"""
    try:
        target = font_xref
        _t, subtype = doc.xref_get_key(font_xref, "Subtype")
        if subtype == "/Type0":
            _t, desc = doc.xref_get_key(font_xref, "DescendantFonts")
            match = _XREF_RE.search(desc or "")
            if not match:
                return None
            target = int(match.group(1))
        _t, fd = doc.xref_get_key(target, "FontDescriptor")
        match = _XREF_RE.search(fd or "")
        if not match:
            return None
        _t, ff = doc.xref_get_key(int(match.group(1)), "FontFile2")
        match = _XREF_RE.search(ff or "")
        return int(match.group(1)) if match else None
    except Exception:
        return None


# ---------------------------------------------------------------- 元数据

def _cleaned_metadata(metadata: Dict[str, Any],
                      preprocess: PreprocessResult) -> Dict[str, str]:
    keep_keys = ("title", "author", "subject", "keywords", "creator",
                 "producer", "creationDate", "modDate")
    cleaned = {k: str(metadata.get(k) or "") for k in keep_keys}
    for field in preprocess.stripped_metadata_fields:
        cleaned[field] = ""
    return cleaned
