"""Phase 8（运行别名 Phase D）：压缩参数决策引擎 —— 纯函数。

铁律（《压缩决策引擎.md》五条局部铁律 + 附录 A grep 强制）：
- 纯函数：本模块禁止文件 I/O、网络调用、图像处理库；只做数学运算。
  config 由调用方（orchestrator）作为参数传入，本模块不读磁盘、不读全局 settings。
- 只降不升：target_dpi ≤ original_dpi，max_dimension ≤ 原图最长边。
- 面积感知：eff_aggr = base + area_sensitivity × (1 − area_ratio)。
- 用户意志优先：skip 页不生成任何 plan，循环中也不触碰。
- 预算守恒：唯一流口径的估算总和 ≤ raster_budget × budget_overshoot_tolerance。
- 相同输入必须产出完全相同输出（无随机性、无时间依赖）。
- 所有阈值与系数来自 CompressionConfig（config.yaml），本文件零裸参数。

预算模型（《压缩决策引擎.md》§7.1 修订 2026-07-04）：
    irreducible_fixed = total_fixed_bytes − Σ per_page.vector_bytes
    vector_planned    = Σ 矢量组按已选策略的估算成本（策略由阈值规则决定，与预算无关，
                        故它就是当前规则下的可达下限）
    skip_raster       = skip 页位图原样保留成本（按唯一 data_ref 去重）
    raster_budget     = target − irreducible_fixed − vector_planned − skip_raster
    raster_budget ≤ 0 → TARGET_TOO_SMALL

共享 data_ref 预算口径（《数据契约.md》§3.1.1）：同一 data_ref 的多处摆放在输出中
只嵌入一份压缩流（Phase 9 去重压缩、Phase 10 共享插入），因此估算按"每页每唯一
data_ref 取各摆放中最大 estimated_bytes"求和——逐摆放累加会把平铺贴图重复计数千次。
"""

from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from app.config.settings import CompressionConfig
from app.contracts import (
    ClassifiedPage,
    CompressionPlan,
    CompressionResult,
    CompressionTier,
    PageElements,
    PagePlan,
    PhaseError,
    PreprocessResult,
    RasterImage,
    RasterPlan,
    UserPreferences,
    VectorPath,
    VectorPlan,
    WholePageRasterPlan,
)

BYTES_PER_MB = 1024 * 1024   # 单位换算，非可调参数
POINTS_PER_INCH = 72.0       # PDF 用户单位定义，非可调参数


def _lerp(a: float, b: float, t: float) -> float:
    return a + (b - a) * t


def _clamp(x: float, lo: float, hi: float) -> float:
    return max(lo, min(hi, x))


# ---------------------------------------------------------------- 主控变量推导

def _initial_aggressiveness(user_prefs: UserPreferences, config: CompressionConfig) -> float:
    """第 1 轮：从压缩场景预设推导；预设缺失时回退全局默认。"""
    preset = config.presets.get(user_prefs.compression_target)
    return preset.aggressiveness if preset is not None else config.aggressiveness


def _next_base_aggressiveness(
    previous_result: CompressionResult,
    raster_budget: int,
    config: CompressionConfig,
) -> float:
    """第 N 轮（N>1）：按上一轮 gap 调整全局激进度（§8.2，一维步进搜索）。

    prev_aggressiveness 从 previous_result.base_aggressiveness 读取
    （Phase 9 从 Plan 原样回带——2026-07-04 契约修订闭合的反馈环）。
    """
    prev = previous_result.base_aggressiveness
    gap_ratio = (previous_result.total_raster_bytes - raster_budget) / raster_budget
    if gap_ratio > config.gap_large_threshold:
        # 超标严重 → 大步加压
        return min(1.0, prev + config.aggressiveness_step_large)
    if gap_ratio > 0:
        # 轻微超标 → 小步加压
        return min(1.0, prev + config.aggressiveness_step_small)
    if gap_ratio < -config.convergence_tolerance:
        # 浪费过多 → 小步放宽
        return max(0.0, prev - config.aggressiveness_step_small)
    return prev  # 已在容忍带内（通常 orchestrator 已 break，不会走到）


# ---------------------------------------------------------------- 体积估算

