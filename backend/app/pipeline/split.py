"""Phase 4（运行别名 Phase A）：PDF 拆页。

把上传的 PDF 拆解成独立的 RawPage 序列，每页原始字节流保存为
tmp_workspace/pages/page_{N}.pdf（N 从 1 开始，对齐 PDF 传统）。

API 风格约定（关注点 2）：全程使用 pymupdf 1.28 新式 API（`import pymupdf` /
`pymupdf.open()`），不使用旧式 `fitz` 别名，不混用。

日志约定（关注点 4）：logger 名固定为 "app.pipeline.split"，单页失败记
warning 后继续，Phase 11 按此名称统一收集。
"""

from __future__ import annotations

import logging

import pymupdf

from app.contracts import PhaseError, PipelineContext, RawPage

logger = logging.getLogger("app.pipeline.split")

PAGES_DIR = "pages"  # 相对 tmp_workspace 的 ref 前缀（《数据契约.md》§1.6）


def split_pdf(input_pdf_path: str, ctx: PipelineContext) -> list[RawPage]:
    """拆页主入口。

    错误语义（《系统架构设计.md》§5.1）：
    - 打不开 / 非 PDF / 零可读页 → PhaseError(code="INVALID_PDF", recoverable=False)
    - 加密 PDF → PhaseError(code="ENCRYPTED_PDF", recoverable=False)
    - 单页读取失败 → warning 并继续，返回数量可能少于原始页数
    """
    try:
        doc = pymupdf.open(input_pdf_path)
    except Exception as exc:
        raise PhaseError(
            phase="split",
            code="INVALID_PDF",
            message=f"cannot open as PDF: {exc}",
            recoverable=False,
        ) from exc

    try:
        # 加密检测（关注点 3）：needs_pass = 需要密码才能读内容；
        # is_encrypted = 当前仍处于加密锁定状态。两者任一为真即拒绝。
        if doc.needs_pass or doc.is_encrypted:
            raise PhaseError(
                phase="split",
                code="ENCRYPTED_PDF",
                message="encrypted PDF is not supported",
                recoverable=False,
            )
        if not doc.is_pdf:
            # pymupdf 也能打开图片/EPUB 等，此处只接受真 PDF
            raise PhaseError(
                phase="split",
                code="INVALID_PDF",
                message="input is not a PDF document",
                recoverable=False,
            )

        pages_root = ctx.resolve_ref(PAGES_DIR)
        pages_root.mkdir(parents=True, exist_ok=True)

        raw_pages: list[RawPage] = []
        for index in range(doc.page_count):
            page_number = index + 1
            try:
                page = doc[index]
                rect = page.rect
                ref = f"{PAGES_DIR}/page_{page_number}.pdf"
                out_path = ctx.resolve_ref(ref)

                single = pymupdf.open()
                try:
                    single.insert_pdf(doc, from_page=index, to_page=index)
                    single.save(str(out_path))
                finally:
                    single.close()

                page_bytes = out_path.stat().st_size
                logger.info(
                    "page %d saved: %s (%d bytes, %.1f x %.1f pt)",
                    page_number, ref, page_bytes, rect.width, rect.height,
                )
                raw_pages.append(
                    RawPage(
                        page_number=page_number,
                        page_width=rect.width,
                        page_height=rect.height,
                        page_bytes_ref=ref,
                    )
                )
            except Exception:
                logger.warning(
                    "page %d failed to split, skipping", page_number, exc_info=True
                )

        if not raw_pages:
            raise PhaseError(
                phase="split",
                code="INVALID_PDF",
                message="no readable pages in PDF",
                recoverable=False,
            )
        return raw_pages
    finally:
        doc.close()
