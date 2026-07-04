"""Phase 5（运行别名 Phase B）：元素提取。

对每个 RawPage 深度解析，提取位图、矢量、文字、字体、XObject、注释，
并构建 PDF 级字体使用表（跨页累计字符集）与全局元数据。

契约 Producer 职责（修订 2026-07-04 首次落地）：
- RasterImage.original_bytes = 写入 data_ref 文件的 len(bytes)
- VectorPath.original_bytes  = pickle 序列化后写入 path_data_ref 的字节数

API 风格：pymupdf 1.28 新式 API（import pymupdf），与 split.py 一致。
日志：logger 名固定 "app.pipeline.extract"；单页失败 → warning + 空 PageElements。
所有 ref 均经 PipelineContext.resolve_ref() 卡口（《数据契约.md》§1.6）。

V1 矢量分组决策：每页所有 drawing 聚合为一个 VectorPath 组（path_group_index=0）。
理由：Phase D 的 keep/simplify/rasterize 策略语义单位是"页面上的矢量群"——
CAD 页 5 万条 2 控制点小线段若按 drawing 逐条建组，每组控制点极少，
永远触发不了 rasterize 阈值（100k），策略退化；聚合后控制点总数才反映
真实复杂度（与母本 §6.1 is_complex_vector 按页求和一致）。契约本身是
List[VectorPath]，未来做空间聚类分组无需改契约。
"""

from __future__ import annotations

import hashlib
import json
import logging
import pickle
import re
from typing import Any, Dict, List, Optional, Tuple

import pymupdf

from app.config.settings import get_settings
from app.contracts import (
    Annotation,
    BBox,
    FontUsage,
    PageElements,
    PhaseError,
    PipelineContext,
    RasterImage,
    RawPage,
    TextBlock,
    VectorPath,
    XObject,
)

logger = logging.getLogger("app.pipeline.extract")

IMAGES_DIR = "images"
VECTORS_DIR = "vectors"
FONTS_DIR = "fonts"

_SUBSET_PREFIX = re.compile(r"^[A-Z]{6}\+")   # 子集字体前缀，如 "ABCDEF+Calibri"
_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")
_SAFE_EXT = re.compile(r"^[a-z0-9]{1,10}$")


# ---------------------------------------------------------------- 工具

def _rect_to_bbox(rect) -> BBox:
    return BBox(
        x=float(rect[0]), y=float(rect[1]),
        w=max(float(rect[2]) - float(rect[0]), 0.0),
        h=max(float(rect[3]) - float(rect[1]), 0.0),
    )


def _font_key(name: Optional[str]) -> str:
    """去掉子集前缀的字体名，作为 fonts_used 的统一 key。"""
    return _SUBSET_PREFIX.sub("", name or "").strip() or "unknown"


def _safe_filename(name: str) -> str:
    return (_SAFE_NAME.sub("_", name)[:80]) or "font"


def _color_to_hex(color) -> Optional[str]:
    if color is None:
        return None
    try:
        r, g, b = (max(0.0, min(1.0, float(c))) for c in tuple(color)[:3])
        return f"#{int(r * 255):02x}{int(g * 255):02x}{int(b * 255):02x}"
    except Exception:
        return None


# ---------------------------------------------------------------- 主入口

def extract_elements(
    raw_pages: List[RawPage],
    original_pdf_path: str,
    ctx: PipelineContext,
) -> Tuple[List[PageElements], Dict[str, FontUsage], Dict[str, Any]]:
    try:
        doc = pymupdf.open(original_pdf_path)
    except Exception as exc:
        raise PhaseError(
            phase="extract", code="INVALID_PDF",
            message=f"cannot reopen source PDF: {exc}", recoverable=False,
        ) from exc

    try:
        for sub in (IMAGES_DIR, VECTORS_DIR, FONTS_DIR):
            ctx.resolve_ref(sub).mkdir(parents=True, exist_ok=True)

        fonts_used = _extract_fonts(doc, ctx)

        pages_out: List[PageElements] = []
        for raw in raw_pages:
            try:
                pages_out.append(_extract_page(doc, raw, ctx, fonts_used))
            except Exception:
                # 单页失败不阻塞：warning + 空 PageElements
                logger.warning(
                    "page %d extraction failed, returning empty elements",
                    raw.page_number, exc_info=True,
                )
                pages_out.append(PageElements(
                    page_number=raw.page_number,
                    page_width=raw.page_width,
                    page_height=raw.page_height,
                ))

        _attach_bookmarks(doc, pages_out)
        metadata: Dict[str, Any] = dict(doc.metadata or {})
        logger.info(
            "extracted %d pages: %d raster, %d vector groups, %d text blocks, %d fonts",
            len(pages_out),
            sum(len(p.raster_images) for p in pages_out),
            sum(len(p.vector_paths) for p in pages_out),
            sum(len(p.text_blocks) for p in pages_out),
            len(fonts_used),
        )
        return pages_out, fonts_used, metadata
    finally:
        doc.close()