def _estimate_raster_bytes(
    pixel_count: float, quality: int, fmt: str, config: CompressionConfig
) -> int:
    """§5.5 经验公式：粗估 ±20%，由收敛循环修正。"""
    if fmt == "jpeg":
        bpp = config.jpeg_bpp_base + (quality / 100.0) * config.jpeg_bpp_quality_coeff
    else:  # png
        bpp = config.png_bytes_per_pixel
    return int(pixel_count * bpp)


def _reestimate(plan: RasterPlan, image: RasterImage, config: CompressionConfig) -> None:
    """按 plan 当前参数重算 estimated_bytes（再平衡后调用）。"""
    scale = min(1.0, (plan.target_dpi / image.dpi) ** 2)
    pixel_count = image.width * image.height * scale
    plan.estimated_bytes = _estimate_raster_bytes(
        pixel_count, plan.quality, plan.output_format, config
    )


# ---------------------------------------------------------------- 位图决策

def _decide_raster(
    image: RasterImage,
    image_index: int,
    page_area: float,
    page_aggressiveness: float,
    config: CompressionConfig,
) -> RasterPlan:
    """单张位图的参数推导（§4 核心公式 + §5.4 alpha 三态格式策略）。"""
    area_ratio = image.bbox.area_ratio(page_area)

    # 面积修正后的有效激进度（连续公式，2026-07-04 决策理由记录见 §4.3）
    eff = _clamp(
        page_aggressiveness + config.area_sensitivity * (1.0 - area_ratio), 0.0, 1.0
    )
    quality = int(round(_lerp(float(config.quality_ceiling), float(config.quality_floor), eff)))
    base_dpi = _lerp(float(config.dpi_ceiling), float(config.dpi_floor), eff)

    # 只降不升（截断取整，绝不越过 original_dpi）
    target_dpi = max(1, int(min(base_dpi, image.dpi)))

    # 像素天花板（§4.5）：display 尺寸反算最长边像素上限，供 Phase 9 resize 兜底。
    # 注：extract 的 dpi 是 display 反算值，按 target_dpi 缩放天然满足天花板；
    # 此处 max_dimension 是执行层的硬约束载体，并叠加"不高于原图最长边"（只降不升）。
    display_long_inches = max(image.bbox.w, image.bbox.h) / POINTS_PER_INCH
    max_dimension = max(1, int(display_long_inches * target_dpi * config.pixel_ceiling_ratio))
    max_dimension = min(max_dimension, max(image.width, image.height))

    # §5.4 修订（架构决策 A2，2026-07-04）：base+SMask 结构原地保留，SMask 是否
    # 保真由 assemble 层的 xref_copy(keep=["SMask","Mask"]) 保证，与 decide 的
    # 格式/质量选择解耦——所有位图基底统一走 JPEG（含真透明）。
    output_format = "jpeg"

    scale = min(1.0, (target_dpi / image.dpi) ** 2)
    pixel_count = image.width * image.height * scale
    return RasterPlan(
        image_index=image_index,
        area_ratio=area_ratio,
        original_dpi=image.dpi,
        original_bytes=image.original_bytes,
        target_dpi=target_dpi,
        quality=quality,
        max_dimension=max_dimension,
        output_format=output_format,
        estimated_bytes=_estimate_raster_bytes(pixel_count, quality, output_format, config),
    )


# ---------------------------------------------------------------- 矢量决策

