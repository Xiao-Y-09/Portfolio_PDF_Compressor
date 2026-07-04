# Phase D — 压缩参数决策引擎

> **定位**：Phase D 是整个压缩流水线唯一包含"不确定性"的环节。其他 Phase 都是单程执行，Phase D 需要通过反馈循环收敛到目标文件大小。它是纯函数——相同输入永远产出相同 Plan，不做任何实际压缩，不读写磁盘。

---

## 一、Phase D 在流水线中的位置

```
Phase A  拆页
Phase B  元素提取
Phase C  分类
         ⏸ 用户 review
Phase D0 确定性预处理（字体子集化 + 固定开销计算）  ← 只跑一次
Phase D  压缩参数决策（纯函数，可被反复调用）       ← 本文档
Phase E  压缩执行
         D+E 收敛循环（binary search）
Phase F  PDF 重组 + 输出
```

### 铁律（Phase D 不可违反的约束）

1. **纯函数**：Phase D 不做任何实际压缩、不读写文件、不调用网络
2. **只降不升**：任何参数调整不能让元素变得比原始更大（不升采样、不升质量）
3. **面积感知**：压缩力度必须考虑元素在页面中的视觉占比，小元素可以被更激进地压缩
4. **用户意志优先**：用户标记"不压缩"的页面，Phase D 不生成任何 plan，循环中也不触碰
5. **预算守恒**：所有页面的位图预算之和 ≤ raster_budget，不允许超支

---

## 二、输入契约

```
DecisionInput {
    // 来自 Phase C + 用户 review
    classified_pages: ClassifiedPage[],

    // 来自用户选择
    user_preferences: {
        target_size_mb: float,              // 用户选择的目标大小：5 / 10 / 15 / 20
        compression_target: "screen" | "print" | "email",
        per_page_overrides: [{
            page_number: int,
            action: "skip" | "aggressive" | "reclassify",
            new_type?: string               // reclassify 时使用
        }]
    },

    // 来自 Phase D0（确定性预处理）
    fixed_overhead: {
        total_fixed_bytes: int,             // 矢量 + 文字 + 子集化字体 + 元数据 + PDF 结构
        per_page_fixed_bytes: [{            // 每页的固定开销明细
            page_number: int,
            vector_bytes: int,
            text_bytes: int,
            annotation_bytes: int
        }]
    },

    // 来自 Phase B（元素提取）
    page_elements: PageElements[],          // 每页的原始元素数据（用于读取 dpi/bbox 等）

    // 来自上一轮循环（第一轮为 null）
    previous_result: null | {
        total_raster_bytes: int,
        per_image_results: [{
            page_number: int,
            image_index: int,
            compressed_bytes: int,
            params_used: { dpi, quality }   // 上一轮用了什么参数
        }]
    }
}
```

---

## 三、输出契约

```
CompressionPlan {
    raster_budget: int,                     // = target_size - total_fixed_bytes

    pages: [{
        page_number: int,
        skip: bool,                         // 用户标记不压缩 → true

        raster_plans: [{
            image_index: int,               // 对应 PageElements.raster_images 下标
            area_ratio: float,              // 该图 bbox 面积 / 页面面积（0~1）
            original_dpi: float,            // Phase B 提取的原始 DPI
            original_bytes: int,            // 原始大小
            target_dpi: int,                // 决策：降到多少 DPI
            quality: int,                   // 决策：JPEG 质量 0-100
            max_dimension: int,             // 决策：最长边像素上限
            output_format: "jpeg" | "png",  // 决策：输出格式
            estimated_bytes: int            // 估算压缩后大小
        }],

        vector_plans: [{
            path_group_index: int,          // 对应 PageElements.vector_paths 分组下标
            area_ratio: float,              // 该矢量组 bbox 面积 / 页面面积
            original_bytes: int,
            strategy: "keep" | "rasterize" | "simplify",
            rasterize_dpi?: int,            // strategy=rasterize 时的目标 DPI
            simplify_tolerance?: float      // strategy=simplify 时的容差
        }]
    }]
}
```

---

## 四、核心概念——面积占比系数（Area Ratio）

### 定义

```
area_ratio = element_bbox_area / page_area
```

其中 `element_bbox_area = bbox.w × bbox.h`，`page_area = page_width × page_height`。

### 为什么需要它

同一张 3000×2000 的渲染图：
- 铺满整个 A3 页面（area_ratio ≈ 0.95）→ 用户会仔细看，需要高质量
- 作为角落的小缩略图（area_ratio ≈ 0.05）→ 用户不会放大看，可以狠压

