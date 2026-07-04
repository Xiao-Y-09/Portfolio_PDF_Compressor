"""Phase 7（运行别名 Phase D0）：确定性预处理——字体子集化 + 固定开销计算。

架构原则："把确定性的操作前置，把不确定性留给循环"。字体子集化结果完全确定
（同字体 + 同字符集 → 输出永远一样），前置后 Phase 8 的位图预算是精确数字
`target - total_fixed_bytes`，二分搜索空间从"整个 PDF"缩小到"位图部分"。

固定开销是估算模型（手册 Phase 7 Prompt 规定），误差 ±10% 由收敛循环兜住；
估算 vs 实际 的验证需要 Phase 10 产出真实 PDF 后才有 ground truth（见测试中的
deferred 标记）。

日志：logger 名固定 "app.pipeline.preprocess"。
"""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Optional

from fontTools import subset
from fontTools.ttLib import TTFont

from app.contracts import (
    FontUsage,
    PageElements,
    PerPageFixedBytes,
    PipelineContext,
    PreprocessResult,
    SubsetResult,
    UserPreferences,
)

logger = logging.getLogger("app.pipeline.preprocess")

SUBSET_DIR = "fonts_subsetted"

# ---- 固定开销估算模型常量（手册 Phase 7 Prompt 给定；粗估，收敛循环兜底）----
TEXT_STRUCTURE_FACTOR = 1.5        # 文字 UTF-8 字节 × 1.5（含字体引用等结构开销）
ANNOTATION_BYTES_ESTIMATE = 300    # 每个注释 100-500 字节，取中值
METADATA_BYTES_ESTIMATE = 1000     # 清理后元数据 500-2000 字节，取中值
PDF_PAGE_STRUCTURE_BYTES = 200     # 每页 PDF 结构开销
PDF_BASE_STRUCTURE_BYTES = 500     # 文档级 PDF 结构开销

# 元数据清理策略：默认清理 author/creator（隐私考虑），保留 title
DEFAULT_STRIP_FIELDS = ("author", "creator")

_SAFE_NAME = re.compile(r"[^A-Za-z0-9_-]+")


def preprocess(
    pages: List[PageElements],
    fonts: Dict[str, FontUsage],
    metadata: Dict[str, Any],
    user_prefs: UserPreferences,
    ctx: PipelineContext,
) -> PreprocessResult:
    ctx.resolve_ref(SUBSET_DIR).mkdir(parents=True, exist_ok=True)

    # 一、字体子集化（确定性操作）
    subsetted_fonts: List[SubsetResult] = []
    for font in fonts.values():
        result = _subset_font(font, ctx)
        if result is not None:
            subsetted_fonts.append(result)

    # 二、元数据清理（记录清理了哪些字段；实际改写在 Phase 10 组装时执行）
    stripped = [f for f in DEFAULT_STRIP_FIELDS if metadata.get(f)]

    # 三、固定开销计算
    per_page = [_page_fixed_bytes(p) for p in pages]
    total_fixed_bytes = (
        sum(pp.vector_bytes + pp.text_bytes + pp.annotation_bytes for pp in per_page)
        + sum(s.subsetted_bytes for s in subsetted_fonts)
        + METADATA_BYTES_ESTIMATE
        + len(pages) * PDF_PAGE_STRUCTURE_BYTES
        + PDF_BASE_STRUCTURE_BYTES
    )

    logger.info(
        "preprocess: %d fonts subsetted (%.1f -> %.1f KB), total_fixed_bytes=%.1f KB",
        len(subsetted_fonts),
        sum(s.original_bytes for s in subsetted_fonts) / 1024,
        sum(s.subsetted_bytes for s in subsetted_fonts) / 1024,
        total_fixed_bytes / 1024,
    )
    return PreprocessResult(
        subsetted_fonts=subsetted_fonts,
        total_fixed_bytes=total_fixed_bytes,
        per_page_fixed_bytes=per_page,
        stripped_metadata_fields=stripped,
    )


# ---------------------------------------------------------------- 字体子集化

