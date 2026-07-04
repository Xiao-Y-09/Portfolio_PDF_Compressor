"""Phase 3 契约测试：JSON round-trip、关键字段边界验证、Set 序列化、ref 解析安全。

全部使用 Pydantic v2 语法（model_dump / model_dump_json / model_validate /
model_validate_json）。
"""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel, ValidationError

from app.contracts import (
    Annotation,
    BBox,
    ClassifiedPage,
    CompressionPlan,
    CompressionResult,
    ConvergenceStep,
    FontUsage,
    ImageResult,
    PageElements,
    PageOverride,
    PagePlan,
    PerPageFixedBytes,
    PhaseError,
    PhaseErrorData,
    PipelineContext,
    PreprocessResult,
    RasterImage,
    RasterPlan,
    RawPage,
    SubsetResult,
    TextBlock,
    UserPreferences,
    VectorPath,
    VectorPlan,
    XObject,
)

# ---------------------------------------------------------------- 样本实例

BBOX = BBox(x=10.0, y=20.0, w=300.0, h=200.0)

SAMPLES: list[BaseModel] = [
    BBOX,
    RasterImage(
        data_ref="images/img_001_000.bin", width=3000, height=2000, dpi=300.0,
        color_space="RGB", has_alpha=False, original_bytes=5_000_000, bbox=BBOX,
    ),
    VectorPath(
        path_data_ref="vectors/vec_001_000.bin", control_point_count=12000,
        stroke_color="#000000", fill_color=None, original_bytes=400_000, bbox=BBOX,
    ),
    TextBlock(content="作品集 Portfolio", font_ref="NotoSansSC", font_size=12.0, bbox=BBOX),
    XObject(ref_id="xobj_7", xobject_type="form", bbox=BBOX),
    Annotation(anno_type="link", target="https://example.com", bbox=BBOX),
    RawPage(page_number=1, page_width=595.0, page_height=842.0,
            page_bytes_ref="pages/page_1.pdf"),
    PageElements(page_number=1, page_width=595.0, page_height=842.0),
    ClassifiedPage(
        page_number=1, page_type="photo_heavy", dominant_element="raster",
        raster_area_ratio=0.8, vector_area_ratio=0.1, text_area_ratio=0.05,
        complexity_score=0.2,
    ),
    FontUsage(font_name="NotoSansSC", data_ref="fonts/font_NotoSansSC.ttf",
              chars_used={"作", "品", "集", "P"}),
    SubsetResult(font_name="NotoSansSC", original_bytes=10_000_000,
                 subsetted_bytes=80_000, subsetted_data_ref="fonts_subsetted/font_NotoSansSC.ttf"),
    PageOverride(page_number=3, action="reclassify", new_type="chart"),
    UserPreferences(target_size_mb=10.0, compression_target="screen",
                    per_page_overrides=[PageOverride(page_number=2, action="skip")]),
    RasterPlan(
        image_index=0, area_ratio=0.75, original_dpi=300.0, original_bytes=5_000_000,
        target_dpi=150, quality=80, max_dimension=2000, output_format="jpeg",
        estimated_bytes=900_000,
    ),
    VectorPlan(path_group_index=0, area_ratio=0.5, original_bytes=400_000,
               strategy="simplify", simplify_tolerance=1.0),
    PagePlan(page_number=1, skip=False,
             raster_plans=[RasterPlan(
                 image_index=0, area_ratio=0.75, original_dpi=300.0, original_bytes=5_000_000,
                 target_dpi=150, quality=80, max_dimension=2000, output_format="jpeg",
                 estimated_bytes=900_000)],
             vector_plans=[VectorPlan(path_group_index=0, area_ratio=0.05,
                                      original_bytes=1000, strategy="keep")]),
    CompressionPlan(raster_budget=8_000_000,
                    pages=[PagePlan(page_number=1, skip=True)], round_number=1,
                    base_aggressiveness=0.5),
    ImageResult(page_number=1, image_index=0, compressed_bytes=850_000,
                compressed_data_ref="compressed/img_001_000.jpeg",
                actual_dpi=150.0, actual_quality=80),
    CompressionResult(total_raster_bytes=850_000,
                      per_image_results=[ImageResult(
                          page_number=1, image_index=0, compressed_bytes=850_000,
                          compressed_data_ref="compressed/img_001_000.jpeg",
                          actual_dpi=150.0, actual_quality=80)],
                      round_number=2, base_aggressiveness=0.58),
    ConvergenceStep(round_number=1, base_aggressiveness=0.5,
                    total_raster_bytes=9_000_000, total_bytes=10_500_000,
                    target_bytes=10_485_760, gap_ratio=0.0014),
    PerPageFixedBytes(page_number=1, vector_bytes=40_000, text_bytes=3_000,
                      annotation_bytes=500),
    PreprocessResult(
        subsetted_fonts=[SubsetResult(font_name="F", original_bytes=100, subsetted_bytes=50,
                                      subsetted_data_ref="fonts_subsetted/font_F.ttf")],
        total_fixed_bytes=1_500_000,
        per_page_fixed_bytes=[PerPageFixedBytes(page_number=1, vector_bytes=40_000,
                                                text_bytes=3_000, annotation_bytes=500)],
        stripped_metadata_fields=["author", "creator"],
    ),
    PipelineContext(file_id="123e4567-e89b-42d3-a456-426614174000", tmp_workspace="./tmp/ws"),
    PhaseErrorData(phase="split", code="INVALID_PDF", message="not a pdf", recoverable=False),
]