### 面积系数对参数的影响

```
quality_modifier = lerp(aggressive_quality, conservative_quality, area_ratio)
```

具体来说：

| area_ratio 范围 | 含义 | 质量倾向 |
|----------------|------|---------|
| > 0.6          | 主图，占据页面大部分 | 保守，优先保质量 |
| 0.2 ~ 0.6     | 中等元素 | 中等压缩 |
| < 0.2          | 小图/缩略图/装饰 | 激进，大幅压缩 |

---

## 五、渲染图（Raster）压缩策略

### 5.1 参数维度

渲染图有三个可调参数，按影响力排序：

1. **DPI 降采样**（影响最大）：从 300dpi 降到 150dpi，像素数变为 1/4，体积约变为 1/4
2. **JPEG 质量**（影响中等）：从 q=95 降到 q=70，体积约变为 1/3
3. **最大尺寸**（兜底保护）：防止超大图片即使降了 DPI 也还是太大

### 5.2 基准参数表

根据 `compression_target × page_type` 确定基准参数，再用 area_ratio 修正。

**基准表（area_ratio = 1.0 时的参数，即主图满页）：**

```
                    screen          print           email
                    dpi   q         dpi   q         dpi   q
photo_heavy         150   80        250   90        96    65
mixed               150   78        250   85        96    62
text_heavy          120   75        200   85        96    60
(页面里的小配图)
```

### 5.3 面积修正公式

```python
def compute_raster_params(base_dpi, base_quality, area_ratio, original_dpi):
    """
    area_ratio 越小 → 压得越狠
    但不能超过原始 DPI（只降不升）
    """

    # DPI 修正：小图不需要高 DPI
    if area_ratio < 0.2:
        dpi_factor = 0.5        # 小图 DPI 减半
    elif area_ratio < 0.6:
        dpi_factor = 0.75       # 中图轻微降低
    else:
        dpi_factor = 1.0        # 主图保持基准

    target_dpi = min(base_dpi * dpi_factor, original_dpi)   # 只降不升

    # Quality 修正：小图质量可以更低
    quality_penalty = int((1.0 - area_ratio) * 15)          # area_ratio=0 时 -15
    target_quality = max(30, base_quality - quality_penalty) # 质量下限 30

    return target_dpi, target_quality
```

### 5.4 透明背景/透明罩处理

```
if image.has_alpha_channel:
    output_format = "png"       # JPEG 不支持透明，必须用 PNG
    # PNG 压缩空间有限，主要靠降 DPI 和降分辨率
    # quality 参数对 PNG 无意义，改为 png_compression_level (0-9)
else:
    output_format = "jpeg"      # 无透明通道，JPEG 压缩率远高于 PNG
```

注意：有些 PDF 里的"透明背景渲染图"实际上是白色背景伪装的，alpha 通道全是 255。Phase B 提取时应检测是否为真透明。如果是伪透明，Phase D 可以安全地转为 JPEG。

### 5.5 体积估算公式

Phase D 不做真正的压缩，但需要估算压缩后的大小来做预算分配：

```python
def estimate_raster_size(width, height, original_dpi, target_dpi, quality, fmt):
    """粗略估算，误差 ±20% 可接受，binary search 会修正"""
    scale = (target_dpi / original_dpi) ** 2
    pixel_count = width * height * scale

    if fmt == "jpeg":
        # JPEG 经验公式：每像素字节数随 quality 近似线性
        bytes_per_pixel = 0.04 + (quality / 100) * 0.12    # q=100 → ~0.16, q=50 → ~0.10
    else:  # png
        bytes_per_pixel = 0.35   # PNG 无损，取经验均值

    return int(pixel_count * bytes_per_pixel)
```

---

## 六、矢量图（Vector）压缩策略

### 6.1 矢量图的特殊性

矢量数据在大多数情况下已经很小，**默认策略是不动它（keep）**。

但有一个你已经发现的例外：**复杂 CAD/线稿图**。判断标准：

```python
def is_complex_vector(page_elements, page_number):
    vectors = page_elements[page_number].vector_paths
    total_control_points = sum(count_points(v.path_data) for v in vectors)
    vector_area_ratio = sum(v.bbox.area for v in vectors) / page_area

    return total_control_points > 50000 or \
           (total_control_points > 10000 and vector_area_ratio > 0.7)
```

### 6.2 三种矢量策略

**策略 A：keep（保持原样）**

适用于：area_ratio 小的矢量装饰（图标、箭头、边框）、控制点少的简单图形。

