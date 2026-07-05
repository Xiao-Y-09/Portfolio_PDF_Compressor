"""Phase 9（运行别名 Phase E）：压缩执行——只按 CompressionPlan 干活，不做决策。

铁律 1 分界线：本模块是流水线中**唯一**允许调用 PIL / pymupdf 做像素级操作的
执行层（decide.py 纯函数禁止触碰图像库）。

共享 data_ref 去重（《数据契约.md》§3.1.1 + 用户关注点 2）：
- 每页每唯一 data_ref 只压缩一次；
- 同组多个 RasterPlan 取**最保守参数**（max quality / max max_dimension）；
- 组内所有摆放的 ImageResult 共享同一 compressed_data_ref；
- total_raster_bytes 按唯一流计（与 decide 的预算口径一致）。

错误容忍（手册 Phase 9）：单张压缩失败 → warning + 原图凑数；
整体成功率 < SUCCESS_RATE_MIN → PhaseError。
只降不升：压缩产物 ≥ 原件时丢弃产物、引用原件。

收敛循环不在本模块：orchestrator（Phase 11）负责 decide↔compress 的多轮组织，
本模块只执行单轮。
"""

from __future__ import annotations

import io
import logging
import pickle
from typing import Dict, List, Tuple

import pymupdf
from PIL import Image

from app.config.settings import CompressionConfig
from app.contracts import (
    CompressionPlan,
    CompressionResult,
    ImageResult,
    PageElements,
    PhaseError,
    PipelineContext,
    RasterPlan,
    VectorPlan,
)

logger = logging.getLogger("app.pipeline.compress")

COMPRESSED_DIR = "compressed"
VEC_SIMPLIFIED_DIR = "vectors_simplified"
VEC_RASTERIZED_DIR = "vectors_rasterized"
# 整页光栅化产物命名约定（Tier 系统 2026-07-04）：工作区内部工件（非契约），
# Producer = 本模块 _render_whole_page，Consumer = assemble._replace_whole_pages
# （按 plan.pages[].whole_page_plan + page_number 确定性重建同一 ref）
WHOLE_PAGE_REF = COMPRESSED_DIR + "/whole_page_{page:03d}.jpg"

# 执行层健壮性阈值（手册 Phase 9："整体成功率 < 80% → 抛出 PhaseError"）
SUCCESS_RATE_MIN = 0.8
# PNG 编码参数改由 config 提供（质量优化 4，2026-07-04）：
# 实测 level≥6 体积零差别、optimize 无增益，默认 level=6 + optimize=False


def execute_compression(
    plan: CompressionPlan,
    pages: List[PageElements],
    ctx: PipelineContext,
    config: CompressionConfig,
) -> CompressionResult:
    for sub in (COMPRESSED_DIR, VEC_SIMPLIFIED_DIR, VEC_RASTERIZED_DIR):
        ctx.resolve_ref(sub).mkdir(parents=True, exist_ok=True)

    pages_by_number = {p.page_number: p for p in pages}
    per_image_results: List[ImageResult] = []
    unique_stream_bytes: Dict[Tuple[int, str], int] = {}
    attempts = 0
    failures = 0

    for page_plan in plan.pages:
        if page_plan.skip:
            continue  # 用户意志优先：skip 页不触碰
        page = pages_by_number[page_plan.page_number]

        # ---- 整页光栅化（Tier 2/3，§8.5-8.6）：渲染后本页不再有元素级处理 ----
        if page_plan.whole_page_plan is not None:
            attempts += 1
            try:
                out_ref, out_bytes = _render_whole_page(
                    ctx, page_plan.page_number, page_plan.whole_page_plan
                )
                unique_stream_bytes[(page_plan.page_number, out_ref)] = out_bytes
            except Exception:
                logger.warning(
                    "page %d: whole-page rasterization failed, page left in place",
                    page_plan.page_number, exc_info=True,
                )
                failures += 1
            continue

        # ---- 位图：按 data_ref 分组去重压缩 ----
        groups: Dict[str, List[RasterPlan]] = {}
        for rp in page_plan.raster_plans:
            ref = page.raster_images[rp.image_index].data_ref
            groups.setdefault(ref, []).append(rp)

        for src_ref, group in groups.items():
            attempts += 1
            image = page.raster_images[group[0].image_index]
            # 最保守参数：质量取最高、尺寸上限取最大（多摆放中最苛刻的需求胜出）
            quality = max(rp.quality for rp in group)
            max_dimension = max(rp.max_dimension for rp in group)
            output_format = group[0].output_format  # 同一图像 alpha_type 相同
            try:
                out_ref, out_bytes, resize_factor = _compress_image(
                    ctx, src_ref, quality, max_dimension, output_format,
                    grayscale=image.is_grayscale, config=config,
                )
            except Exception:
                logger.warning(
                    "page %d image %s: compression failed, using original",
                    page_plan.page_number, src_ref, exc_info=True,
                )
                failures += 1
                out_ref, out_bytes, resize_factor = src_ref, image.original_bytes, 1.0

            unique_stream_bytes[(page_plan.page_number, out_ref)] = out_bytes
            for rp in group:
                per_image_results.append(ImageResult(
                    page_number=page_plan.page_number,
                    image_index=rp.image_index,
                    compressed_bytes=out_bytes,
                    compressed_data_ref=out_ref,
                    actual_dpi=max(rp.original_dpi * resize_factor, 1.0),
                    actual_quality=quality,
                ))

        # ---- 矢量：keep 不动 / simplify DP / rasterize 渲染 ----
        for vp in page_plan.vector_plans:
            if vp.strategy == "keep":
                continue
            attempts += 1
            try:
                _execute_vector(ctx, page, page_plan.page_number, vp)
            except Exception:
                logger.warning(
                    "page %d vector group %d: %s failed, keeping original",
                    page_plan.page_number, vp.path_group_index, vp.strategy,
                    exc_info=True,
                )
                failures += 1

    if attempts > 0 and (attempts - failures) / attempts < SUCCESS_RATE_MIN:
        raise PhaseError(
            phase="compress",
            code="EXECUTION_FAILURE_RATE",
            message=f"compression success rate {(attempts - failures) / attempts:.0%} "
                    f"below {SUCCESS_RATE_MIN:.0%} ({failures}/{attempts} failed)",
            recoverable=False,
        )

    total = sum(unique_stream_bytes.values())
    logger.info(
        "round %d executed: %d unique streams from %d placements, "
        "total_raster_bytes=%.2fMB, failures=%d/%d",
        plan.round_number, len(unique_stream_bytes), len(per_image_results),
        total / 1e6, failures, attempts,
    )
    return CompressionResult(
        total_raster_bytes=total,
        per_image_results=per_image_results,
        round_number=plan.round_number,
        base_aggressiveness=plan.base_aggressiveness,  # 原样回带（反馈环闭合）
        tier_used=plan.tier,                           # 原样回带（同上通路）
    )