# ---------------------------------------------------------------- round-trip

@pytest.mark.parametrize("sample", SAMPLES, ids=lambda s: type(s).__name__)
def test_json_roundtrip(sample: BaseModel):
    """所有契约类型：model_dump_json → model_validate_json 恒等（关注点：v2 语法）。"""
    cls = type(sample)
    restored = cls.model_validate_json(sample.model_dump_json())
    assert restored == sample
    # python-dict 模式同样恒等
    assert cls.model_validate(sample.model_dump()) == sample


# ---------------------------------------------------------------- 关键字段边界

def _raster_plan_kwargs(**overrides):
    kwargs = dict(image_index=0, area_ratio=0.5, original_dpi=300.0, original_bytes=100,
                  target_dpi=150, quality=80, max_dimension=1000, output_format="jpeg",
                  estimated_bytes=100)
    kwargs.update(overrides)
    return kwargs


@pytest.mark.parametrize("bad", [{"quality": -1}, {"quality": 101}, {"target_dpi": 0},
                                 {"target_dpi": -10}, {"area_ratio": -0.1}, {"area_ratio": 1.5}])
def test_raster_plan_field_bounds(bad):
    with pytest.raises(ValidationError):
        RasterPlan(**_raster_plan_kwargs(**bad))


def test_classified_page_ratio_bounds():
    with pytest.raises(ValidationError):
        ClassifiedPage(page_number=1, page_type="mixed", dominant_element="text",
                       raster_area_ratio=0.1, vector_area_ratio=0.1,
                       text_area_ratio=0.1, complexity_score=1.2)


def test_page_number_starts_at_one():
    with pytest.raises(ValidationError):
        RawPage(page_number=0, page_width=595.0, page_height=842.0,
                page_bytes_ref="pages/page_0.pdf")


def test_literal_fields_reject_unknown_values():
    with pytest.raises(ValidationError):
        Annotation(anno_type="popup", target="x", bbox=BBOX)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        UserPreferences(target_size_mb=10.0, compression_target="fax")  # type: ignore[arg-type]


# ---------------------------------------------------------------- BBox 几何

def test_bbox_area_and_ratio():
    assert BBOX.area == 300.0 * 200.0
    page_area = 595.0 * 842.0
    assert BBOX.area_ratio(page_area) == pytest.approx(60_000 / page_area)


def test_bbox_area_ratio_clamped_and_zero_page():
    huge = BBox(x=0, y=0, w=10_000, h=10_000)
    assert huge.area_ratio(100.0) == 1.0     # 出血元素封顶
    assert BBOX.area_ratio(0.0) == 0.0       # 防御除零
    with pytest.raises(ValidationError):
        BBox(x=0, y=0, w=-1, h=10)           # 负尺寸拒绝


# ---------------------------------------------------------------- Set 序列化（关注点 5）

def test_fontusage_set_json_roundtrip():
    fu = FontUsage(font_name="NotoSansSC", data_ref="fonts/font_NotoSansSC.ttf",
                   chars_used={"审", "计", "a", "Z"})
    restored = FontUsage.model_validate_json(fu.model_dump_json())
    assert isinstance(restored.chars_used, set)
    assert restored.chars_used == fu.chars_used


def test_fontusage_serialization_is_deterministic_sorted():
    """chars_used 序列化为排序列表：相同字符集 → 字节级一致的 JSON。"""
    a = FontUsage(font_name="F", chars_used=set("dcba"))
    b = FontUsage(font_name="F", chars_used=set("abdc"))
    assert a.model_dump_json() == b.model_dump_json()
    payload = json.loads(a.model_dump_json())
    assert payload["chars_used"] == ["a", "b", "c", "d"]


def test_fontusage_defaults():
    fu = FontUsage(font_name="Broken")
    assert fu.data_ref is None       # 字体提取失败场景（Phase 7 跳过子集化）
    assert fu.chars_used == set()


# ---------------------------------------------------------------- resolve_ref 安全