def _decide_vector(
    vector: VectorPath, group_index: int, page_area: float, config: CompressionConfig
) -> Tuple[VectorPlan, int]:
    """单个矢量组的策略决策（§6.1-6.4）。返回 (plan, 估算输出成本)。

    决策顺序：先面积门槛，后控制点三级阈值（§6.3）。策略与预算无关，
    因此估算成本即当前规则下的可达下限（§7.1 修订的依据）。
    """
    area_ratio = vector.bbox.area_ratio(page_area)
    cp = vector.control_point_count

    # 面积过小 → 无条件 keep（不值得处理）；控制点少 → keep
    if area_ratio < config.vector_ignore_area_ratio or cp < config.vector_simplify_threshold:
        plan = VectorPlan(
            path_group_index=group_index, area_ratio=area_ratio,
            original_bytes=vector.original_bytes, strategy="keep",
        )
        return plan, vector.original_bytes

    def _simplify_plan() -> Tuple[VectorPlan, int]:
        tolerance = (
            config.simplify_tolerance_conservative
            if area_ratio > config.area_tier_large
            else config.simplify_tolerance_aggressive
        )
        plan = VectorPlan(
            path_group_index=group_index, area_ratio=area_ratio,
            original_bytes=vector.original_bytes, strategy="simplify",
            simplify_tolerance=tolerance,
        )
        return plan, int(vector.original_bytes * config.simplify_size_factor)

    # simplify 带：中等复杂度，或复杂度够 rasterize 但面积不够（<0.3 只能 keep/simplify）
    if cp < config.vector_rasterize_threshold or area_ratio < config.vector_rasterize_min_area_ratio:
        return _simplify_plan()

    # rasterize：极端复杂矢量（不可逆，review 界面须明确告知用户）
    r_dpi = (
        config.rasterize_dpi_large
        if area_ratio > config.area_tier_large
        else config.rasterize_dpi_medium
    )
    pixel_count = (
        (vector.bbox.w / POINTS_PER_INCH * r_dpi)
        * (vector.bbox.h / POINTS_PER_INCH * r_dpi)
    )
    rasterize_cost = _estimate_raster_bytes(
        pixel_count, config.rasterize_estimate_quality, "jpeg", config
    )
    # F1 成本守卫（2026-07-04 用户批准）：§6.1 前提"矢量数据比等效位图大"不成立
    # （光栅化比 keep 还贵）→ 降级 simplify。大 bbox 整页 CAD 的典型保护。
    if rasterize_cost >= vector.original_bytes:
        return _simplify_plan()

    plan = VectorPlan(
        path_group_index=group_index, area_ratio=area_ratio,
        original_bytes=vector.original_bytes, strategy="rasterize",
        rasterize_dpi=r_dpi,
    )
    return plan, rasterize_cost


# ---------------------------------------------------------------- Tier 2/3 整页光栅化（§8.5-8.7）

def _tier_bounds(tier: str, config: CompressionConfig) -> Tuple[int, int, int, int]:
    """(quality_floor, quality_ceiling, dpi_floor, dpi_ceiling)。

    Tier 3 用独立下限（§8.7 用户裁决 2）："达标优先于质量"哲学下允许突破目视
    标定 floor，但绝不污染 Tier 1/2 的标定值。契约层只管通用界（§1.5 约定），
    这里是 config 相关界的唯一收紧点。
    """
    if tier == "full_raster":
        return (config.tier3_quality_floor, config.quality_ceiling,
                config.tier3_dpi_floor, config.tier3_dpi_ceiling)
    return (config.quality_floor, config.quality_ceiling,
            config.dpi_floor, config.dpi_ceiling)


def _make_whole_page_plan(
    page: PageElements, aggressiveness: float, tier: str, config: CompressionConfig
) -> WholePageRasterPlan:
    """整页光栅化计划：参数从 aggressiveness 经 §4.3 lerp 推导（整页即满页主图，
    area_ratio=1，无面积修正），边界按 Tier 取（§8.7）。"""
    q_floor, q_ceiling, d_floor, d_ceiling = _tier_bounds(tier, config)
    quality = int(round(_lerp(float(q_ceiling), float(q_floor), aggressiveness)))
    dpi = max(1, int(_lerp(float(d_ceiling), float(d_floor), aggressiveness)))
    return WholePageRasterPlan(
        dpi=dpi, quality=quality,
        estimated_bytes=_estimate_whole_page_bytes(page, dpi, quality, config),
    )


def _estimate_whole_page_bytes(
    page: PageElements, dpi: int, quality: int, config: CompressionConfig
) -> int:
    pixel_count = (
        (page.page_width / POINTS_PER_INCH * dpi)
        * (page.page_height / POINTS_PER_INCH * dpi)
    )
    return _estimate_raster_bytes(pixel_count, quality, "jpeg", config)