```
触发条件：control_points < 10000 或 area_ratio < 0.3
结果：不做任何处理
体积变化：0
```

**策略 B：simplify（路径简化）**

适用于：中等复杂度的矢量图，保留矢量格式但减少控制点。

```
触发条件：10000 ≤ control_points < 100000 且 area_ratio ≥ 0.3
参数：tolerance（容差）
  - area_ratio > 0.6（主图）：tolerance = 0.5    → 几乎看不出变化
  - area_ratio 0.3~0.6    ：tolerance = 1.0    → 轻微简化
风险：直线可能轻微弯曲，曲线可能失真
体积减少：约 20%-50%
```

**策略 C：rasterize（光栅化）**

适用于：极端复杂的矢量图（超密 CAD 平面图，数十万控制点），矢量数据本身已经比等效位图还大。

```
触发条件：control_points ≥ 100000
操作：将矢量渲染成位图，然后走 Raster 压缩策略
参数：rasterize_dpi
  - area_ratio > 0.6（主图）：rasterize_dpi = 200
  - area_ratio 0.3~0.6    ：rasterize_dpi = 150
  - area_ratio < 0.3      ：rasterize_dpi = 96

注意：光栅化是不可逆的！用户必须在 review 界面被明确告知。
```

### 6.3 面积修正

矢量压缩的面积修正比位图简单，因为只影响策略选择，不影响连续参数：

```
area_ratio < 0.1  → 无论复杂度如何，keep（太小了不值得处理）
area_ratio < 0.3  → 只能 keep 或 simplify（小图不值得 rasterize）
area_ratio ≥ 0.3  → 根据 control_points 决定三选一
```

### 6.4 矢量体积估算

```python
def estimate_vector_size(vectors, strategy, rasterize_dpi=None):
    if strategy == "keep":
        return sum(v.original_bytes for v in vectors)
    elif strategy == "simplify":
        return sum(v.original_bytes for v in vectors) * 0.5  # 保守估计减半
    elif strategy == "rasterize":
        # 光栅化后走位图估算逻辑
        return estimate_raster_size(
            bbox.w * rasterize_dpi / 72,   # 宽（像素）
            bbox.h * rasterize_dpi / 72,   # 高（像素）
            rasterize_dpi, rasterize_dpi,
            quality=80, fmt="jpeg"
        )
```

---

## 七、预算分配算法

### 7.1 预算计算

```
target_bytes     = user_preferences.target_size_mb × 1024 × 1024
fixed_bytes      = fixed_overhead.total_fixed_bytes
raster_budget    = target_bytes - fixed_bytes

if raster_budget < 0:
    → 即使不压缩任何位图，固定开销已经超标
    → 返回错误：目标太小，无法达成
```

### 7.2 按页分配预算

不是每页平均分。按每页位图的"视觉重要性权重"分配：

```python
def compute_page_weight(page):
    """页面权重 = 该页位图原始体积的加权和"""
    weight = 0
    for img in page.raster_images:
        # 面积大的图权重高（用户更在意主图质量）
        weight += img.original_bytes * img.area_ratio
    return weight

total_weight = sum(compute_page_weight(p) for p in pages if not p.skip)

for page in pages:
    if page.skip:
        page.budget = page.original_raster_bytes  # 不压缩，原样计入
    else:
        page.budget = raster_budget * (compute_page_weight(page) / total_weight)
```

### 7.3 页内分配

一页内有多张位图时，也按 area_ratio 加权分配该页的预算：

```python
for img in page.raster_images:
    img.budget = page.budget * (img.area_ratio / sum_area_ratios_in_page)
```

---

## 八、Binary Search 收敛循环

### 8.1 循环结构

```
round = 0
max_rounds = 5

while round < max_rounds:
    plan = PhaseD(input, previous_result)
    result = PhaseE(plan)             # 真正压缩

    total = result.total_raster_bytes + fixed_bytes
    gap = total - target_bytes

    if gap ≤ 0 and gap > -(target_bytes * 0.15):
        break                          # 达标：不超标，且没浪费超过 15%

    if gap > 0:
        # 超标 → 下一轮 Phase D 会根据 previous_result 降低参数
        pass
    else:
        # 浪费太多 → 下一轮 Phase D 会适当提高参数
        pass

    previous_result = result
    round += 1
```

### 8.2 Phase D 在第 N 轮（N>1）的调整逻辑