# ---------------------------------------------------------------- 字体

def _extract_fonts(doc, ctx: PipelineContext) -> Dict[str, FontUsage]:
    """收集全文档字体清单并提取字体字节。chars_used 由文字提取阶段累计。"""
    fonts: Dict[str, FontUsage] = {}
    for page_index in range(doc.page_count):
        try:
            font_list = doc[page_index].get_fonts(full=True)
        except Exception:
            logger.warning("get_fonts failed on page %d", page_index + 1, exc_info=True)
            continue
        for entry in font_list:
            xref, _ext, _ftype, basefont = entry[0], entry[1], entry[2], entry[3]
            key = _font_key(basefont)
            if key in fonts:
                continue
            data_ref: Optional[str] = None
            try:
                _name, ext, _stype, buffer = doc.extract_font(xref)
                if buffer:
                    ext = ext if _SAFE_EXT.match(ext or "") else "ttf"
                    ref = f"{FONTS_DIR}/font_{_safe_filename(key)}.{ext}"
                    ctx.resolve_ref(ref).write_bytes(buffer)
                    data_ref = ref
                else:
                    logger.warning("font %s: not embedded, skip bytes", key)
            except Exception:
                logger.warning("font %s: extraction failed", key, exc_info=True)
            fonts[key] = FontUsage(font_name=key, data_ref=data_ref, chars_used=set())
    return fonts


# ---------------------------------------------------------------- 单页提取

def _extract_page(
    doc, raw: RawPage, ctx: PipelineContext, fonts_used: Dict[str, FontUsage]
) -> PageElements:
    page = doc[raw.page_number - 1]
    return PageElements(
        page_number=raw.page_number,
        page_width=raw.page_width,
        page_height=raw.page_height,
        raster_images=_extract_rasters(doc, page, raw.page_number, ctx),
        vector_paths=_extract_vectors(page, raw.page_number, ctx),
        text_blocks=_extract_text(page, fonts_used),
        xobjects=_extract_xobjects(page),
        annotations=_extract_annotations(page),
    )


# alpha 采样分析常量（提取层启发式，非压缩参数，不入 config）
ALPHA_SAMPLE_TARGET = 512     # 采样像素数上限（数百点足够判定，不扫全图）
ALPHA_OPAQUE_RATIO = 0.99     # ≥99% 采样像素为 255 → opaque（伪透明）