def _page_floor_raster_estimate(page: PageElements, config: CompressionConfig) -> int:
    """该页唯一位图在标准下限参数（dpi_floor/quality_floor）下的估算成本之和
    （§8.6 保留成本的位图分量——in_place 语义下这已是位图的可达下限）。"""
    seen: Dict[str, int] = {}
    for img in page.raster_images:
        if img.data_ref in seen:
            continue
        scale = min(1.0, (config.dpi_floor / img.dpi) ** 2)
        seen[img.data_ref] = _estimate_raster_bytes(
            img.width * img.height * scale, config.quality_floor, "jpeg", config
        )
    return sum(seen.values())


def select_whole_pages(
    pages: List[PageElements],
    preprocess: PreprocessResult,
    user_prefs: UserPreferences,
    config: CompressionConfig,
    tier: CompressionTier,
) -> Set[int]:
    """§8.6 选页（纯函数）：返回需整页光栅化的 page_number 集合。

    - in_place → 空集；full_raster → 全部非 skip 页；
    - hybrid → 候选资格（光栅化真的省钱）+ 贪心（净节省降序，选够即停）。
      估算用 Tier 预设激进度（非轮次调整值）——选页在收敛轮之间保持稳定，
      不随 aggressiveness 步进抖动；whole_page_plan 的实际参数仍按轮次推导。
    - skip 页无条件排除（铁律 4，三个 Tier 一致）。
    """
    if tier == "in_place":
        return set()
    skip_pages = {
        o.page_number for o in user_prefs.per_page_overrides if o.action == "skip"
    }
    if tier == "full_raster":
        return {p.page_number for p in pages if p.page_number not in skip_pages}

    # ---- hybrid：候选资格 + 贪心 ----
    # 保留成本按**原地手术真实语义**：矢量原样保留（in_place 不执行元素级
    # simplify/rasterize 策略——8a 架构记录/R9，这正是 Tier 2 存在的原因），
    # 因此矢量分量用 original_bytes，而非 _decide_vector 的计划下限（那是
    # 重建语义的虚构值，会让重矢量页永远"看起来不值得整页光栅化"）。
    preset_aggr = _initial_aggressiveness(user_prefs, config)
    target_bytes = int(user_prefs.target_size_mb * BYTES_PER_MB)
    fixed_by_page = {pp.page_number: pp for pp in preprocess.per_page_fixed_bytes}
    vector_fixed = sum(pp.vector_bytes for pp in preprocess.per_page_fixed_bytes)
    irreducible = preprocess.total_fixed_bytes - vector_fixed

    skip_floor = 0          # skip 页原样保留成本（矢量 + 位图）
    vec_retained: Dict[int, int] = {}
    candidates: List[Tuple[int, int, int, int, int]] = []
    for page in pages:
        if page.page_number in skip_pages:
            skip_floor += sum(v.original_bytes for v in page.vector_paths)
            skip_floor += _unique_original_raster_bytes(page)
            continue
        vec_bytes = sum(v.original_bytes for v in page.vector_paths)
        vec_retained[page.page_number] = vec_bytes
        fx = fixed_by_page.get(page.page_number)
        text_bytes = fx.text_bytes if fx is not None else 0
        keep_cost = vec_bytes + text_bytes + _page_floor_raster_estimate(page, config)
        whole_est = _make_whole_page_plan(page, preset_aggr, tier, config).estimated_bytes
        if whole_est < keep_cost:  # 资格：F1 成本守卫的反向应用
            savings = keep_cost - whole_est
            candidates.append((savings, page.page_number, whole_est, text_bytes, vec_bytes))

    # 贪心：净节省降序纳入，floor 降到停止线即停（最小化被光栅化的页数）
    floor = irreducible + skip_floor + sum(vec_retained.values())
    stop_line = target_bytes * config.tier2_budget_margin
    selected: Set[int] = set()
    for savings, page_number, whole_est, text_bytes, vec_bytes in sorted(
        candidates, key=lambda c: (-c[0], c[1])
    ):
        if floor <= stop_line:
            break
        floor = floor - vec_bytes - text_bytes + whole_est
        selected.add(page_number)
    return selected


# ---------------------------------------------------------------- 预算口径（共享 data_ref）

