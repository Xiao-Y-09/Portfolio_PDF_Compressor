"""Phase 9 压缩执行测试：去重压缩、最保守参数、只降不升、容错率、矢量执行、
以及 decide↔compress 双轮握手（关注点 3）。

图像素材：PIL 生成的真实 JPEG/PNG 字节（种子固定的噪声图，不可压缩性接近照片，
缩放收益可预期），非 mock。
"""

from __future__ import annotations

import io
import pickle
import random
import uuid
from pathlib import Path

import pymupdf
import pytest
from PIL import Image

from app.config.settings import CompressionConfig
from app.contracts import (
    BBox,
    CompressionPlan,
    PageElements,
    PagePlan,
    PerPageFixedBytes,
    PhaseError,
    PipelineContext,
    PreprocessResult,
    RasterImage,
    RasterPlan,
    UserPreferences,
    VectorPath,
    VectorPlan,
)
from app.pipeline.compress import execute_compression
from app.pipeline.decide import decide_compression

CFG = CompressionConfig()
PAGE_W, PAGE_H = 595.0, 842.0


def _new_ctx(base: Path) -> PipelineContext:
    ws = base / "ws"
    for sub in ("images", "vectors", "pages"):
        (ws / sub).mkdir(parents=True, exist_ok=True)
    return PipelineContext(file_id=str(uuid.uuid4()), tmp_workspace=str(ws))


def make_noise_jpeg_bytes(size=(800, 600), q=90, seed=42) -> bytes:
    rnd = random.Random(seed)
    im = Image.new("RGB", size)
    im.putdata([(rnd.randrange(256), rnd.randrange(256), rnd.randrange(256))
                for _ in range(size[0] * size[1])])
    buf = io.BytesIO()
    im.save(buf, "JPEG", quality=q)
    return buf.getvalue()


def put_image(ctx, ref: str, data: bytes) -> int:
    ctx.resolve_ref(ref).write_bytes(data)
    return len(data)


def make_raster_image(ref: str, nbytes: int, bbox_w: float = 200.0,
                      width: int = 800, height: int = 600,
                      alpha_type: str = "none") -> RasterImage:
    bbox = BBox(x=0, y=0, w=bbox_w, h=bbox_w * height / width)
    return RasterImage(
        data_ref=ref, width=width, height=height, dpi=width / (bbox_w / 72.0),
        color_space="RGB", has_alpha=(alpha_type == "translucent"),
        alpha_type=alpha_type, original_bytes=nbytes, bbox=bbox,
    )


def make_plan(image_index: int, quality: int, max_dimension: int,
              fmt: str = "jpeg", dpi: float = 288.0) -> RasterPlan:
    return RasterPlan(image_index=image_index, area_ratio=0.3, original_dpi=dpi,
                      original_bytes=100_000, target_dpi=100, quality=quality,
                      max_dimension=max_dimension, output_format=fmt,
                      estimated_bytes=50_000)


def wrap_plan(page_plans) -> CompressionPlan:
    return CompressionPlan(raster_budget=10_000_000, pages=page_plans,
                           round_number=1, base_aggressiveness=0.5)


# ---------------------------------------------------------------- 去重 + 最保守参数

def test_dedup_shared_ref_compressed_once_with_conservative_params(tmp_path):
    ctx = _new_ctx(tmp_path)
    nbytes = put_image(ctx, "images/img_001_000.bin", make_noise_jpeg_bytes())
    page = PageElements(page_number=1, page_width=PAGE_W, page_height=PAGE_H,
                        raster_images=[
                            make_raster_image("images/img_001_000.bin", nbytes, 200.0),
                            make_raster_image("images/img_001_000.bin", nbytes, 100.0)])
    plan = wrap_plan([PagePlan(page_number=1, raster_plans=[
        make_plan(0, quality=60, max_dimension=300),
        make_plan(1, quality=85, max_dimension=450)])])

    result = execute_compression(plan, [page], ctx, CFG)

    assert len(result.per_image_results) == 2
    refs = {r.compressed_data_ref for r in result.per_image_results}
    assert len(refs) == 1  # 共享同一压缩流
    out_ref = refs.pop()
    assert out_ref.startswith("compressed/") and out_ref.endswith(".jpg")
    # 最保守参数：quality 取 max(60,85)=85；尺寸上限取 max(300,450)=450
    assert all(r.actual_quality == 85 for r in result.per_image_results)
    with Image.open(ctx.resolve_ref(out_ref)) as im:
        assert max(im.size) == 450
    # total 只计一次
    single = result.per_image_results[0].compressed_bytes
    assert result.total_raster_bytes == single
    assert single < nbytes  # 确实变小
    # base_aggressiveness / round 原样回带
    assert result.base_aggressiveness == plan.base_aggressiveness
    assert result.round_number == plan.round_number