```python
def adjust_params(image_plan, prev_image_result, gap_ratio):
    """
    gap_ratio > 0：超标，需要压得更狠
    gap_ratio < 0：浪费空间，可以放宽
    """
    if gap_ratio > 0.3:
        # 超标 30% 以上，大幅调整
        quality_delta = -10
        dpi_factor = 0.8
    elif gap_ratio > 0:
        # 轻微超标
        quality_delta = -5
        dpi_factor = 0.9
    elif gap_ratio < -0.15:
        # 浪费 15% 以上
        quality_delta = +5
        dpi_factor = 1.1
    else:
        return image_plan  # 在可接受范围内，不调整

    new_quality = clamp(prev.quality + quality_delta, 30, 95)
    new_dpi = min(prev.target_dpi * dpi_factor, image_plan.original_dpi)

    return updated_plan
```

### 8.3 收敛失败处理

5 轮后仍未达标：
- 如果仍超标 → 返回用户："目标 X MB 过小，当前最优压缩结果为 Y MB。建议选择更大的目标或标记更多页面为可激进压缩。"
- 如果浪费过多 → 接受当前结果（宁可小一点也不超标）

---

## 九、验证清单

### 纯函数验证

```bash
# Phase D 内无文件 I/O（期望：无结果）
grep -r "os\.Open\|os\.Create\|ioutil\|io\.Read" internal/compress/decision/

# Phase D 内无网络调用（期望：无结果）
grep -r "http\.\|net\." internal/compress/decision/

# Phase D 内无图像处理库调用（期望：无结果，压缩是 Phase E 的事）
grep -r "image\.\|jpeg\.\|png\.\|opencv\|pillow\|PIL" internal/compress/decision/
```

### 逻辑验证

- [ ] 相同输入调用两次 PhaseD，输出完全一致
- [ ] skip=true 的页面在 plan 中无 raster_plans
- [ ] 所有 target_dpi ≤ original_dpi（只降不升）
- [ ] 所有 quality ∈ [30, 95]
- [ ] area_ratio < 0.1 的矢量 strategy 永远是 "keep"
- [ ] rasterize 策略只出现在 control_points ≥ 100000 的矢量上
- [ ] Σ(estimated_bytes) ≤ raster_budget（首轮预算守恒）
- [ ] 透明通道图片 output_format = "png"
- [ ] previous_result = null 时使用基准参数表，不崩溃

### 收敛验证

- [ ] 5 轮内收敛（误差 < 15%）的成功率 > 90%（用 10 份不同的作品集测试）
- [ ] 每一轮的 total_bytes 单调趋近 target（不振荡）
- [ ] 收敛失败时返回明确的用户提示，不静默输出超标文件

---

## 十、关键常量参考表

| 模块 | 参数 | 默认值 | 含义 |
|------|------|--------|------|
| 面积系数 | 大图阈值 | 0.6 | area_ratio > 此值视为主图 |
| 面积系数 | 小图阈值 | 0.2 | area_ratio < 此值视为缩略图/装饰 |
| 位图 | quality 下限 | 30 | 任何情况不低于此值 |
| 位图 | quality 上限 | 95 | 任何情况不高于此值（100=无损，浪费） |
| 位图 | 估算误差容忍 | ±20% | 经验公式的可接受误差范围 |
| 矢量 | 简单阈值 | 10,000 | 控制点 < 此值 → keep |
| 矢量 | 复杂阈值 | 100,000 | 控制点 ≥ 此值 → rasterize |
| 矢量 | 小图免处理 | 0.1 | area_ratio < 此值 → 无条件 keep |
| 矢量 | simplify 保守容差 | 0.5 | 主图简化容差 |
| 矢量 | simplify 激进容差 | 1.0 | 中图简化容差 |
| 收敛 | 最大轮数 | 5 | binary search 最大迭代次数 |
| 收敛 | 达标下限 | -15% | 低于目标此比例视为浪费 |
| 收敛 | quality 步长（大调） | 10 | 超标 >30% 时的单轮调整量 |
| 收敛 | quality 步长（小调） | 5 | 超标 <30% 时的单轮调整量 |
| 收敛 | dpi 缩放（大调） | 0.8 | 超标 >30% 时的 DPI 乘数 |
| 收敛 | dpi 缩放（小调） | 0.9 | 超标 <30% 时的 DPI 乘数 |
| 预算 | 页权重算法 | bytes × area_ratio | 按视觉重要性加权分配 |
| 格式 | 透明通道 | → PNG | 有 alpha 通道强制 PNG |
| 格式 | 无透明 | → JPEG | 无 alpha 通道默认 JPEG |
