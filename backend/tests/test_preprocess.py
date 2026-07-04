"""Phase 7 预处理测试：字体子集化分支全覆盖 + 固定开销总和恒等式。

注意：total_fixed_bytes 的"估算 vs 实际 ±10%"验证需要 Phase 10 产出真实 PDF
才有 ground truth，见文末 deferred 测试标记。
"""

from __future__ import annotations

import uuid
from pathlib import Path

import pytest

from app.contracts import FontUsage, PipelineContext, UserPreferences
from app.pipeline.preprocess import (
    ANNOTATION_BYTES_ESTIMATE,
    METADATA_BYTES_ESTIMATE,
    PDF_BASE_STRUCTURE_BYTES,
    PDF_PAGE_STRUCTURE_BYTES,
    preprocess,
)

PORTFOLIOS = Path(__file__).parent / "fixtures" / "portfolios"
PREFS = UserPreferences(target_size_mb=10.0, compression_target="screen")


def _new_ctx(base: Path) -> PipelineContext:
    ws = base / "ws"
    ws.mkdir(parents=True, exist_ok=True)
    return PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))


# ---------------------------------------------------------------- 真实样本（portfolio_2：24 字体）

@pytest.fixture(scope="module")
def real_preprocessed(tmp_path_factory):
    from app.pipeline.extract import extract_elements
    from app.pipeline.split import split_pdf

    src = PORTFOLIOS / "portfolio_2.pdf"
    assert src.exists(), "缺少真实样本 portfolio_2.pdf（规则 6）"
    ctx = _new_ctx(tmp_path_factory.mktemp("pre_p2"))
    raw = split_pdf(str(src), ctx)
    pages, fonts, meta = extract_elements(raw, str(src), ctx)
    result = preprocess(pages, fonts, meta, PREFS, ctx)
    return {"ctx": ctx, "pages": pages, "fonts": fonts, "meta": meta, "result": result}


def test_subsetting_reduces_font_bytes_on_real_sample(real_preprocessed):
    """实测锚点（2026-07-04）：TrueType 字体子集化显著缩减；全体不升。"""
    subs = real_preprocessed["result"].subsetted_fonts
    assert len(subs) >= 20  # 24 个嵌入字体，至少 20 个产出 SubsetResult

    total_original = sum(s.original_bytes for s in subs)
    total_subsetted = sum(s.subsetted_bytes for s in subs)
    assert total_subsetted < total_original  # 总量严格缩减（实测 -55.6%）
    # 至少一个字体缩减 >70%（实测 Helvetica-Bold -87.5%）
    assert any(s.subsetted_bytes < s.original_bytes * 0.3 for s in subs)
    # 只降不升：任何字体不得变大
    for s in subs:
        assert s.subsetted_bytes <= s.original_bytes


def test_subsetted_files_exist(real_preprocessed):
    ctx = real_preprocessed["ctx"]
    for s in real_preprocessed["result"].subsetted_fonts:
        f = ctx.resolve_ref(s.subsetted_data_ref)
        assert f.is_file() and f.stat().st_size > 0
        if s.subsetted_bytes < s.original_bytes:
            # 真正子集化成功的，产物必须在 fonts_subsetted/ 下
            assert s.subsetted_data_ref.startswith("fonts_subsetted/")


def test_failed_fonts_keep_original_ref(real_preprocessed):
    """容错分支（实测触发 8 个：6 缺 cmap + 2 裸 CFF）：保留原字体引用。"""
    kept = [s for s in real_preprocessed["result"].subsetted_fonts
            if s.subsetted_bytes == s.original_bytes]
    assert kept, "实测样本应存在子集化失败/不赚的字体"
    for s in kept:
        assert s.subsetted_data_ref.startswith("fonts/")  # 指回原文件