def _unique_stream_total(page_plans: List[PagePlan], pages: List[PageElements]) -> int:
    """唯一流口径的位图估算总和：每页每唯一 data_ref 取各摆放中最大的估算值。"""
    total = 0
    for plan_page, page in zip(page_plans, pages):
        if plan_page.skip:
            continue
        best_per_ref: Dict[str, int] = {}
        for rp in plan_page.raster_plans:
            ref = page.raster_images[rp.image_index].data_ref
            best_per_ref[ref] = max(best_per_ref.get(ref, 0), rp.estimated_bytes)
        total += sum(best_per_ref.values())
    return total


def _unique_original_raster_bytes(page: PageElements) -> int:
    """skip 页位图原样保留的成本（唯一 data_ref 去重，源文件本就只存一份）。"""
    seen: Dict[str, int] = {}
    for img in page.raster_images:
        seen[img.data_ref] = img.original_bytes
    return sum(seen.values())


# ---------------------------------------------------------------- 主入口

def decide_compression(
    classified: List[ClassifiedPage],
    pages: List[PageElements],
    user_prefs: UserPreferences,
    preprocess: PreprocessResult,
    previous_result: Optional[CompressionResult],
    round_number: int,
    config: CompressionConfig,
    tier: CompressionTier = "in_place",
) -> CompressionPlan:
    target_bytes = int(user_prefs.target_size_mb * BYTES_PER_MB)
    overrides = {o.page_number: o.action for o in user_prefs.per_page_overrides}
    # Tier 2/3（§8.5-8.6）：需整页光栅化的页集合（纯函数选页，skip 页永不入选）
    whole_pages = select_whole_pages(pages, preprocess, user_prefs, config, tier)
    fixed_by_page = {pp.page_number: pp for pp in preprocess.per_page_fixed_bytes}

    # 一、矢量决策先行（策略与预算无关 → 矢量计划成本 = 可达下限）
    #    整页光栅化的页不出矢量计划——其矢量随页内容一并消亡
    vector_plans_by_page: Dict[int, List[VectorPlan]] = {}
    vector_planned_total = 0
    skip_raster_bytes = 0
    for page in pages:
        page_area = page.page_width * page.page_height
        if overrides.get(page.page_number) == "skip":
            # 铁律"用户意志优先"：skip 页不生成任何 plan，其内容原样计入开销
            vector_planned_total += sum(v.original_bytes for v in page.vector_paths)
            skip_raster_bytes += _unique_original_raster_bytes(page)
            continue
        if page.page_number in whole_pages:
            continue
        plans: List[VectorPlan] = []
        for gi, vec in enumerate(page.vector_paths):
            vplan, cost = _decide_vector(vec, gi, page_area, config)
            plans.append(vplan)
            vector_planned_total += cost
        vector_plans_by_page[page.page_number] = plans

    # 二、预算计算（§7.1 修订：基于不可削减下限）
    #    整页光栅化的页：其文字结构开销随内容流替换一并消亡，从不可削减中扣除；
    #    其整页图像成本是可调节项（随 quality/DPI 收敛），计入预算消耗而非下限
    vector_fixed = sum(pp.vector_bytes for pp in preprocess.per_page_fixed_bytes)
    whole_text_removed = sum(
        fixed_by_page[pn].text_bytes for pn in whole_pages if pn in fixed_by_page
    )
    irreducible_fixed = preprocess.total_fixed_bytes - vector_fixed - whole_text_removed
    floor_bytes = irreducible_fixed + vector_planned_total + skip_raster_bytes
    raster_budget = target_bytes - floor_bytes
    if raster_budget <= 0:
        raise PhaseError(
            phase="decide",
            code="TARGET_TOO_SMALL",
            message=(
                f"[tier={tier}] 不可削减开销 {floor_bytes} 字节（固定 {irreducible_fixed}"
                f" + 矢量下限 {vector_planned_total} + skip 页位图 {skip_raster_bytes}）"
                f"已超过目标 {target_bytes} 字节"
            ),
            recoverable=False,
        )

    # 三、全局激进度（第 1 轮：预设；第 N 轮：按上一轮 gap 步进）
    if round_number <= 1 or previous_result is None:
        base_aggressiveness = _initial_aggressiveness(user_prefs, config)
    else:
        base_aggressiveness = _next_base_aggressiveness(
            previous_result, raster_budget, config
        )

    # 四、页计划（skip 页除外；aggressive 页加一档激进度；whole 页出整页计划）
    page_plans: List[PagePlan] = []
    for page in pages:
        if overrides.get(page.page_number) == "skip":
            page_plans.append(PagePlan(page_number=page.page_number, skip=True))
            continue
        page_aggr = base_aggressiveness
        if overrides.get(page.page_number) == "aggressive":
            page_aggr = _clamp(
                base_aggressiveness + config.aggressiveness_step_large, 0.0, 1.0
            )
        if page.page_number in whole_pages:
            page_plans.append(PagePlan(
                page_number=page.page_number,
                skip=False,
                whole_page_plan=_make_whole_page_plan(page, page_aggr, tier, config),
            ))
            continue
        page_area = page.page_width * page.page_height
        raster_plans = [
            _decide_raster(img, i, page_area, page_aggr, config)
            for i, img in enumerate(page.raster_images)
        ]
        page_plans.append(PagePlan(
            page_number=page.page_number,
            skip=False,
            raster_plans=raster_plans,
            vector_plans=vector_plans_by_page.get(page.page_number, []),
        ))

    # 五、预算校验与再平衡（§7.4；唯一流口径 + 整页计划）
    tier_q_floor, _q_ceiling, tier_d_floor, _d_ceiling = _tier_bounds(tier, config)
    pages_by_number = {p.page_number: p for p in pages}

    def _total_estimated() -> int:
        whole_total = sum(
            pp.whole_page_plan.estimated_bytes
            for pp in page_plans if pp.whole_page_plan is not None
        )
        return _unique_stream_total(page_plans, pages) + whole_total

    total_estimated = _total_estimated()
    if total_estimated > raster_budget * config.budget_overshoot_tolerance:
        # 第一遍：线性缩 quality（元素级与整页计划同步缩，floor 按 Tier 取）
        scale = raster_budget / total_estimated
        for plan_page, page in zip(page_plans, pages):
            if plan_page.skip:
                continue
            if plan_page.whole_page_plan is not None:
                wpp = plan_page.whole_page_plan
                wpp.quality = max(tier_q_floor, int(wpp.quality * scale))
                wpp.estimated_bytes = _estimate_whole_page_bytes(
                    pages_by_number[plan_page.page_number], wpp.dpi, wpp.quality, config
                )
                continue
            for rp in plan_page.raster_plans:
                rp.quality = max(config.quality_floor, int(rp.quality * scale))
                _reestimate(rp, page.raster_images[rp.image_index], config)
        total_estimated = _total_estimated()
        if total_estimated > raster_budget * config.budget_overshoot_tolerance:
            # 第二遍：quality 见底仍超 → 按面积比例缩 DPI（像素数 ∝ dpi²）
            dpi_scale = (raster_budget / total_estimated) ** 0.5
            for plan_page, page in zip(page_plans, pages):
                if plan_page.skip:
                    continue
                if plan_page.whole_page_plan is not None:
                    wpp = plan_page.whole_page_plan
                    floor_guard = min(tier_d_floor, wpp.dpi)
                    wpp.dpi = max(1, max(int(wpp.dpi * dpi_scale), floor_guard))
                    wpp.estimated_bytes = _estimate_whole_page_bytes(
                        pages_by_number[plan_page.page_number], wpp.dpi, wpp.quality, config
                    )
                    continue
                for rp in plan_page.raster_plans:
                    image = page.raster_images[rp.image_index]
                    scaled_dpi = int(rp.target_dpi * dpi_scale)
                    # dpi_floor 是推导下限但不得反超"只降不升"后的现值
                    floor_guard = min(config.dpi_floor, rp.target_dpi)
                    rp.target_dpi = max(1, max(scaled_dpi, floor_guard))
                    display_long_inches = max(image.bbox.w, image.bbox.h) / POINTS_PER_INCH
                    rp.max_dimension = min(
                        max(1, int(display_long_inches * rp.target_dpi
                                   * config.pixel_ceiling_ratio)),
                        max(image.width, image.height),
                    )
                    _reestimate(rp, image, config)

    return CompressionPlan(
        raster_budget=raster_budget,
        pages=page_plans,
        round_number=round_number,
        base_aggressiveness=base_aggressiveness,
        tier=tier,
    )