def _classify_alpha(doc, smask_xref: int) -> str:
    """SMask 像素采样统计 → "opaque"（伪透明）或 "translucent"（真透明）。

    修订 2026-07-04：生产数据显示 ~97% 位图带 SMask，必须区分伪/真透明，
    否则 Phase 8 全按 PNG 处理会使激进目标不可收敛。读取失败按真透明保守处理。
    """
    try:
        pix = pymupdf.Pixmap(doc, smask_xref)
        samples = bytes(pix.samples)
        if not samples:
            return "translucent"
        step = max(1, len(samples) // ALPHA_SAMPLE_TARGET)
        sampled = samples[::step]
        opaque_hits = sum(1 for v in sampled if v == 255)
        if opaque_hits / len(sampled) >= ALPHA_OPAQUE_RATIO:
            return "opaque"
        return "translucent"
    except Exception:
        return "translucent"


def _merge_smask(doc, xref: int, smask_xref: int):
    """把 SMask 合并进基底图的 alpha 通道，返回 (png_bytes, width, height)。

    失败返回 None（调用方保留基底图并记 warning）。CMYK 等非 RGB 基底先转 RGB
    （PNG 不支持 CMYK；Pixmap(base, mask) 要求无 alpha 的基底 + 同尺寸灰度掩膜）。
    """
    try:
        base = pymupdf.Pixmap(doc, xref)
        if base.alpha:  # 基底自带 alpha 的极少数情况：直接导出
            data = base.tobytes("png")
            return data, base.width, base.height
        if base.colorspace is None or base.colorspace.n > 3:
            base = pymupdf.Pixmap(pymupdf.csRGB, base)
        mask = pymupdf.Pixmap(doc, smask_xref)
        if (mask.width, mask.height) != (base.width, base.height):
            mask = pymupdf.Pixmap(mask, base.width, base.height, None)  # 缩放到同尺寸
        merged = pymupdf.Pixmap(base, mask)
        data = merged.tobytes("png")
        return data, merged.width, merged.height
    except Exception:
        logger.warning("smask merge failed (xref %d, smask %d)", xref, smask_xref,
                       exc_info=True)
        return None


def _extract_rasters(doc, page, page_number: int, ctx: PipelineContext) -> List[RasterImage]:
    """提取本页位图。

    去重决策（2026-07-04，数据驱动）：真实样本中少数贴图被平铺摆放数千次
    （单页 6000+ 摆放 vs 全文档仅 ~300 个唯一图像）。每页每个 xref 只写一次
    字节，多处摆放共享同一 data_ref——契约未要求 data_ref 唯一，Phase 9 可按
    data_ref 分组去重压缩，Phase 10 同一压缩流多处插入。
    """
    rasters: List[RasterImage] = []
    file_index = 0  # 每页唯一图像文件计数（非摆放计数）
    seen_digests: Dict[bytes, str] = {}  # digest → data_ref（同内容不同 xref 去重）
    xref_map = _load_xref_map(ctx)  # sidecar：data_ref → 源 xref（原地替换定位用）
    for item in page.get_images(full=True):  # full=True 覆盖嵌套在 Form XObject 里的图
        xref, smask = item[0], item[1]
        try:
            rects = page.get_image_rects(item)
        except Exception:
            rects = []
        if not rects:
            continue  # 引用了但未显示
        try:
            info = doc.extract_image(xref)
        except Exception:
            logger.warning("page %d: image xref %d extraction failed", page_number, xref,
                           exc_info=True)
            continue
        img_bytes: bytes = info["image"]
        # 修复 2026-07-04（Phase 10 测试发现）：pymupdf get_image_rects 按内容摘要
        # 匹配摆放——同内容的 N 个 xref 各自返回全部 M 个矩形，导致 N×M 条目膨胀。
        # 按字节摘要去重：同内容只处理一次（其 rects 已含全部摆放）。
        digest = hashlib.md5(img_bytes).digest()
        if digest in seen_digests:
            # 同内容的额外 xref：不重复建条目，但记入 sidecar 供原地替换逐个处理
            dup_ref = seen_digests[digest]
            if xref not in xref_map[dup_ref]["xrefs"]:
                xref_map[dup_ref]["xrefs"].append(xref)
            continue
        width, height = int(info["width"]), int(info["height"])
        if width <= 0 or height <= 0 or not img_bytes:
            continue
        color_space = str(info.get("cs-name") or info.get("colorspace") or "unknown")
        alpha_type = _classify_alpha(doc, smask) if smask else "none"

        # 修复 2026-07-04（目视验收问题一/二/五根因）：extract_image 返回的是
        # 不含透明的基底图，而阴影/压暗层的基底常是纯黑像素 + SMask。若原样保存，
        # 重组时半透明黑纱会变成实心黑板（白底变黑/整页全黑/盖掉矢量）。
        # 真透明图必须把 SMask 合并进 alpha 通道后以 PNG 保存。
        if alpha_type == "translucent":
            merged = _merge_smask(doc, xref, smask)
            if merged is not None:
                img_bytes, width, height = merged
            else:
                logger.warning(
                    "page %d xref %d: SMask merge failed, keeping base image",
                    page_number, xref,
                )

        # 每个 xref 每页只落盘一次；所有摆放共享同一 data_ref
        ref = f"{IMAGES_DIR}/img_{page_number:03d}_{file_index:03d}.bin"
        ctx.resolve_ref(ref).write_bytes(img_bytes)
        file_index += 1
        seen_digests[digest] = ref
        # sidecar 登记源 xref（原地手术主干的替换定位键，2026-07-04 架构决策 A）
        xref_map[ref] = {"page": page_number, "xrefs": [xref], "smask": smask}

        for rect in rects:  # 同一 xref 多处摆放 → 每处一个元素（bbox/dpi 不同）
            if rect.width <= 0 or rect.height <= 0:
                continue
            rasters.append(RasterImage(
                data_ref=ref,
                width=width,
                height=height,
                dpi=width / (rect.width / 72.0),  # 反算 DPI（rect 单位 points）
                color_space=color_space,
                has_alpha=(alpha_type == "translucent"),  # 兼容性快捷字段
                alpha_type=alpha_type,
                original_bytes=len(img_bytes),  # ← Producer 落地（修订 2026-07-04）
                bbox=_rect_to_bbox((rect.x0, rect.y0, rect.x1, rect.y1)),
            ))
    _save_xref_map(ctx, xref_map)
    return rasters


XREF_MAP_REF = f"{IMAGES_DIR}/xref_map.json"


def _load_xref_map(ctx: PipelineContext) -> dict:
    """sidecar：data_ref → {page, xrefs[], smask}。工作区内部工件（非契约），
    与 pickle 文件同性质；Producer=extract，Consumer=assemble（原地替换定位）。"""
    path = ctx.resolve_ref(XREF_MAP_REF)
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {}


def _save_xref_map(ctx: PipelineContext, xref_map: dict) -> None:
    ctx.resolve_ref(XREF_MAP_REF).write_text(
        json.dumps(xref_map, ensure_ascii=False), encoding="utf-8"
    )


def _drawing_to_primitives(d: dict) -> dict:
    """把 pymupdf drawing 转成纯 Python 基元（可 pickle、Phase 10 可重绘）。"""
    items_out = []
    for item in d.get("items", []):
        op = item[0]
        if op == "l":
            pts = [tuple(item[1]), tuple(item[2])]
        elif op == "c":
            pts = [tuple(p) for p in item[1:5]]
        elif op == "re":
            r = item[1]
            pts = [(r.x0, r.y0), (r.x1, r.y0), (r.x1, r.y1), (r.x0, r.y1)]
        elif op == "qu":
            q = item[1]
            pts = [tuple(q.ul), tuple(q.ur), tuple(q.ll), tuple(q.lr)]
        else:
            pts = []
        items_out.append((op, pts))
    rect = d.get("rect")
    return {
        "items": items_out,
        "rect": (rect.x0, rect.y0, rect.x1, rect.y1) if rect is not None else None,
        "color": tuple(d["color"]) if d.get("color") else None,
        "fill": tuple(d["fill"]) if d.get("fill") else None,
        "width": d.get("width"),
        "even_odd": d.get("even_odd"),
        "close_path": d.get("closePath"),
    }


def _extract_vectors(page, page_number: int, ctx: PipelineContext) -> List[VectorPath]:
    drawings = page.get_drawings()
    if not drawings:
        return []
    primitives = [_drawing_to_primitives(d) for d in drawings]
    control_points = sum(
        len(pts) for prim in primitives for _op, pts in prim["items"]
    )
    xs0, ys0, xs1, ys1 = [], [], [], []
    for prim in primitives:
        if prim["rect"] is not None:
            x0, y0, x1, y1 = prim["rect"]
            xs0.append(x0); ys0.append(y0); xs1.append(x1); ys1.append(y1)
    if not xs0:
        return []
    payload = pickle.dumps(primitives, protocol=pickle.HIGHEST_PROTOCOL)
    ref = f"{VECTORS_DIR}/vec_{page_number:03d}_000.bin"
    ctx.resolve_ref(ref).write_bytes(payload)

    stroke = next((_color_to_hex(p["color"]) for p in primitives if p["color"]), None)
    fill = next((_color_to_hex(p["fill"]) for p in primitives if p["fill"]), None)
    # 语义修订 2026-07-04：original_bytes = 内容流预估输出成本（cp × config 系数），
    # 不是 pickle 大小（pickle ~31.6 B/cp 是内存序列化成本，作预算会膨胀 10 倍）。
    # pickle 文件照旧存盘，仅供 Phase 10 重绘。
    estimated_stream_bytes = int(
        control_points * get_settings().compression.vector_bytes_per_control_point
    )
    return [VectorPath(
        path_data_ref=ref,
        control_point_count=control_points,
        stroke_color=stroke,
        fill_color=fill,
        original_bytes=estimated_stream_bytes,
        bbox=_rect_to_bbox((min(xs0), min(ys0), max(xs1), max(ys1))),
    )]


def _extract_text(page, fonts_used: Dict[str, FontUsage]) -> List[TextBlock]:
    blocks: List[TextBlock] = []
    text_dict = page.get_text("dict")
    for block in text_dict.get("blocks", []):
        if block.get("type") != 0:  # 0 = 文本块
            continue
        for line in block.get("lines", []):
            for span in line.get("spans", []):
                content = span.get("text", "")
                if not content.strip():
                    continue
                key = _font_key(span.get("font"))
                # 跨页累计字符集；span 字体不在字体清单时补一个 data_ref=None 条目
                usage = fonts_used.setdefault(key, FontUsage(font_name=key))
                usage.chars_used.update(content)
                blocks.append(TextBlock(
                    content=content,
                    font_ref=key,
                    font_size=max(float(span.get("size", 1.0)), 0.1),
                    bbox=_rect_to_bbox(span["bbox"]),
                ))
    return blocks


def _extract_xobjects(page) -> List[XObject]:
    """Form XObject 记录（图片内容已由 get_images(full=True) 展开提取）。"""
    out: List[XObject] = []
    try:
        entries = page.get_xobjects()
    except Exception:
        logger.warning("get_xobjects failed on page %d", page.number + 1, exc_info=True)
        return out
    for entry in entries:
        try:
            xref, name, _invoker, rect = entry[0], entry[1], entry[2], entry[3]
            out.append(XObject(
                ref_id=str(name or xref),
                xobject_type="form",
                bbox=_rect_to_bbox((rect[0], rect[1], rect[2], rect[3])),
            ))
        except Exception:
            logger.warning("bad xobject entry on page %d: %r", page.number + 1, entry)
    return out


def _extract_annotations(page) -> List[Annotation]:
    annos: List[Annotation] = []
    # 超链接
    try:
        for link in page.get_links():
            rect = link.get("from")
            target = link.get("uri") or f"page:{link.get('page', -1) + 1}"
            annos.append(Annotation(
                anno_type="link", target=str(target),
                bbox=_rect_to_bbox((rect.x0, rect.y0, rect.x1, rect.y1)),
            ))
    except Exception:
        logger.warning("get_links failed on page %d", page.number + 1, exc_info=True)
    # 表单字段
    try:
        for widget in page.widgets() or []:
            annos.append(Annotation(
                anno_type="form_field", target=str(widget.field_name or ""),
                bbox=_rect_to_bbox(tuple(widget.rect)),
            ))
    except Exception:
        logger.warning("widgets() failed on page %d", page.number + 1, exc_info=True)
    # 文本注释（Link 注释已由 get_links 覆盖，跳过）
    try:
        for annot in page.annots() or []:
            if annot.type[0] == pymupdf.PDF_ANNOT_LINK:
                continue
            info = annot.info or {}
            annos.append(Annotation(
                anno_type="comment",
                target=str(info.get("content") or annot.type[1]),
                bbox=_rect_to_bbox(tuple(annot.rect)),
            ))
    except Exception:
        logger.warning("annots() failed on page %d", page.number + 1, exc_info=True)
    return annos


def _attach_bookmarks(doc, pages_out: List[PageElements]) -> None:
    """书签（TOC）是文档级数据，挂到目标页的 annotations（bbox 用零占位）。"""
    try:
        toc = doc.get_toc(simple=True)
    except Exception:
        logger.warning("get_toc failed", exc_info=True)
        return
    by_number = {p.page_number: p for p in pages_out}
    for entry in toc:
        _level, title, target_page = entry[0], entry[1], entry[2]
        target = by_number.get(int(target_page))
        if target is None:
            continue  # 目标页拆页失败或超界
        target.annotations.append(Annotation(
            anno_type="bookmark", target=str(title),
            bbox=BBox(x=0.0, y=0.0, w=0.0, h=0.0),
        ))