def test_only_shrink_falls_back_to_original(tmp_path):
    """源文件用与压缩器完全相同的参数预先编码 → 重编码字节数必然相等（≥）→ 回退。"""
    ctx = _new_ctx(tmp_path)
    src = io.BytesIO()
    noise = Image.new("RGB", (100, 75))
    rnd = random.Random(9)
    noise.putdata([(rnd.randrange(256),) * 3 for _ in range(100 * 75)])
    noise.save(src, "JPEG", quality=95, optimize=True)  # 与执行器同参数
    nbytes = put_image(ctx, "images/img_001_000.bin", src.getvalue())
    page = PageElements(page_number=1, page_width=PAGE_W, page_height=PAGE_H,
                        raster_images=[make_raster_image(
                            "images/img_001_000.bin", nbytes, 25.0, width=100, height=75)])
    plan = wrap_plan([PagePlan(page_number=1, raster_plans=[
        make_plan(0, quality=95, max_dimension=100)])])  # 不缩放、同 quality

    result = execute_compression(plan, [page], ctx, CFG)
    r = result.per_image_results[0]
    assert r.compressed_data_ref == "images/img_001_000.bin"  # 引用原件
    assert r.compressed_bytes == nbytes                        # 只降不升


def test_translucent_encoded_as_png(tmp_path):
    ctx = _new_ctx(tmp_path)
    rgba = Image.new("RGBA", (400, 300))
    rgba.putdata([(255, 0, 0, (x % 400) * 255 // 399) for x in range(400 * 300)])
    buf = io.BytesIO(); rgba.save(buf, "PNG")
    nbytes = put_image(ctx, "images/img_001_000.bin", buf.getvalue())
    page = PageElements(page_number=1, page_width=PAGE_W, page_height=PAGE_H,
                        raster_images=[make_raster_image(
                            "images/img_001_000.bin", nbytes, 200.0,
                            width=400, height=300, alpha_type="translucent")])
    plan = wrap_plan([PagePlan(page_number=1, raster_plans=[
        make_plan(0, quality=80, max_dimension=200, fmt="png")])])

    result = execute_compression(plan, [page], ctx, CFG)
    out_ref = result.per_image_results[0].compressed_data_ref
    assert out_ref.endswith(".png")
    with Image.open(ctx.resolve_ref(out_ref)) as im:
        assert im.mode == "RGBA"  # 透明通道保留


def test_grayscale_encoded_single_channel_visually_equal(tmp_path):
    """质量优化 2：灰度图输出 L 模式（1 通道），且像素级视觉无差别。"""
    ctx = _new_ctx(tmp_path)
    rnd = random.Random(31)
    gray = Image.new("RGB", (400, 300))
    gray.putdata([(v, v, v) for v in (rnd.randrange(256) for _ in range(400 * 300))])
    buf = io.BytesIO(); gray.save(buf, "JPEG", quality=90)
    nbytes = put_image(ctx, "images/img_001_000.bin", buf.getvalue())

    img = make_raster_image("images/img_001_000.bin", nbytes, 200.0,
                            width=400, height=300)
    img = img.model_copy(update={"is_grayscale": True})
    page = PageElements(page_number=1, page_width=PAGE_W, page_height=PAGE_H,
                        raster_images=[img])
    plan = wrap_plan([PagePlan(page_number=1, raster_plans=[
        make_plan(0, quality=80, max_dimension=400)])])

    result = execute_compression(plan, [page], ctx, CFG)
    out_ref = result.per_image_results[0].compressed_data_ref
    with Image.open(ctx.resolve_ref(out_ref)) as out_im:
        assert out_im.mode == "L"  # 3→1 通道
        src_gray = gray.convert("L")
        diff = sum(abs(a - b) for a, b in
                   zip(src_gray.getdata(), out_im.getdata())) / (400 * 300)
        # 阈值按噪声夹具校准：随机噪声是 JPEG 最坏输入，双重编码实测 ~7.5/255；
        # 真实灰度线稿/照片的差异远低于此
        assert diff < 10.0


# ---------------------------------------------------------------- 容错

def _five_image_page(ctx, corrupt_indices=()) -> PageElements:
    images = []
    for i in range(5):
        ref = f"images/img_001_{i:03d}.bin"
        data = b"corrupt" if i in corrupt_indices else make_noise_jpeg_bytes(
            size=(400, 300), seed=i)
        images.append(make_raster_image(ref, put_image(ctx, ref, data), 150.0,
                                        width=400, height=300))
    return PageElements(page_number=1, page_width=PAGE_W, page_height=PAGE_H,
                        raster_images=images)


def test_single_failure_uses_original_and_continues(tmp_path, caplog):
    import logging
    ctx = _new_ctx(tmp_path)
    page = _five_image_page(ctx, corrupt_indices={2})
    plan = wrap_plan([PagePlan(page_number=1, raster_plans=[
        make_plan(i, quality=70, max_dimension=200) for i in range(5)])])
    with caplog.at_level(logging.WARNING, logger="app.pipeline.compress"):
        result = execute_compression(plan, [page], ctx, CFG)  # 4/5=80% 不触发熔断
    bad = [r for r in result.per_image_results if r.image_index == 2][0]
    assert bad.compressed_data_ref == "images/img_001_002.bin"  # 原图凑数
    assert any("using original" in rec.getMessage() for rec in caplog.records)


def test_failure_rate_below_threshold_raises(tmp_path):
    ctx = _new_ctx(tmp_path)
    page = _five_image_page(ctx, corrupt_indices={0, 1, 2, 3, 4})
    plan = wrap_plan([PagePlan(page_number=1, raster_plans=[
        make_plan(i, quality=70, max_dimension=200) for i in range(5)])])
    with pytest.raises(PhaseError) as excinfo:
        execute_compression(plan, [page], ctx, CFG)
    assert excinfo.value.code == "EXECUTION_FAILURE_RATE"


def test_skip_page_untouched(tmp_path):
    ctx = _new_ctx(tmp_path)
    page = _five_image_page(ctx)
    plan = wrap_plan([PagePlan(page_number=1, skip=True)])
    result = execute_compression(plan, [page], ctx, CFG)
    assert result.per_image_results == []
    assert result.total_raster_bytes == 0
    assert not list(ctx.resolve_ref("compressed").iterdir())  # 未产生任何文件


# ---------------------------------------------------------------- 矢量执行

def _line_chain(n: int, jitter: float = 0.05):
    rnd = random.Random(7)
    pts = [(float(i), rnd.uniform(-jitter, jitter)) for i in range(n)]
    return [("l", [pts[i], pts[i + 1]]) for i in range(n - 1)]


def test_vector_simplify_reduces_points(tmp_path):
    ctx = _new_ctx(tmp_path)
    drawing = {"items": _line_chain(1000), "rect": (0, 0, 999, 1),
               "color": (0, 0, 0), "fill": None, "width": 1.0,
               "even_odd": None, "close_path": False}
    payload = pickle.dumps([drawing])
    ctx.resolve_ref("vectors/vec_001_000.bin").write_bytes(payload)
    page = PageElements(page_number=1, page_width=PAGE_W, page_height=PAGE_H,
                        vector_paths=[VectorPath(
                            path_data_ref="vectors/vec_001_000.bin",
                            control_point_count=1000, original_bytes=3000,
                            bbox=BBox(x=0, y=0, w=400, h=300))])
    plan = wrap_plan([PagePlan(page_number=1, vector_plans=[VectorPlan(
        path_group_index=0, area_ratio=0.4, original_bytes=3000,
        strategy="simplify", simplify_tolerance=1.0)])])

    execute_compression(plan, [page], ctx, CFG)

    out = ctx.resolve_ref("vectors_simplified/vec_001_000.bin")
    assert out.is_file()
    simplified = pickle.loads(out.read_bytes())
    assert len(simplified[0]["items"]) < 1000 * 0.2  # 近直线抖动链大幅简化
    assert out.stat().st_size < len(payload)


def test_vector_rasterize_renders_png(tmp_path):
    ctx = _new_ctx(tmp_path)
    doc = pymupdf.open()
    doc.new_page(width=PAGE_W, height=PAGE_H)
    pg = doc[0]
    pg.draw_rect(pymupdf.Rect(50, 50, 400, 300), color=(0, 0, 1), width=3)
    doc.save(str(ctx.resolve_ref("pages/page_1.pdf")))
    doc.close()

    page = PageElements(page_number=1, page_width=PAGE_W, page_height=PAGE_H,
                        vector_paths=[VectorPath(
                            path_data_ref="vectors/vec_001_000.bin",
                            control_point_count=200_000, original_bytes=600_000,
                            bbox=BBox(x=50, y=50, w=350, h=250))])
    plan = wrap_plan([PagePlan(page_number=1, vector_plans=[VectorPlan(
        path_group_index=0, area_ratio=0.35, original_bytes=600_000,
        strategy="rasterize", rasterize_dpi=96)])])

    execute_compression(plan, [page], ctx, CFG)

    out = ctx.resolve_ref("vectors_rasterized/vec_001_000.png")
    assert out.is_file() and out.stat().st_size > 0
    with Image.open(out) as im:
        assert im.width == pytest.approx(350 / 72 * 96, abs=2)  # bbox × dpi


# ---------------------------------------------------------------- 双轮握手（关注点 3）

def test_two_round_decide_compress_decide_handshake(tmp_path):
    """CompressionResult 无需任何转换即可作为下一轮 decide 的 previous_result。"""
    ctx = _new_ctx(tmp_path)
    images = []
    for i in range(2):
        ref = f"images/img_001_{i:03d}.bin"
        nbytes = put_image(ctx, ref, make_noise_jpeg_bytes(size=(800, 600), seed=i))
        images.append(make_raster_image(ref, nbytes, 250.0))
    pages = [PageElements(page_number=1, page_width=PAGE_W, page_height=PAGE_H,
                          raster_images=images)]
    pre = PreprocessResult(
        subsetted_fonts=[], total_fixed_bytes=50_000,
        per_page_fixed_bytes=[PerPageFixedBytes(page_number=1, vector_bytes=0,
                                                text_bytes=1000, annotation_bytes=0)],
        stripped_metadata_fields=[])
    prefs = UserPreferences(target_size_mb=10.0, compression_target="screen")

    plan1 = decide_compression([], pages, prefs, pre, None, 1, CFG)
    result1 = execute_compression(plan1, pages, ctx, CFG)   # 单轮执行
    plan2 = decide_compression([], pages, prefs, pre, result1, 2, CFG)  # 直接回传

    assert plan2.round_number == 2
    # 调整方向与公式一致（在测试内重推期望值）
    gap_ratio = (result1.total_raster_bytes - plan1.raster_budget) / plan1.raster_budget
    if gap_ratio > CFG.gap_large_threshold:
        expected = min(1.0, plan1.base_aggressiveness + CFG.aggressiveness_step_large)
    elif gap_ratio > 0:
        expected = min(1.0, plan1.base_aggressiveness + CFG.aggressiveness_step_small)
    elif gap_ratio < -CFG.convergence_tolerance:
        expected = max(0.0, plan1.base_aggressiveness - CFG.aggressiveness_step_small)
    else:
        expected = plan1.base_aggressiveness
    assert plan2.base_aggressiveness == pytest.approx(expected)