def test_total_fixed_bytes_sum_identity(real_preprocessed):
    """关注点 4（可当下验证的部分）：total = 各分量之和，恒等式必须成立。"""
    r = real_preprocessed["result"]
    expected = (
        sum(pp.vector_bytes + pp.text_bytes + pp.annotation_bytes
            for pp in r.per_page_fixed_bytes)
        + sum(s.subsetted_bytes for s in r.subsetted_fonts)
        + METADATA_BYTES_ESTIMATE
        + len(real_preprocessed["pages"]) * PDF_PAGE_STRUCTURE_BYTES
        + PDF_BASE_STRUCTURE_BYTES
    )
    assert r.total_fixed_bytes == expected
    assert len(r.per_page_fixed_bytes) == len(real_preprocessed["pages"])


def test_metadata_strip_only_present_fields(real_preprocessed):
    stripped = real_preprocessed["result"].stripped_metadata_fields
    meta = real_preprocessed["meta"]
    for field in stripped:
        assert field in ("author", "creator")
        assert meta.get(field)  # 只清理确实存在的字段
    assert "title" not in stripped  # title 永远保留


# ---------------------------------------------------------------- 分支覆盖（合成契约对象）

def test_font_with_none_data_ref_is_skipped(tmp_path):
    """关注点 3：data_ref=None（Phase 5 提取失败兜底）→ 跳过子集化，不崩溃。"""
    ctx = _new_ctx(tmp_path)
    fonts = {"Ghost": FontUsage(font_name="Ghost", data_ref=None, chars_used={"a", "b"})}
    result = preprocess([], fonts, {}, PREFS, ctx)
    assert result.subsetted_fonts == []  # 无字节可算，不产出条目、不计入固定开销


def test_font_with_empty_charset_is_skipped(tmp_path):
    ctx = _new_ctx(tmp_path)
    ref = "fonts/font_Unused.ttf"
    ctx.resolve_ref("fonts").mkdir(parents=True)
    ctx.resolve_ref(ref).write_bytes(b"\x00\x01\x00\x00" + b"x" * 100)
    fonts = {"Unused": FontUsage(font_name="Unused", data_ref=ref, chars_used=set())}
    result = preprocess([], fonts, {}, PREFS, ctx)
    assert result.subsetted_fonts == []  # 字符集为空 → 跳过整个字体


def test_corrupt_font_falls_back_to_original(tmp_path):
    ctx = _new_ctx(tmp_path)
    ref = "fonts/font_Broken.ttf"
    ctx.resolve_ref("fonts").mkdir(parents=True)
    ctx.resolve_ref(ref).write_bytes(b"this is not a font at all")
    fonts = {"Broken": FontUsage(font_name="Broken", data_ref=ref, chars_used={"a"})}
    result = preprocess([], fonts, {}, PREFS, ctx)
    assert len(result.subsetted_fonts) == 1
    s = result.subsetted_fonts[0]
    assert s.subsetted_bytes == s.original_bytes  # 保留原字体
    assert s.subsetted_data_ref == ref


def test_annotation_bytes_use_estimate_constant(tmp_path):
    from app.contracts import Annotation, BBox, PageElements

    ctx = _new_ctx(tmp_path)
    page = PageElements(
        page_number=1, page_width=595.0, page_height=842.0,
        annotations=[Annotation(anno_type="link", target="https://x",
                                bbox=BBox(x=0, y=0, w=10, h=10))] * 3)
    result = preprocess([page], {}, {}, PREFS, ctx)
    assert result.per_page_fixed_bytes[0].annotation_bytes == 3 * ANNOTATION_BYTES_ESTIMATE


# ---------------------------------------------------------------- 延后验证

@pytest.mark.skip(reason="估算 vs 实际 ±10% 需要 Phase 10 assemble 产出真实 PDF "
                         "作为 ground truth；Phase 10 首次产出真实 PDF 时校准（含 "
                         "vector_bytes_per_control_point=3.0 系数的实测标定），Phase 13 端到端复验")
def test_fixed_bytes_estimate_within_10_percent_of_actual():
    pass