# ---------------------------------------------------------------- 整页光栅化（Tier 2/3）

def _render_whole_page(ctx: PipelineContext, page_number: int, wpp) -> Tuple[str, int]:
    """整页渲染原语的执行侧：split 单页 PDF → 指定 DPI 渲染 → JPEG。

    Tier 2 与 Tier 3 共用（页集合由 decide 决定，本函数不做决策）。
    页内容替换在 assemble._replace_whole_pages（职责分离：渲染归执行层，
    替换归组装层）。返回 (输出 ref, 字节数)。
    """
    page_pdf = ctx.resolve_ref(f"pages/page_{page_number}.pdf")
    doc = pymupdf.open(str(page_pdf))
    try:
        zoom = wpp.dpi / 72.0
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom))
    finally:
        doc.close()
    im = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    out_ref = WHOLE_PAGE_REF.format(page=page_number)
    out_path = ctx.resolve_ref(out_ref)
    im.save(str(out_path), "JPEG", quality=wpp.quality, optimize=True)
    return out_ref, out_path.stat().st_size


# ---------------------------------------------------------------- 位图执行

def _compress_image(
    ctx: PipelineContext, src_ref: str, quality: int, max_dimension: int, fmt: str,
    grayscale: bool = False, config: CompressionConfig = None,
) -> Tuple[str, int, float]:
    """压缩单个唯一图像流。返回 (输出 ref, 字节数, 缩放系数)。

    只降不升：产物不小于原件 → 丢弃产物、返回原件引用。
    质量优化 2：is_grayscale 图转 L/LA 编码（3→1 通道）。
    """
    src_path = ctx.resolve_ref(src_ref)
    original_data = src_path.read_bytes()
    im = Image.open(io.BytesIO(original_data))
    im.load()

    long_edge = max(im.width, im.height)
    resize_factor = min(1.0, max_dimension / long_edge)
    if resize_factor < 1.0:
        new_size = (max(1, round(im.width * resize_factor)),
                    max(1, round(im.height * resize_factor)))
        im = im.resize(new_size, Image.LANCZOS)

    stem = src_ref.rsplit("/", 1)[-1].rsplit(".", 1)[0]
    if fmt == "jpeg":
        if im.mode in ("RGBA", "LA", "PA"):
            # 伪透明/无效 alpha 压平到白底（真透明在 decide 已走 png 分支）
            background = Image.new("RGB", im.size, (255, 255, 255))
            background.paste(im, mask=im.getchannel("A"))
            im = background
        elif im.mode not in ("RGB", "L"):
            im = im.convert("RGB")
        if grayscale and im.mode != "L":
            im = im.convert("L")  # 3→1 通道
        out_ref = f"{COMPRESSED_DIR}/{stem}.jpg"
        out_path = ctx.resolve_ref(out_ref)
        im.save(str(out_path), "JPEG", quality=quality, optimize=True)
    else:  # png
        if grayscale:
            target_mode = "LA" if ("A" in im.mode or im.mode == "P") else "L"
            if im.mode != target_mode:
                im = im.convert(target_mode)  # 3→1 通道（保 alpha）
        elif im.mode not in ("RGBA", "LA"):
            im = im.convert("RGBA")
        out_ref = f"{COMPRESSED_DIR}/{stem}.png"
        out_path = ctx.resolve_ref(out_ref)
        level = config.png_compress_level if config is not None else 6
        optimize = config.png_optimize if config is not None else False
        im.save(str(out_path), "PNG", optimize=optimize, compress_level=level)

    out_bytes = out_path.stat().st_size
    if out_bytes >= len(original_data):
        out_path.unlink(missing_ok=True)  # 只降不升：没赚到就用原件
        return src_ref, len(original_data), 1.0
    return out_ref, out_bytes, resize_factor