def _subset_font(font: FontUsage, ctx: PipelineContext) -> Optional[SubsetResult]:
    """单个字体子集化。

    分支语义（手册 Phase 7 + 关注点 3）：
    - data_ref=None（Phase 5 提取失败/非嵌入字体）→ 跳过子集化，返回 None
      （无字节可算，不参与固定开销；输出 PDF 中该字体维持名称引用）
    - chars_used 为空 → 跳过整个字体（可能是仅装饰性引用，Phase 10 不会嵌入）
    - 加载/子集化失败 → 保留原字体：subsetted_bytes = original_bytes，
      subsetted_data_ref 指向原文件
    - 只降不升：子集化结果比原文件还大 → 保留原字体
    """
    if font.data_ref is None:
        logger.warning("font %s: data_ref=None (not embedded), skip subsetting",
                       font.font_name)
        return None

    src = ctx.resolve_ref(font.data_ref)
    if not src.is_file():
        logger.warning("font %s: data_ref file missing, skip", font.font_name)
        return None
    original_bytes = src.stat().st_size

    if not font.chars_used:
        logger.info("font %s: empty charset, skip whole font (decorative reference)",
                    font.font_name)
        return None

    # 可疑字符集门禁（2026-07-04，原地手术 QC 发现）：无 ToUnicode 的字体提取出的
    # chars_used 是私有区/乱码码点，按它子集化会保留错误的 cmap 条目——原地替换
    # 字体流后文字层被污染（简单 TrueType 还有视觉风险）。此类字体放弃子集化，
    # 保留原字体（体积损失几百 KB 级，正确性优先）。
    if any(0xE000 <= ord(c) <= 0xF8FF or ord(c) < 0x20 for c in font.chars_used):
        logger.warning(
            "font %s: charset contains PUA/control codepoints (likely no ToUnicode), "
            "skipping subsetting to protect text integrity", font.font_name)
        return SubsetResult(
            font_name=font.font_name,
            original_bytes=original_bytes,
            subsetted_bytes=original_bytes,
            subsetted_data_ref=font.data_ref,
        )

    try:
        tt = TTFont(str(src))
        options = subset.Options()
        options.notdef_outline = True  # 保留 .notdef 轮廓，缺字时不至于渲染空白
        # 原地手术主干（2026-07-04 架构决策 A）必需：保持字形 ID 不变。
        # 子集化产物将原地替换 PDF 里的 FontFile2 流，PDF 文本以 GID 寻址字形
        # （CID 字体 CIDToGIDMap），GID 重排会导致视觉乱码；retain_gids 只清空
        # 未用字形的轮廓、不重新编号，体积收益仍在（轮廓是大头）。
        options.retain_gids = True
        subsetter = subset.Subsetter(options)
        subsetter.populate(text="".join(sorted(font.chars_used)))
        subsetter.subset(tt)

        safe = _SAFE_NAME.sub("_", font.font_name)[:80] or "font"
        out_ref = f"{SUBSET_DIR}/font_{safe}.ttf"
        out_path = ctx.resolve_ref(out_ref)
        tt.save(str(out_path))
        subsetted_bytes = out_path.stat().st_size

        if subsetted_bytes >= original_bytes:
            # 只降不升：子集化没赚到（小字符集拉丁字体常见），保留原字体
            out_path.unlink(missing_ok=True)
            logger.info("font %s: subset not smaller (%d >= %d), keeping original",
                        font.font_name, subsetted_bytes, original_bytes)
            return SubsetResult(
                font_name=font.font_name,
                original_bytes=original_bytes,
                subsetted_bytes=original_bytes,
                subsetted_data_ref=font.data_ref,
            )
        return SubsetResult(
            font_name=font.font_name,
            original_bytes=original_bytes,
            subsetted_bytes=subsetted_bytes,
            subsetted_data_ref=out_ref,
        )
    except Exception:
        logger.warning("font %s: subsetting failed, keeping original",
                       font.font_name, exc_info=True)
        return SubsetResult(
            font_name=font.font_name,
            original_bytes=original_bytes,
            subsetted_bytes=original_bytes,
            subsetted_data_ref=font.data_ref,
        )


# ---------------------------------------------------------------- 固定开销

def _page_fixed_bytes(page: PageElements) -> PerPageFixedBytes:
    vector_bytes = sum(v.original_bytes for v in page.vector_paths)
    text_bytes = int(
        sum(len(t.content.encode("utf-8")) for t in page.text_blocks)
        * TEXT_STRUCTURE_FACTOR
    )
    annotation_bytes = len(page.annotations) * ANNOTATION_BYTES_ESTIMATE
    return PerPageFixedBytes(
        page_number=page.page_number,
        vector_bytes=vector_bytes,
        text_bytes=text_bytes,
        annotation_bytes=annotation_bytes,
    )