def test_resolve_ref_inside_workspace(tmp_path):
    ctx = PipelineContext(file_id="fid", tmp_workspace=str(tmp_path))
    resolved = ctx.resolve_ref("images/img_001_000.bin")
    assert resolved == (tmp_path / "images" / "img_001_000.bin").resolve()


@pytest.mark.parametrize("bad_ref", [
    "", "../outside.bin", "a/../../outside.bin", "/etc/passwd",
    "C:/Windows/system32", "c:evil", "images\\img.bin",
])
def test_resolve_ref_rejects_traversal(tmp_path, bad_ref):
    ctx = PipelineContext(file_id="fid", tmp_workspace=str(tmp_path))
    with pytest.raises(ValueError):
        ctx.resolve_ref(bad_ref)


# ---------------------------------------------------------------- PhaseError 双层

def test_phase_error_is_raisable_and_dumps():
    with pytest.raises(PhaseError) as excinfo:
        raise PhaseError(phase="decide", code="TARGET_TOO_SMALL",
                         message="fixed overhead exceeds target", recoverable=False)
    err = excinfo.value
    assert err.phase == "decide" and err.code == "TARGET_TOO_SMALL"
    dumped = err.model_dump()  # 手册 Phase 11 用法：meta={"error": e.model_dump()}
    assert dumped == {"phase": "decide", "code": "TARGET_TOO_SMALL",
                      "message": "fixed overhead exceeds target", "recoverable": False}
    # 数据契约可独立 round-trip
    assert PhaseErrorData.model_validate(dumped) == err.data


# ---------------------------------------------------------------- 修订字段（2026-07-04 四补丁）

def test_base_aggressiveness_bounds():
    with pytest.raises(ValidationError):
        CompressionPlan(raster_budget=1, round_number=1, base_aggressiveness=1.5)
    with pytest.raises(ValidationError):
        CompressionResult(total_raster_bytes=0, round_number=1, base_aggressiveness=-0.1)


def test_convergence_history_default_empty_and_appendable():
    ctx = PipelineContext(file_id="fid", tmp_workspace="./ws")
    assert ctx.convergence_history == []
    ctx.convergence_history.append(ConvergenceStep(
        round_number=1, base_aggressiveness=0.5, total_raster_bytes=100,
        total_bytes=150, target_bytes=140, gap_ratio=(150 - 140) / 140))
    restored = PipelineContext.model_validate_json(ctx.model_dump_json())
    assert len(restored.convergence_history) == 1
    assert restored.convergence_history[0].gap_ratio == pytest.approx(10 / 140)


def test_gap_ratio_allows_negative():
    step = ConvergenceStep(round_number=2, base_aggressiveness=0.6,
                           total_raster_bytes=50, total_bytes=80,
                           target_bytes=100, gap_ratio=-0.2)  # 压过头（浪费）合法
    assert step.gap_ratio == -0.2


# ---------------------------------------------------------------- 跨 Phase 装配

def test_phase_outputs_compose_without_conversion():
    """Phase N 输出可直接作为 Phase N+1 输入字段（无需任何转换层）。

    含 2026-07-04 修订字段的完整通路：original_bytes 从 RasterImage 流入
    RasterPlan；base_aggressiveness 从 Plan 回带到 Result（反馈环闭合）。
    """
    img = RasterImage(data_ref="images/img_001_000.bin", width=3000, height=2000,
                      dpi=300.0, color_space="RGB", has_alpha=True,
                      original_bytes=1_000_000, bbox=BBOX)
    page = PageElements(page_number=1, page_width=595.0, page_height=842.0,
                        raster_images=[img])
    plan = CompressionPlan(
        raster_budget=8_000_000, round_number=1, base_aggressiveness=0.5,
        pages=[PagePlan(page_number=page.page_number, raster_plans=[RasterPlan(
            image_index=0,
            area_ratio=img.bbox.area_ratio(page.page_width * page.page_height),
            original_dpi=img.dpi, original_bytes=img.original_bytes, target_dpi=150,
            quality=80, max_dimension=2000,
            output_format="png" if img.has_alpha else "jpeg",
            estimated_bytes=500_000)])],
    )
    assert plan.pages[0].raster_plans[0].output_format == "png"
    assert plan.pages[0].raster_plans[0].original_bytes == img.original_bytes
    result = CompressionResult(
        total_raster_bytes=500_000, round_number=plan.round_number,
        base_aggressiveness=plan.base_aggressiveness,  # Phase 9 原样回带
        per_image_results=[ImageResult(
            page_number=page.page_number,
            image_index=plan.pages[0].raster_plans[0].image_index,
            compressed_bytes=500_000, compressed_data_ref="compressed/img_001_000.png",
            actual_dpi=150.0, actual_quality=80)],
    )
    assert result.per_image_results[0].image_index == 0
    assert result.base_aggressiveness == plan.base_aggressiveness