# ---------------------------------------------------------------- 矢量执行

def _execute_vector(
    ctx: PipelineContext, page: PageElements, page_number: int, vp: VectorPlan
) -> None:
    vector = page.vector_paths[vp.path_group_index]
    if vp.strategy == "simplify":
        primitives = pickle.loads(ctx.resolve_ref(vector.path_data_ref).read_bytes())
        simplified = [_simplify_drawing(d, vp.simplify_tolerance or 1.0) for d in primitives]
        out_ref = f"{VEC_SIMPLIFIED_DIR}/vec_{page_number:03d}_{vp.path_group_index:03d}.bin"
        ctx.resolve_ref(out_ref).write_bytes(
            pickle.dumps(simplified, protocol=pickle.HIGHEST_PROTOCOL)
        )
        return
    # rasterize：用 split 产物（单页 PDF）渲染 bbox 区域
    # V1 保真注意：clip 渲染包含该区域内所有内容层，Phase 10 组装时需按策略处理层级
    page_pdf = ctx.resolve_ref(f"pages/page_{page_number}.pdf")
    doc = pymupdf.open(str(page_pdf))
    try:
        clip = pymupdf.Rect(
            vector.bbox.x, vector.bbox.y,
            vector.bbox.x + vector.bbox.w, vector.bbox.y + vector.bbox.h,
        )
        zoom = (vp.rasterize_dpi or 96) / 72.0
        pix = doc[0].get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), clip=clip)
        out_ref = f"{VEC_RASTERIZED_DIR}/vec_{page_number:03d}_{vp.path_group_index:03d}.png"
        pix.save(str(ctx.resolve_ref(out_ref)))
    finally:
        doc.close()


def _simplify_drawing(drawing: dict, tolerance: float) -> dict:
    """对 drawing 内连续的直线链应用 Douglas-Peucker；曲线/矩形/四边形保持不动。"""
    items = drawing.get("items", [])
    out_items: list = []
    chain: list = []  # 连续 l 段拼成的折线点列

    def flush_chain() -> None:
        if len(chain) >= 3:
            reduced = _douglas_peucker(chain, tolerance)
            for a, b in zip(reduced, reduced[1:]):
                out_items.append(("l", [a, b]))
        else:
            for a, b in zip(chain, chain[1:]):
                out_items.append(("l", [a, b]))
        chain.clear()

    for op, pts in items:
        if op == "l" and len(pts) == 2:
            if chain and chain[-1] == pts[0]:
                chain.append(pts[1])
            else:
                flush_chain()
                chain.extend([pts[0], pts[1]])
        else:
            flush_chain()
            out_items.append((op, pts))
    flush_chain()

    result = dict(drawing)
    result["items"] = out_items
    return result


def _douglas_peucker(points: list, tolerance: float) -> list:
    """迭代版 Douglas-Peucker 折线简化（自实现，避免引入 shapely 依赖）。"""
    if len(points) < 3:
        return list(points)
    keep = [False] * len(points)
    keep[0] = keep[-1] = True
    stack = [(0, len(points) - 1)]
    while stack:
        start, end = stack.pop()
        ax, ay = points[start]
        bx, by = points[end]
        seg_dx, seg_dy = bx - ax, by - ay
        seg_len_sq = seg_dx * seg_dx + seg_dy * seg_dy
        max_dist = -1.0
        max_idx = -1
        for i in range(start + 1, end):
            px, py = points[i]
            if seg_len_sq == 0:
                dist_sq = (px - ax) ** 2 + (py - ay) ** 2
            else:
                t = ((px - ax) * seg_dx + (py - ay) * seg_dy) / seg_len_sq
                t = max(0.0, min(1.0, t))
                cx, cy = ax + t * seg_dx, ay + t * seg_dy
                dist_sq = (px - cx) ** 2 + (py - cy) ** 2
            if dist_sq > max_dist:
                max_dist, max_idx = dist_sq, i
        if max_idx >= 0 and max_dist > tolerance * tolerance:
            keep[max_idx] = True
            stack.append((start, max_idx))
            stack.append((max_idx, end))
    return [p for p, k in zip(points, keep) if k]
