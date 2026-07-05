# PDF 压缩 SaaS 构建手册

> **使用方式**：把每个 Phase 的 Prompt 直接粘贴进 Fable 5（Cursor / Claude Code），按顺序执行。每个 Phase 完成后都有可独立验证的检查点，确保在继续下一步前当前阶段是可工作的。
>
> **具体实例**：本手册以"设计学生作品集压缩"为参考场景，输入是含渲染图/CAD/文字混排的 PDF 作品集（典型 50-200MB），目标是压缩到 5/10/15/20MB 之内且保留可读性。

---

## 一、系统定位与核心架构

### 系统是什么

面向设计学生和专业人士的 PDF 智能压缩工具：通过页面分类 + 元素级压缩策略 + 二分搜索收敛，将大体积作品集压缩到用户指定的目标大小，且保留矢量精度、文字清晰度、书签/超链接等结构化信息。

### 三层物理架构

- **前端（Next.js on Vercel）**：用户上传、进度显示、分类 review、结果下载
- **后端（Python/FastAPI on AWS EC2）**：压缩引擎主体，含 Phase A-F 全部逻辑
- **存储（EC2 本地 EBS + S3 可选）**：临时文件（处理完立即清理）+ 可选的长期归档

### 全局技术决策（不可推翻的铁律）

**在开始前，把这七条铁律贴在显眼的地方。违反任何一条都要立即停下来。**

1. **决策与执行分离**：Phase D（决策）不做压缩，Phase E（执行）不做决策。决策是纯函数，可反复调用；执行是昂贵操作，只在决策稳定后触发
2. **Phase D 纯函数**：决策模块内部禁止文件 I/O、网络调用、图像处理库调用（PIL/OpenCV），只做数学运算
3. **只降不升**：任何参数调整不能让元素变得比原始更大——不升采样、不升质量、不升 DPI
4. **面积感知**：压缩力度必须考虑元素的 `area_ratio = element_bbox_area / page_area`，小图和大图不同对待
5. **临时文件强制清理**：用户上传的 PDF 处理完成后（无论成功失败）必须在 5 分钟内删除，永不持久化
6. **输入输出契约不可下游修改**：Phase N 输出的数据结构一旦定义，Phase N+1 只能消费不能修改；如需调整必须回到契约层重新定义
7. **收敛失败不静默输出**：Binary search 5 轮内未达标必须返回明确错误提示，不允许输出超出用户目标 15% 以上的文件

---

## 二、压缩决策哲学 — 三主控变量框架

### 本质：把二三十个参数收敛为三个旋钮

传统 PDF 压缩工具暴露给用户几十个参数（quality、DPI、色彩空间、下采样算法……），普通用户根本不知道怎么调。本系统的核心设计是——**所有具体参数都可以从三个语义化的主控变量推导出来**。

### 三个主控变量

**变量 1：`aggressiveness`（压缩激进度，0.0 ~ 1.0）**

```
0.0 = 最保守，几乎不压
0.5 = 中等（默认，屏幕查看质量）
1.0 = 最激进，能压多狠压多狠
```

所有的 quality、DPI 基准值都从这一个数推导：

```
base_quality = lerp(quality_ceiling, quality_floor, aggressiveness)
base_dpi     = lerp(dpi_ceiling, dpi_floor, aggressiveness)
```

**变量 2：`area_sensitivity`（面积敏感度，0.0 ~ 1.0）**

控制"小图比大图压得狠多少"：

```
effective_aggressiveness = aggressiveness + area_sensitivity × (1.0 - area_ratio)

# area_ratio = 1.0 的主图：不加压
# area_ratio = 0.1 的小图：额外加 0.9 × area_sensitivity 的激进度
```

**变量 3：`pixel_ceiling_ratio`（像素天花板比率）**

根据元素在页面上的**显示尺寸**反算所需像素上限，超出部分强制裁掉。这是压缩效果最立竿见影的手段——一张 4K 渲染图缩小显示在页面角落时，多余的像素可以直接砍掉。

```
display_size_inches = bbox / 72
max_needed_pixels   = display_size_inches × target_dpi × pixel_ceiling_ratio
```

`pixel_ceiling_ratio = 1.2` 意味着允许比理论需要多 20% 的像素作为安全余量。

### 用户体验层的映射

用户在前端只选择三个东西：**目标大小 + 压缩场景 + 分类修改**。这些选择在后端映射到主控变量：

```
target_size_mb:   用户直选 5/10/15/20
compression_preset:
  print  → aggressiveness=0.2
  screen → aggressiveness=0.5（默认）
  email  → aggressiveness=0.8
per_page_override: 用户可对特定页面标记 skip / aggressive
```

### 二分搜索收敛

一次决策不可能精确命中目标大小。系统在 Phase D 和 Phase E 之间形成反馈环：

```
预算 = 目标大小 - 固定开销（矢量 + 文字 + 子集化字体 + 元数据）

第 N 轮：
  Phase D 生成 Plan → Phase E 执行 → 得到实际大小 total_N
  gap_N = total_N - target
  
  gap > 15%   → Phase D 下轮降低参数（quality -10, dpi × 0.8）
  gap ∈ [0, 15%]  → 已达标，进入 Phase F
  gap ∈ [-15%, 0] → 已达标（略保守），进入 Phase F
  gap < -15%  → 压过头浪费，下轮提高参数（quality +5, dpi × 1.1）
```

最多 5 轮。5 轮仍未收敛 → 明确返回用户"目标过小，无法达成"。

---

## Phase 0 — 环境初始化与 AI 协作基础设施

### 目标

建立 AI 工作约束文件（`CLAUDE.md` / `.cursorrules`），让编辑器在每次对话中自动加载项目规范；初始化 Python + Node.js 项目依赖；搭建目录骨架。

### Context

这一步决定了后续所有 AI 对话的质量。`CLAUDE.md` 是 AI 的"宪法"，每次对话自动读入。如果跳过这一步，每次对话都要重复解释约束，且 AI 会做出局部正确但全局不一致的决策（比如在 Phase D 里偷偷调用 PIL 做压缩测试）。

### Prompt

```
帮我完成以下几件事：

第一，在项目根目录创建 CLAUDE.md 和 .cursorrules 约束文件，内容包含：

"唯一功能真源"：声明功能只依据 docs/ 下三份文档（系统架构设计、压缩决策引擎、数据契约），文档未定义的功能不进入实现。

"铁律"：七条不可推翻的约束——决策与执行分离、Phase D 纯函数、只降不升、面积感知、临时文件强制清理、输入输出契约不可下游修改、收敛失败不静默输出。

"代码目录职责"：
- backend/app/main.py：FastAPI 入口
- backend/app/config/：配置加载（pydantic-settings + YAML）
- backend/app/api/：REST 路由 handler
- backend/app/contracts/：所有 Phase 间的数据类型定义（Pydantic 模型）
- backend/app/storage/：文件存储抽象（本地 + S3 可选）
- backend/app/pipeline/split.py：Phase 4 拆页
- backend/app/pipeline/extract.py：Phase 5 元素提取
- backend/app/pipeline/classify.py：Phase 6 页面分类
- backend/app/pipeline/preprocess.py：Phase 7 字体子集化 + 固定开销
- backend/app/pipeline/decide.py：Phase 8 压缩决策（纯函数）
- backend/app/pipeline/compress.py：Phase 9 压缩执行
- backend/app/pipeline/assemble.py：Phase 10 PDF 重组
- backend/app/pipeline/orchestrator.py：Phase 11 流水线编排 + 收敛循环
- backend/app/queue/：任务队列（Celery/RQ）
- frontend/：Next.js 前端

"验证命令"：
- cd backend && pytest tests/ -v
- cd backend && python -m app.main --check
- 铁律 grep 检查（见附录 A）

第二，初始化后端项目：
- Python 3.11+
- 依赖：fastapi, uvicorn, pydantic, pydantic-settings, pymupdf, pillow, celery, redis, boto3, python-multipart, pytest, httpx
- 创建 requirements.txt

第三，初始化前端项目：
- Next.js 14 + TypeScript + Tailwind CSS
- 依赖：react, next, tailwindcss, axios, react-dropzone, zustand
- 创建 package.json

第四，创建以上所有目录，每个目录放一个 .gitkeep 文件。

第五，创建 config.yaml 模板（所有密钥字段留空并注明通过环境变量注入）。

第六，创建 skills/SKILLS.md，至少包含四个 Skill：系统架构师、PDF 处理专家、Python 后端专家、AWS 部署专家。

第七，创建 .gitignore（含 config.local.yaml、*.pdf、tmp/、node_modules/、__pycache__/）。
```

### 预期产出

- `CLAUDE.md` + `.cursorrules`
- `backend/requirements.txt` + 目录骨架
- `frontend/package.json` + 目录骨架
- `config.yaml` 模板
- `skills/SKILLS.md`
- `.gitignore`

---

## Phase 1 — 三份真源文档

### 目标

在写任何代码之前，用文档固化设计意图。三份文档是整个系统的"法律"，是后续所有代码的唯一依据。

### 说明

创建 `docs/` 目录下的三份文档：

- `docs/系统架构设计.md`：整体架构、Phase 划分、数据流图、部署拓扑
- `docs/压缩决策引擎.md`：Phase D 的详细规格（三主控变量、面积修正、像素天花板、预算分配、收敛算法）
- `docs/数据契约.md`：所有 Phase 间传递的数据类型完整定义

### Prompt

```
请阅读 CLAUDE.md 中的铁律，然后帮我起草 docs/ 目录下的三份文档。

第一份：docs/系统架构设计.md
- 第 1 章：系统定位与用户价值
- 第 2 章：三层物理架构（前端/后端/存储）
- 第 3 章：Phase 划分与数据流总览（附流水线图）
- 第 4 章：用户 review 断点的设计
- 第 5 章：错误处理与临时文件清理策略
- 第 6 章：AWS EC2 部署拓扑（含 Nginx / systemd / EBS / CloudWatch）

第二份：docs/压缩决策引擎.md（这是最详细的一份）
- 第 1 章：设计哲学（三主控变量框架）
- 第 2 章：输入契约（DecisionInput）
- 第 3 章：输出契约（CompressionPlan）
- 第 4 章：核心公式（aggressiveness / area_sensitivity / pixel_ceiling_ratio 的推导）
- 第 5 章：渲染图（Raster）压缩策略
  - 5.1 参数维度
  - 5.2 基准参数表
  - 5.3 面积修正
  - 5.4 透明通道处理
  - 5.5 体积估算公式
- 第 6 章：矢量图（Vector）压缩策略
  - 6.1 三种策略：keep / simplify / rasterize
  - 6.2 复杂度判断（控制点数 + area_ratio）
  - 6.3 光栅化决策（不可逆，需用户确认）
- 第 7 章：预算分配算法（按视觉重要性加权）
- 第 8 章：Binary Search 收敛循环
- 第 9 章：所有可配置常量（对应 config.yaml）

第三份：docs/数据契约.md
- 第 1 章：类型定义总览
- 第 2 章：RawPage（Phase 4 输出）
- 第 3 章：PageElements + fonts_used + metadata（Phase 5 输出）
- 第 4 章：ClassifiedPage（Phase 6 输出）
- 第 5 章：PreprocessResult（Phase 7 输出）
- 第 6 章：CompressionPlan（Phase 8 输出）
- 第 7 章：CompressionResult（Phase 9 输出）
- 第 8 章：类型链路图（谁流向谁）

铁律要求：所有类型使用 Pydantic 模型定义，不能用裸 dict。
```

### 预期产出

- `docs/系统架构设计.md`
- `docs/压缩决策引擎.md`
- `docs/数据契约.md`

---

## Phase 2 — 基础设施层（Config + Storage + Queue）

### 目标

搭建系统的物理基础：配置加载、文件存储抽象、Redis + 任务队列、临时文件生命周期管理。

### Context

PDF 处理是**长时间任务**（大文件可能几十秒），必须异步化。同时**临时文件必须严格管理**——不能因为任务失败就留下用户 PDF 在服务器上。

### Prompt

```
请阅读 docs/系统架构设计.md 第 5 章（错误处理与临时文件清理），然后实现基础设施层。

一、backend/app/config/settings.py
使用 pydantic-settings，从 config.yaml 和环境变量加载：
- App（app_env: dev/prod, log_level）
- Storage（tmp_dir 路径, retention_seconds 默认 300 秒）
- Redis（host, port, db）
- Compression（所有压缩相关的可调参数，见附录 B）
- AWS（可选：s3_bucket, region，未配置则不启用 S3）

二、backend/app/storage/local.py
本地存储抽象：
- save_upload(file_bytes, extension) → file_id: str
- get_path(file_id) → Path
- delete(file_id)
- cleanup_expired()：扫描 tmp_dir，删除超过 retention_seconds 的文件
所有文件保存在 tmp_dir/<file_id>.<ext>，file_id 是 UUID v4。

三、backend/app/storage/s3.py（可选）
S3 抽象，接口与 local.py 相同。settings.aws.s3_bucket 为空时不启用。

四、backend/app/storage/__init__.py
根据 config 自动选择 local 或 s3，暴露统一的 get_storage() 工厂。

五、backend/app/queue/celery_app.py
Celery 配置：Redis broker + Redis result backend。

六、backend/app/queue/tasks.py
先只定义一个 stub 任务 compress_pdf_task(file_id, options)，暂时只 sleep 3 秒返回 dummy result。真实逻辑在 Phase 11 填入。

七、在 backend/app/main.py 的 startup event 中：
- 启动定时清理任务（每 60 秒调用 storage.cleanup_expired()）
- 打印当前 config 概况（隐藏敏感字段）

验证：
- pytest backend/tests/test_storage.py（保存、读取、过期清理）
- celery -A app.queue.celery_app worker --loglevel=info 能正常启动
```

### 预期产出

- `backend/app/config/settings.py`
- `backend/app/storage/{local.py, s3.py, __init__.py}`
- `backend/app/queue/{celery_app.py, tasks.py}`
- `config.yaml` 已填充默认值

---

## Phase 3 — 核心数据契约层

### 目标

**在写任何 Phase 4-10 的业务代码之前**，先把它们之间流转的所有数据类型定义清楚。这是整个系统最重要的一个 Phase——它决定了后面所有 Phase 能不能干净地拼在一起。

### Context

对应 QuantSaaS 手册中的 `internal/quant/types.go`。所有 Phase 间的接口在这里定死，后续任何 Phase 只能消费定义好的类型，不能私自扩展或修改字段。

### Prompt

```
请阅读 docs/数据契约.md，然后在 backend/app/contracts/ 下实现所有数据类型定义。

铁律：全部使用 Pydantic BaseModel，禁止使用裸 dict 或 dataclass。

一、backend/app/contracts/geometry.py
基础几何类型：
- BBox { x: float, y: float, w: float, h: float }
  - property area → float
  - method area_ratio(page_area: float) → float

二、backend/app/contracts/elements.py
Phase 5 输出的元素类型：
- RasterImage { data_ref: str, width: int, height: int, dpi: float, 
                color_space: str, has_alpha: bool, bbox: BBox }
  注：data_ref 是文件内引用（如 "img_001"），不直接存 bytes（避免 Pydantic 膨胀）
- VectorPath { path_data_ref: str, control_point_count: int, 
                stroke_color: Optional[str], fill_color: Optional[str], bbox: BBox }
- TextBlock { content: str, font_ref: str, font_size: float, bbox: BBox }
- XObject { ref_id: str, xobject_type: Literal["image", "form"], bbox: BBox }
- Annotation { anno_type: Literal["link", "bookmark", "comment", "form_field"], 
                target: str, bbox: BBox }

三、backend/app/contracts/pages.py
- RawPage { page_number: int, page_width: float, page_height: float, 
            page_bytes_ref: str }（Phase 4 输出）
- PageElements { page_number: int, page_width: float, page_height: float,
                 raster_images: List[RasterImage],
                 vector_paths: List[VectorPath],
                 text_blocks: List[TextBlock],
                 xobjects: List[XObject],
                 annotations: List[Annotation] }（Phase 5 输出）
- ClassifiedPage { page_number: int, page_type: Literal["photo_heavy","text_heavy","chart","mixed"],
                   dominant_element: Literal["raster","vector","text"],
                   raster_area_ratio: float, vector_area_ratio: float,
                   text_area_ratio: float,
                   complexity_score: float  # 0~1，用于分类不确定性
                 }（Phase 6 输出）

四、backend/app/contracts/fonts.py
- FontUsage { font_name: str, data_ref: str, chars_used: Set[str] }
- SubsetResult { font_name: str, original_bytes: int, subsetted_bytes: int, 
                  subsetted_data_ref: str }

五、backend/app/contracts/preferences.py
用户偏好：
- CompressionTarget = Literal["screen", "print", "email"]
- PageOverride { page_number: int, 
                 action: Literal["skip", "aggressive", "reclassify"],
                 new_type: Optional[str] = None }
- UserPreferences { target_size_mb: float, 
                    compression_target: CompressionTarget,
                    per_page_overrides: List[PageOverride] = [] }

六、backend/app/contracts/plan.py
Phase 8 输出：
- RasterPlan { image_index: int, area_ratio: float, 
                original_dpi: float, original_bytes: int,
                target_dpi: int, quality: int, max_dimension: int,
                output_format: Literal["jpeg", "png"],
                estimated_bytes: int }
- VectorPlan { path_group_index: int, area_ratio: float, original_bytes: int,
                strategy: Literal["keep", "simplify", "rasterize"],
                rasterize_dpi: Optional[int] = None,
                simplify_tolerance: Optional[float] = None }
- PagePlan { page_number: int, skip: bool, 
             raster_plans: List[RasterPlan], 
             vector_plans: List[VectorPlan] }
- CompressionPlan { raster_budget: int, pages: List[PagePlan],
                    round_number: int  # 第几轮循环 }

七、backend/app/contracts/result.py
Phase 9 输出：
- ImageResult { page_number: int, image_index: int, 
                compressed_bytes: int, compressed_data_ref: str,
                actual_dpi: float, actual_quality: int }
- CompressionResult { total_raster_bytes: int, per_image_results: List[ImageResult],
                       round_number: int }

八、backend/app/contracts/preprocess.py
Phase 7 输出：
- PerPageFixedBytes { page_number: int, vector_bytes: int, 
                      text_bytes: int, annotation_bytes: int }
- PreprocessResult { subsetted_fonts: List[SubsetResult],
                     total_fixed_bytes: int,
                     per_page_fixed_bytes: List[PerPageFixedBytes],
                     stripped_metadata_fields: List[str] }

九、backend/app/contracts/pipeline.py
- PipelineContext { file_id: str, tmp_workspace: str }（携带所有 Phase 共享的运行时上下文）
- PhaseError { phase: str, code: str, message: str, recoverable: bool }

十、编写契约测试 backend/tests/test_contracts.py
- 所有类型能 JSON round-trip（model_dump / model_validate）
- 关键字段的验证：quality ∈ [0, 100], target_dpi > 0, area_ratio ∈ [0, 1]

验证：
- pytest backend/tests/test_contracts.py -v
- python -c "from app.contracts import *; print('all imports ok')"
```

### 预期产出

- `backend/app/contracts/*.py`（8 个文件）
- `backend/tests/test_contracts.py`

### 关键检查

**这一 Phase 完成后，抓住一件事验证：把所有类型的字段列出来画一张 DAG。每个字段要么来自上一个 Phase 的输出，要么是新引入的。如果发现某个字段"凭空出现"或"来源不明"，说明契约设计有漏洞，回去补齐。**

---

## Phase 4 — PDF 拆页

### 目标

把上传的 PDF 拆解成独立的 `RawPage` 序列，每页的原始字节流被引用保存到临时工作区。

### Context

拆页是所有 PDF 处理的第一步，几乎不会出错。分离出来的目的是让 Phase 5 的元素提取可以逐页独立进行，任何一页解析失败也不会阻塞其他页。

### Prompt

```
请阅读 docs/数据契约.md 中 RawPage 的定义，然后实现 backend/app/pipeline/split.py。

功能：
- split_pdf(input_pdf_path: str, ctx: PipelineContext) → List[RawPage]

实现要求：
- 使用 PyMuPDF (fitz)
- 逐页读取，把每页的字节流保存到 ctx.tmp_workspace/pages/page_{N}.pdf
- 保存路径作为 RawPage.page_bytes_ref（相对路径）
- 提取每页的 page_width 和 page_height（PDF 用户单位，即 points）
- 页号从 1 开始（对齐 PDF 传统）

错误处理：
- 输入不是有效 PDF → raise PhaseError(phase="split", code="INVALID_PDF")
- 加密 PDF → raise PhaseError(code="ENCRYPTED_PDF", recoverable=False)
- 单页读取失败 → 记录 warning 但继续处理其他页，最终 raw_pages 数量可能少于原始页数

验证：
- 编写 tests/test_split.py
- 用一个 5 页测试 PDF 验证：返回 5 个 RawPage
- 每个 RawPage.page_bytes_ref 指向的文件确实存在且可读
- 加密 PDF 抛出正确的 PhaseError
```

### 输入 → 输出

- **输入**：`input_pdf_path` + `PipelineContext`
- **输出**：`List[RawPage]`

### 预期产出

- `backend/app/pipeline/split.py`
- `backend/tests/test_split.py`

---

## Phase 5 — 元素提取

### 目标

对每个 `RawPage` 做深度解析，提取出位图、矢量、文字、字体、XObject、注释。同时构建整个 PDF 级别的字体使用表（跨页累计字符集）。

### Context

这是整个流水线里**最复杂的一步**。PDF 的绘图指令有各种边界情况：嵌套的 XObject、CID 编码的字体、多种色彩空间、旋转过的图片。Prompt 里要明确让 AI 处理已知的坑。

### Prompt

```
请阅读 docs/数据契约.md 中 PageElements、FontUsage、Annotation 的定义，然后实现 backend/app/pipeline/extract.py。

主入口：
- extract_elements(raw_pages: List[RawPage], original_pdf_path: str, ctx: PipelineContext) 
  → Tuple[List[PageElements], Dict[str, FontUsage], Dict[str, Any]]
  返回：每页元素列表 + 全局字体表 + 全局元数据

实现要求（重点）：

一、位图提取
- 使用 PyMuPDF 的 page.get_images() + doc.extract_image()
- 检测 alpha 通道：SMask 存在 → has_alpha = True
- 反算 DPI：dpi = image_pixel_width / (bbox.w / 72)（bbox.w 单位是 points）
- 图片字节流保存到 ctx.tmp_workspace/images/img_{page}_{index}.bin
- data_ref = 相对路径

二、矢量路径提取
- 使用 page.get_drawings()
- 每个 drawing 包含 items（路径命令）
- 统计 control_point_count = 所有路径中控制点的总数
- 计算合并 bbox（所有路径的最小外接矩形）
- 路径数据用 pickle 序列化后保存到 vectors/vec_{page}_{index}.bin

三、文字块提取
- 使用 page.get_text("dict") 获取结构化文本
- 每个 block → 每个 line → 每个 span 展开
- 记录 font_ref = span["font"]（字体名字符串）
- 记录 chars_used：把每个 span 的字符加入全局 fonts_used[font_ref].chars_used

四、字体提取
- 遍历 doc 的所有页面，用 page.get_fonts() 收集字体清单
- 用 doc.extract_font(font_xref) 提取字体原始字节
- 保存到 fonts/font_{name}.ttf
- FontUsage.chars_used 在文字块提取阶段累计

五、XObject 提取
- 检测 page 上的 Form XObject（page.get_xobjects()）
- 递归展开一层：如果 XObject 内部含图片，也提取
- 标记 xobject_type = "image" 或 "form"

六、注释提取
- 超链接：page.get_links() → target = uri
- 书签：doc.get_toc() → 每个 entry
- 表单字段：page.widgets()
- 文本注释：page.annots()

七、元数据提取
- doc.metadata（title, author, creator, producer, creation_date）
- 返回为 Dict[str, Any]

八、错误容忍
- 单页提取失败 → 记录 warning，该页返回空 PageElements（不阻塞后续）
- 字体提取失败 → 记录 warning，该字体标记 data_ref=None（后续 Phase 7 遇到时跳过子集化）

九、编写 tests/test_extract.py
- 用真实的作品集样本（准备 3 份：photo_heavy / vector_heavy / mixed）
- 断言：raster_images 数量合理、vector_paths.control_point_count > 0、text_blocks 非空、fonts_used 至少 1 个
- 断言：所有 data_ref 指向的文件都真实存在

验证：
- pytest backend/tests/test_extract.py -v
```

### 输入 → 输出

- **输入**：`List[RawPage]` (Phase 4) + 原 PDF 路径 + `PipelineContext`
- **输出**：`List[PageElements]` + `Dict[str, FontUsage]` + `metadata: Dict`

### 预期产出

- `backend/app/pipeline/extract.py`
- `backend/tests/test_extract.py`
- `tests/fixtures/`（放测试用的作品集样本）

---

## Phase 6 — 页面分类器

### 目标

对每一页判断类型（photo_heavy / text_heavy / chart / mixed），输出 `ClassifiedPage`。V1 使用启发式规则（面积占比 + 元素数量），V2 可替换为 AI 分类模型（接口不变）。

### Context

分类结果会影响 Phase 8 的基准参数选择和用户 review 界面的默认显示。**分类不需要百分百准确**——用户会在 review 界面手动修正。目标是让分类器"大部分时候是对的"，减少用户手动修改的负担。

### Prompt

```
请阅读 docs/数据契约.md 中 ClassifiedPage 的定义，然后实现 backend/app/pipeline/classify.py。

主入口：
- classify_pages(pages: List[PageElements]) → List[ClassifiedPage]

V1 启发式规则（先实现这个）：

对每一页计算：
  page_area          = page_width × page_height
  raster_area        = sum(img.bbox.area for img in raster_images)
  vector_area        = sum(v.bbox.area for v in vector_paths)
  text_area          = sum(t.bbox.area for t in text_blocks)
  vector_complexity  = sum(v.control_point_count for v in vector_paths)

分类规则（按优先级）：

1. photo_heavy:
   raster_area / page_area > 0.5
   AND raster 中至少一张 dpi > 100
   dominant_element = "raster"

2. chart（矢量为主的分析图/CAD/线稿）:
   vector_area / page_area > 0.3
   AND vector_complexity > 5000
   AND raster_area / page_area < 0.3
   dominant_element = "vector"

3. text_heavy:
   text_blocks 数量 > 15
   AND raster_area / page_area < 0.15
   AND vector_complexity < 2000
   dominant_element = "text"

4. mixed:
   以上都不满足
   dominant_element = 三者面积占比最大的那个

complexity_score 计算：
  = 分类边界的模糊程度（0 = 非常明确，1 = 非常模糊）
  用于前端提示用户："这一页分类不太确定，请手动确认"
  
  例如：raster_area / page_area = 0.48（接近 photo_heavy 的 0.5 阈值）
       → complexity_score = 1.0 - abs(0.48 - 0.5) × 10 = 0.8
       （高分表示需要用户注意）

V2 接口预留（本 Phase 不实现，只留 hook）：
- 在 classify.py 顶部加注释：
  # V2: replace HeuristicClassifier with AIClassifier
  # AIClassifier 应实现 classify_pages(pages) → List[ClassifiedPage]
  # 输入输出与启发式版本完全一致

编写 tests/test_classify.py：
- 用 5 页样本测试：至少 4 页分类正确
- complexity_score 在合理范围（0~1）
- 每页至少一个 dominant_element

验证：
- pytest backend/tests/test_classify.py
```

### 输入 → 输出

- **输入**：`List[PageElements]` (Phase 5)
- **输出**：`List[ClassifiedPage]`

### 预期产出

- `backend/app/pipeline/classify.py`
- `backend/tests/test_classify.py`

---

## Phase 7 — 确定性预处理（字体子集化 + 固定开销计算）

### 目标

处理所有**结果确定的**元素——字体子集化、元数据清理，同时计算 Phase 8 需要的**固定开销**（矢量 + 文字 + 子集化后字体 + 元数据 + PDF 结构开销的总字节数）。

### Context

**为什么这一 Phase 排在 Phase 8 之前？** 因为字体子集化的结果是完全确定的（同样的字体 + 同样的字符集，输出永远一样），把它前置可以：
1. 让 Phase 8 的"位图预算"是一个精确数字（`target_size - fixed_overhead`），而不是估算
2. 二分搜索的搜索空间从"整个 PDF 大小"缩小到"位图部分大小"，收敛更快

这就是"把确定性的操作前置，把不确定性留给循环"的架构原则。

### Prompt

```
请阅读 docs/压缩决策引擎.md 第 7 章（预算分配），然后实现 backend/app/pipeline/preprocess.py。

主入口：
- preprocess(pages: List[PageElements], 
             fonts: Dict[str, FontUsage],
             metadata: Dict,
             user_prefs: UserPreferences,
             ctx: PipelineContext) → PreprocessResult

实现步骤：

一、字体子集化
- 依赖：fonttools（TTFont, subset）
- 对每个 FontUsage：
  - 加载原始字体文件
  - 用 fontTools.subset.Subsetter，只保留 chars_used 中的字符
  - 输出到 ctx.tmp_workspace/fonts_subsetted/font_{name}.ttf
- 记录 SubsetResult（original_bytes, subsetted_bytes, subsetted_data_ref）

  错误处理：
  - 无法加载的字体 → 保留原字体，subsetted_bytes = original_bytes
  - 字符集为空 → 跳过整个字体（可能是仅装饰性引用）

二、元数据清理
- 根据 user_prefs 决定清理哪些字段
- 默认：清理 author, creator（隐私考虑），保留 title
- 记录 stripped_metadata_fields

三、固定开销计算
对每一页计算：
  vector_bytes    = sum(size of vector_paths 的序列化数据)
  text_bytes      = sum(len(t.content.encode('utf-8')) for t in text_blocks) × 1.5  # 包含字体引用等结构开销
  annotation_bytes = 对每个 annotation 估算 100-500 字节

全局固定开销：
  total_fixed_bytes = 
    sum(per_page_fixed_bytes) 
    + sum(subsetted_bytes for all fonts)
    + estimated_metadata_bytes（清理后估算 500-2000 字节）
    + estimated_pdf_structure_overhead（页数 × 200 字节 + 500）

  # 这个估算不需要非常精确，误差 ±10% 由 Phase 8/9 的收敛循环兜住

编写 tests/test_preprocess.py：
- 断言字体子集化确实减小了字体体积（对于典型中文字体应 > 80% 缩减）
- 断言 total_fixed_bytes = 各分量之和
- 断言 subsetted 字体文件真实存在

验证：
- pytest backend/tests/test_preprocess.py
```

### 输入 → 输出

- **输入**：`List[PageElements]` (Phase 5) + `Dict[str, FontUsage]` + `metadata` + `UserPreferences`
- **输出**：`PreprocessResult`

### 预期产出

- `backend/app/pipeline/preprocess.py`
- `backend/tests/test_preprocess.py`

---

## Phase 8 — 压缩决策引擎（核心 ⭐）

### 目标

**整个系统最复杂、最关键的一步。** 根据分类结果、用户偏好、固定开销预算，生成每张位图和每组矢量的精确压缩参数。**纯函数——不做任何实际压缩、不读写文件、不调用图像处理库。**

### Context

Phase 8 的核心是三个主控变量的推导逻辑 + area_ratio 修正 + pixel_ceiling 截断 + 预算分配 + Binary Search 反馈。所有可调参数从 config.yaml 读取，代码里不出现魔法数字。

### Prompt

````
请仔细阅读 docs/压缩决策引擎.md 全文，然后实现 backend/app/pipeline/decide.py。

铁律再次强调：
- 本模块是纯函数，不能有 file I/O、网络调用、图像处理库调用
- 只能做数学计算和 config 读取
- 相同输入必须产出完全相同的输出（无随机性）
- 所有阈值和系数从 settings.compression 读取，不许硬编码

主入口：
- decide_compression(
    classified: List[ClassifiedPage],
    pages: List[PageElements],
    user_prefs: UserPreferences,
    preprocess: PreprocessResult,
    previous_result: Optional[CompressionResult],
    round_number: int,
    config: CompressionConfig
  ) → CompressionPlan

实现步骤：

一、计算位图预算
```python
target_bytes = user_prefs.target_size_mb * 1024 * 1024
raster_budget = target_bytes - preprocess.total_fixed_bytes

if raster_budget <= 0:
    raise PhaseError(
        phase="decide",
        code="TARGET_TOO_SMALL",
        message=f"固定开销 {preprocess.total_fixed_bytes} 已超过目标 {target_bytes}",
        recoverable=False
    )
```

二、确定基础激进度（第 1 轮）或调整激进度（第 2+ 轮）
```python
if round_number == 1:
    # 第 1 轮：从用户预设推导
    base_aggressiveness = config.presets[user_prefs.compression_target].aggressiveness
else:
    # 第 2+ 轮：根据上一轮 gap 调整
    prev_total = previous_result.total_raster_bytes
    gap_ratio = (prev_total - raster_budget) / raster_budget
    
    if gap_ratio > 0.3:
        # 超标严重
        base_aggressiveness = min(1.0, prev_aggressiveness + 0.15)
    elif gap_ratio > 0:
        # 轻微超标
        base_aggressiveness = min(1.0, prev_aggressiveness + 0.08)
    elif gap_ratio < -0.15:
        # 浪费过多
        base_aggressiveness = max(0.0, prev_aggressiveness - 0.08)
    else:
        base_aggressiveness = prev_aggressiveness  # 已达标，通常不会走到这里
```

三、对每一页决策

跳过 skip 页：
```python
for page_num, override in user_prefs.per_page_overrides:
    if override.action == "skip":
        pages_map[page_num].skip = True
```

对 non-skip 页决策每个位图：

```python
def decide_raster(image: RasterImage, page: PageElements, 
                  classified: ClassifiedPage, base_aggr: float, config):
    area_ratio = image.bbox.area / (page.page_width * page.page_height)
    
    # 面积修正
    eff_aggr = base_aggr + config.area_sensitivity * (1.0 - area_ratio)
    eff_aggr = clamp(eff_aggr, 0.0, 1.0)
    
    # 从 eff_aggr 推导基础参数
    quality = int(lerp(config.quality_ceiling, config.quality_floor, eff_aggr))
    dpi     = lerp(config.dpi_ceiling, config.dpi_floor, eff_aggr)
    
    # 只降不升
    target_dpi = min(dpi, image.dpi)
    
    # 像素天花板
    display_w_inches = image.bbox.w / 72
    display_h_inches = image.bbox.h / 72
    max_w = display_w_inches * target_dpi * config.pixel_ceiling_ratio
    max_h = display_h_inches * target_dpi * config.pixel_ceiling_ratio
    
    if image.width > max_w:
        # 原图像素已超天花板，进一步降 DPI
        target_dpi = min(target_dpi, max_w / display_w_inches)
    
    # 输出格式
    output_format = "png" if image.has_alpha else "jpeg"
    
    # 体积估算
    scale = (target_dpi / image.dpi) ** 2
    pixel_count = image.width * image.height * scale
    if output_format == "jpeg":
        bytes_per_pixel = 0.04 + (quality / 100) * 0.12
    else:
        bytes_per_pixel = 0.35
    estimated_bytes = int(pixel_count * bytes_per_pixel)
    
    return RasterPlan(...)
```

对每组矢量决策：

```python
def decide_vector(vector_group: VectorPath, page: PageElements, config):
    area_ratio = vector_group.bbox.area / (page.page_width * page.page_height)
    cp_count = vector_group.control_point_count
    
    # 面积过小 → 无条件 keep
    if area_ratio < config.vector_ignore_area_ratio:
        return VectorPlan(strategy="keep", ...)
    
    # 三级复杂度分类
    if cp_count < config.vector_simplify_threshold:
        return VectorPlan(strategy="keep", ...)
    elif cp_count < config.vector_rasterize_threshold:
        tolerance = 0.5 if area_ratio > 0.6 else 1.0
        return VectorPlan(strategy="simplify", simplify_tolerance=tolerance, ...)
    else:
        # 光栅化
        if area_ratio > 0.6:
            r_dpi = 200
        elif area_ratio > 0.3:
            r_dpi = 150
        else:
            r_dpi = 96
        return VectorPlan(strategy="rasterize", rasterize_dpi=r_dpi, ...)
```

四、预算校验与再平衡

```python
# 计算所有 estimated_bytes 之和
total_estimated = sum(...)

if total_estimated > raster_budget * 1.1:
    # 估算已经超预算 → 全局降低 quality 直到估算总和 ≤ raster_budget
    # 使用简单的线性缩放：quality_i *= scale_factor
    scale = raster_budget / total_estimated
    for plan in all_raster_plans:
        plan.quality = max(config.quality_floor, int(plan.quality * scale))
        # 重新计算 estimated_bytes
```

五、聚合返回 CompressionPlan

六、编写 tests/test_decide.py

必须包含以下测试用例：

1. **纯函数验证**：相同输入调用 3 次，输出完全相等
2. **只降不升**：所有 target_dpi ≤ original_dpi，所有 quality ≤ quality_ceiling
3. **skip 生效**：override action="skip" 的页面 raster_plans 为空
4. **面积修正生效**：给同一图片不同 area_ratio 输入，area_ratio 小的 quality 更低
5. **像素天花板生效**：给一张原始 6000x4000 但 bbox 显示只有 1 英寸的图，target_dpi 应远低于 base_dpi
6. **矢量策略切换**：control_point < 10000 → keep；10000-100000 → simplify；> 100000 → rasterize
7. **透明通道 → PNG**：has_alpha=True 的图 output_format 一定是 "png"
8. **预算守恒**：sum(estimated_bytes) ≤ raster_budget * 1.1
9. **收敛调整**：模拟上一轮超标 30%，本轮 aggressiveness 应上升 0.15
10. **目标过小报错**：raster_budget < 0 时抛出 TARGET_TOO_SMALL

验证：
- pytest backend/tests/test_decide.py -v
- grep 检查：backend/app/pipeline/decide.py 内不含 open()/requests/PIL/cv2/fitz
````

### 输入 → 输出

- **输入**：`List[ClassifiedPage]` + `List[PageElements]` + `UserPreferences` + `PreprocessResult` + `Optional[CompressionResult]`（上一轮结果）+ `round_number` + `CompressionConfig`
- **输出**：`CompressionPlan`

### 预期产出

- `backend/app/pipeline/decide.py`
- `backend/tests/test_decide.py`

### 关键检查

**这个 Phase 完成后必须通过一次专项 code review。逐条检查铁律：**
- Grep 无 file I/O
- Grep 无图像库导入
- 所有阈值来自 config
- 输出的字段全部在 Phase 3 定义过
- 相同输入产生完全相同输出（跑 3 次测试）

---

## Phase 9 — 压缩执行 + 收敛循环

### 目标

按 `CompressionPlan` 真正压缩每张位图和处理每组矢量，收集实际压缩结果。**这里才是唯一允许调用 PIL/OpenCV 的地方。**

### Context

Phase 9 是"施工队"，只按图纸干活。同时，`orchestrator.py` 负责把 Phase 8 和 Phase 9 组织成收敛循环。

### Prompt

```
请阅读 docs/压缩决策引擎.md 第 8 章（收敛循环），然后实现 backend/app/pipeline/compress.py 和 backend/app/pipeline/orchestrator.py（收敛部分）。

一、backend/app/pipeline/compress.py

主入口：
- execute_compression(plan: CompressionPlan, 
                     pages: List[PageElements], 
                     ctx: PipelineContext) → CompressionResult

对每个 RasterPlan：
  加载原图（从 image.data_ref）
  按 plan.target_dpi 计算目标像素尺寸
  Resize（使用 Pillow 的 Image.thumbnail 或 resize with LANCZOS）
  按 plan.output_format 编码：
    - jpeg: quality=plan.quality, optimize=True
    - png: optimize=True, compress_level=9
  保存到 ctx.tmp_workspace/compressed/img_{page}_{index}.{ext}
  记录 actual_bytes = os.path.getsize()

对每个 VectorPlan：
  - keep: 不动，actual_bytes = original_bytes
  - simplify: 用 shapely 或自实现 Douglas-Peucker 简化
  - rasterize: 用 PyMuPDF 将矢量区域渲染成 PNG，再走 raster 逻辑

聚合返回 CompressionResult。

错误处理：
- 单张图压缩失败 → 记录 warning，用原图凑数（compressed_bytes = original_bytes）
- 整体成功率 < 80% → 抛出 PhaseError

二、backend/app/pipeline/orchestrator.py

主入口：
- run_compression_pipeline(
    input_pdf_path: str,
    user_prefs: UserPreferences,
    ctx: PipelineContext,
    on_progress: Callable[[str, float], None]
  ) → str  # 返回最终 PDF 路径

流程：
  1. on_progress("splitting", 0.05)
     raw_pages = split_pdf(...)
  2. on_progress("extracting", 0.15)
     page_elements, fonts, metadata = extract_elements(...)
  3. on_progress("classifying", 0.35)
     classified = classify_pages(page_elements)
     
     # 【用户 review 断点】
     # 这里 orchestrator 返回一个 review_context，暂停流水线
     # 前端用户确认/修改分类后，再调用 continue_pipeline(...)
     
  4. on_progress("preprocessing", 0.50)
     preprocess = preprocess(...)
  5. 收敛循环 on_progress("converging", 0.55 → 0.90)
     previous_result = None
     for round_num in 1..config.max_convergence_rounds:
         plan = decide_compression(..., previous_result, round_num, config)
         result = execute_compression(plan, ...)
         
         total = result.total_raster_bytes + preprocess.total_fixed_bytes
         target = user_prefs.target_size_mb * 1024 * 1024
         gap_ratio = (total - target) / target
         
         if -config.convergence_tolerance <= gap_ratio <= 0:
             # 达标（略保守）
             break
         if 0 < gap_ratio <= config.convergence_tolerance / 2:
             # 微超标，也接受
             break
         
         previous_result = result
         on_progress("converging", 0.55 + 0.35 * (round_num / max_rounds))
     else:
         # 5 轮未收敛
         if abs(gap_ratio) > 0.15:
             raise PhaseError(
                 phase="orchestrator",
                 code="CONVERGENCE_FAILED",
                 message=f"目标 {target/1e6:.1f}MB 无法达成，最优结果 {total/1e6:.1f}MB"
             )
  6. on_progress("assembling", 0.95)
     final_path = assemble_pdf(...)  # Phase 10
  7. on_progress("done", 1.0)
     return final_path

三、【用户 review 断点】的实现策略

因为流水线要暂停等用户，orchestrator 拆成两个函数：
  - run_until_classify(...) → 返回 (classified, session_id)
    把中间状态（page_elements、fonts、metadata、classified）序列化保存到 tmp_workspace/session.pickle
  - resume_after_review(session_id, user_modified_classified, user_prefs) 
    → 从 pickle 加载状态，继续 Phase 7-10

四、编写 tests/test_compress.py 和 tests/test_orchestrator.py
- 用一份中等复杂度作品集端到端测试
- 断言：最终 PDF 大小在目标 ±15% 之内
- 断言：至少 90% 的样本在 5 轮内收敛
- 断言：收敛失败时抛出正确的 PhaseError

验证：
- pytest backend/tests/test_compress.py -v
- pytest backend/tests/test_orchestrator.py -v
- 端到端 smoke test：50MB 作品集 → 目标 10MB → 实际 8.5-11.5MB
```

### 输入 → 输出

- **Phase 9 (compress.py)**：`CompressionPlan` + `List[PageElements]` → `CompressionResult`
- **Orchestrator**：`input_pdf_path` + `UserPreferences` → 最终 PDF 路径

### 预期产出

- `backend/app/pipeline/compress.py`
- `backend/app/pipeline/orchestrator.py`
- `backend/tests/test_compress.py`
- `backend/tests/test_orchestrator.py`

---

## Phase 10 — PDF 重组与输出

### 目标

把压缩后的位图、简化/光栅化后的矢量、不变的文字、子集化后的字体、注释、书签全部拼装成一个新的 PDF。

### Context

这是流水线的最后一环。核心难点是**保持位置和层级关系**——压缩后的图要放回原来的 bbox，文字要保持在原来的位置，超链接要指向原来的目标。

### Prompt

```
请实现 backend/app/pipeline/assemble.py。

主入口：
- assemble_pdf(
    pages: List[PageElements],
    plans: CompressionPlan,
    results: CompressionResult,
    preprocess: PreprocessResult,
    metadata: Dict,
    ctx: PipelineContext
  ) → str  # 返回最终 PDF 路径

实现要求：

使用 PyMuPDF (fitz) 逐页构造新 PDF：

一、页面初始化
- new_doc = fitz.open()
- 对每一页：
  - new_page = new_doc.new_page(width=page.page_width, height=page.page_height)

二、放置位图
- 对每个 RasterPlan 及其 ImageResult：
  - 加载压缩后的图片 bytes
  - new_page.insert_image(rect=plan.bbox_as_rect, stream=compressed_bytes)

三、放置矢量
- keep 策略：从原始 pickle 加载 path_data，用 new_page.draw_* 系列 API 重绘
- simplify 策略：用简化后的路径重绘
- rasterize 策略：把光栅化后的 PNG 当位图插入（走 insert_image）

四、放置文字
- 使用 new_page.insert_text(bbox_position, text, fontname=..., fontsize=...)
- 字体使用子集化后的 TTF：用 new_doc.insert_font(fontname, fontfile=subsetted_path)

五、放置注释
- 超链接：new_page.insert_link(dict-form Link)
- 书签：new_doc.set_toc(toc_list)（在所有页处理完后统一设置）

六、设置元数据
- new_doc.set_metadata(cleaned_metadata)

七、保存
- output_path = ctx.tmp_workspace / "output.pdf"
- new_doc.save(str(output_path), garbage=4, clean=True, deflate=True, deflate_images=False)
  # deflate_images=False 因为我们的图片已经压缩过，重复压缩浪费 CPU
  # garbage=4 清理未引用对象
  # clean=True 清理冗余内容流

八、错误处理
- 保存失败 → PhaseError(code="ASSEMBLY_FAILED")
- 输出文件大小与预期偏离 > 30% → 记录 warning（可能表示重组过程中 PyMuPDF 做了意外的重编码）

九、编写 tests/test_assemble.py
- 断言输出 PDF 页数正确
- 断言输出 PDF 能被 fitz.open() 重新打开
- 断言输出 PDF 的元数据字段符合预期
- 断言书签和超链接保留
- 端到端：走完整流水线，最终 PDF 用 Adobe Reader / macOS Preview 目视验证

验证：
- pytest backend/tests/test_assemble.py -v
```

### 输入 → 输出

- **输入**：`List[PageElements]` + `CompressionPlan` + `CompressionResult` + `PreprocessResult` + `metadata`
- **输出**：最终 PDF 文件路径

### 预期产出

- `backend/app/pipeline/assemble.py`
- `backend/tests/test_assemble.py`

---

## Phase 11 — 任务编排层（Celery + 进度反馈）

### 目标

把 Phase 4-10 的完整流水线包装为一个 Celery 任务，前端可以异步触发 + 轮询进度。同时实现用户 review 断点的会话管理。

### Prompt

```
请实现 backend/app/queue/tasks.py 的真实版本，替换 Phase 2 的 stub。

一、backend/app/queue/tasks.py

@celery_app.task(bind=True)
def compress_pdf_task(self, file_id: str, user_prefs_dict: dict):
    """
    完整流水线任务，从 Phase 4 跑到分类完成。
    分类完成后：
      - 保存 session 到 Redis（key = task_id, TTL = 30 min）
      - 更新任务状态为 "AWAITING_REVIEW"
      - 前端轮询看到这个状态，展示分类 review 界面
    """
    user_prefs = UserPreferences(**user_prefs_dict)
    ctx = PipelineContext(file_id=file_id, tmp_workspace=...)
    
    def on_progress(stage, percent):
        self.update_state(state="PROGRESS", meta={"stage": stage, "percent": percent})
    
    try:
        # 前半段：Phase 4-6
        classified, session_data = run_until_classify(file_id, user_prefs, ctx, on_progress)
        
        # 保存 session
        redis.setex(f"session:{self.request.id}", 1800, pickle.dumps(session_data))
        
        # 返回 AWAITING_REVIEW 状态
        return {
            "status": "AWAITING_REVIEW",
            "classified": [c.model_dump() for c in classified],
            "session_id": self.request.id
        }
    except PhaseError as e:
        self.update_state(state="FAILURE", meta={"error": e.model_dump()})
        raise

@celery_app.task(bind=True)
def resume_compression_task(self, session_id: str, modified_classified: list, user_prefs_dict: dict):
    """
    用户 review 之后继续的任务。
    """
    session_data = pickle.loads(redis.get(f"session:{session_id}"))
    user_prefs = UserPreferences(**user_prefs_dict)
    classified = [ClassifiedPage(**c) for c in modified_classified]
    
    def on_progress(stage, percent):
        self.update_state(state="PROGRESS", meta={"stage": stage, "percent": percent})
    
    try:
        output_path = resume_after_review(session_data, classified, user_prefs, on_progress)
        # 上传到临时下载区
        download_id = storage.save_from_path(output_path)
        # 清理 tmp workspace
        cleanup_workspace(session_data.tmp_workspace)
        redis.delete(f"session:{session_id}")
        
        return {
            "status": "SUCCESS",
            "download_id": download_id,
            "final_size_mb": os.path.getsize(output_path) / 1e6
        }
    except PhaseError as e:
        self.update_state(state="FAILURE", meta={"error": e.model_dump()})
        raise

二、进度回调设计

不同 Phase 的进度分配：
  Phase 4 (split):        0% → 10%
  Phase 5 (extract):     10% → 30%
  Phase 6 (classify):    30% → 40%
  【AWAITING_REVIEW】     40%（等待用户）
  Phase 7 (preprocess):  40% → 50%
  Phase 8-9 (converge):  50% → 90%（每轮增加 8%）
  Phase 10 (assemble):   90% → 100%

三、失败清理
- 任务失败时：调用 cleanup_workspace(ctx.tmp_workspace)
- 30 分钟 review 超时：定时任务扫描过期 session，清理对应的 tmp_workspace

四、编写 tests/test_tasks.py
- 用 Celery eager mode 测试完整任务链路
- 模拟用户 review 修改分类后继续
- 模拟超时清理

验证：
- pytest backend/tests/test_tasks.py -v
- 手动测试：启动 celery worker + FastAPI，用 curl 触发一次完整流程
```

### 输入 → 输出

- **Celery 任务输入**：`file_id` + `user_prefs_dict`
- **状态输出**：`PROGRESS { stage, percent }` / `AWAITING_REVIEW { classified, session_id }` / `SUCCESS { download_id, final_size_mb }` / `FAILURE { error }`

### 预期产出

- `backend/app/queue/tasks.py`（完整版）
- `backend/tests/test_tasks.py`

---

## Phase 12 — REST API 层

### Prompt

```
请实现 backend/app/api/ 下的 REST 路由。

一、backend/app/api/upload.py
POST /api/v1/upload
- 接收 multipart/form-data
- 保存到 storage，返回 file_id
- 大小限制：500MB
- 类型验证：必须是 application/pdf

二、backend/app/api/compress.py
POST /api/v1/compress
- 请求体：{ file_id, target_size_mb, compression_target }
- 触发 compress_pdf_task.delay(...)，返回 task_id

GET /api/v1/tasks/:task_id
- 查询任务状态
- 返回：
  { state: "PENDING" | "PROGRESS" | "AWAITING_REVIEW" | "SUCCESS" | "FAILURE",
    meta: {...} }

POST /api/v1/tasks/:task_id/resume
- 请求体：{ session_id, modified_classified: [...], user_prefs: {...} }
- 触发 resume_compression_task.delay(...)
- 返回新的 task_id（用于跟踪后半段任务）

三、backend/app/api/download.py
GET /api/v1/download/:download_id
- 返回压缩后的 PDF 文件（Content-Disposition: attachment）
- 下载后 5 分钟从 storage 删除

四、backend/app/api/health.py
GET /api/v1/health
- 返回 { status: "ok", redis_connected: bool, storage_writable: bool }

五、CORS 配置
- 允许来源：从 config 读取（dev: localhost:3000, prod: 你的前端域名）

六、错误处理中间件
- 统一处理 PhaseError → 转 HTTP 4xx/5xx + JSON body
- 未捕获异常 → 500，log 完整堆栈但不返回给用户

七、编写 tests/test_api.py
- 用 httpx.AsyncClient 测试完整流程
- 上传 → 压缩 → 查询 → resume → 下载

验证：
- pytest backend/tests/test_api.py -v
- uvicorn app.main:app 能启动，/api/v1/health 返回 ok
```

### 输入 → 输出

- REST 层，输入是 HTTP 请求，输出是 JSON 或文件流

### 预期产出

- `backend/app/api/*.py`
- `backend/app/main.py`（集成所有路由）
- `backend/tests/test_api.py`

---

## Phase 13 — 测试与验证

### Prompt

```
请为核心模块补充覆盖测试，目标是验证正确性，不是追求覆盖率。

一、契约测试（已有 Phase 3 的 test_contracts.py，补充）
- 所有 Phase 的输入输出契约都能 JSON round-trip
- 从 Phase N 的输出构造 Phase N+1 的输入不需要任何转换

二、端到端测试 backend/tests/test_e2e.py
准备 5 份真实作品集样本（放在 tests/fixtures/portfolios/）：
- photo_heavy.pdf（照片为主，50MB）
- vector_heavy.pdf（CAD/线稿为主，20MB）
- text_heavy.pdf（文字为主，10MB）
- mixed.pdf（混合类型，80MB）
- huge.pdf（超大，200MB）

对每份样本，测试目标 5/10/15/20MB 的压缩结果：
- 断言：最终大小在目标 ±15% 之内（合计 20 个测试用例）
- 断言：页数正确
- 断言：书签和超链接保留
- 断言：处理时间 < 5 分钟

三、决策纯函数专项测试（补充 Phase 8 已有的）
- 用 property-based testing（hypothesis 库）随机生成 100 组输入
- 断言：所有输出的 quality ∈ [30, 95]
- 断言：所有 target_dpi ≤ original_dpi
- 断言：sum(estimated_bytes) 与 raster_budget 的比值 ∈ [0.7, 1.15]

四、收敛性能测试
- 用 20 份不同的作品集
- 统计 5 轮内收敛的成功率（目标 > 90%）
- 统计平均收敛轮数（目标 ≤ 3）

五、临时文件清理测试
- 触发 10 个任务，其中 3 个模拟失败
- 5 分钟后检查 tmp_workspace，断言所有文件已清理

六、并发测试
- 用 locust 或简单的 threading 同时提交 10 个压缩任务
- 断言：所有任务都能完成，无死锁，无文件混淆

最后运行：
- pytest backend/tests/ -v --cov=app --cov-report=html
- 打开 htmlcov/index.html 查看覆盖率报告
```

### 预期产出

- `backend/tests/test_e2e.py`
- `backend/tests/fixtures/portfolios/`（真实样本）
- 覆盖率报告

---

## Phase 14 — Web 前端

### 目标

用户界面：上传 → 目标选择 → 进度显示 → 分类 review → 下载。

### Prompt

```
请实现 frontend/ 下的 Next.js 前端。

技术栈：Next.js 14 (App Router) + TypeScript + Tailwind CSS + Zustand + Axios

页面结构：

一、/ (首页) - frontend/app/page.tsx
- 上传区（react-dropzone）
- 目标大小选择器（5/10/15/20 MB）
- 压缩场景选择器（屏幕/打印/邮件）
- 上传后跳转到 /task/[taskId]

二、/task/[taskId] - frontend/app/task/[taskId]/page.tsx
根据任务状态渲染不同界面：

  state = PROGRESS：
    - 进度条 + 当前 stage 文字提示
    - 每 2 秒轮询 GET /api/v1/tasks/:taskId

  state = AWAITING_REVIEW：
    - 网格显示所有页面（每页一张缩略图 + 分类标签 + 修改按钮）
    - complexity_score 高的页面高亮提示"请确认"
    - 底部"继续压缩"按钮触发 POST /api/v1/tasks/:taskId/resume

  state = SUCCESS：
    - 显示最终大小 + 压缩率
    - 下载按钮

  state = FAILURE：
    - 显示错误信息
    - 特别针对 TARGET_TOO_SMALL 和 CONVERGENCE_FAILED，
      引导用户"选择更大的目标"或"标记更多页面为激进压缩"

三、状态管理 - frontend/lib/store.ts
Zustand store：
- currentTaskId
- taskState
- uploadProgress
- classifiedPages（用户可修改）

四、API 客户端 - frontend/lib/api.ts
- uploadPdf(file, target_size_mb, compression_target)
- getTaskStatus(taskId)
- resumeTask(taskId, session_id, modifiedClassified)
- downloadResult(downloadId)

五、样式
- 使用 Tailwind + 简约风格
- 主色调深灰 + 强调色橙
- 深色模式支持

六、测试
- 至少一个 e2e 测试（Playwright）：
  上传 → 等待 review → 修改一页分类 → 继续 → 下载

验证：
- npm run build 成功
- npm run dev 启动，浏览器访问 localhost:3000 完整走一遍流程
```

### 预期产出

- `frontend/app/*`（页面）
- `frontend/lib/*`（API 客户端 + store）
- `frontend/components/*`（复用组件）

---

## Phase 15 — AWS EC2 部署

### 目标

把系统部署到 AWS EC2 生产环境。

### Context

**为什么不用 Railway？** EC2 给你完整的 IaaS 控制权：可以精细调优 CPU/RAM、可以挂 EBS 大盘存临时文件、可以细粒度控制安全组和网络。对于 CPU 密集型的 PDF 处理任务，EC2 的性价比明显好于 Railway/Vercel Serverless。

你之前部署过 KamaChat 到 EC2，这里可以直接复用你的经验。

### Prompt

```
请创建 AWS EC2 部署配置。

一、backend/Dockerfile
多阶段构建：
- Stage 1: python:3.11-slim as builder，pip install
- Stage 2: python:3.11-slim，复制依赖 + 应用代码
- CMD: uvicorn app.main:app --host 0.0.0.0 --port 8000

二、backend/celery.Dockerfile
基于同样的 base image，CMD: celery -A app.queue.celery_app worker

三、deploy/docker-compose.yml
服务：
- api（backend/Dockerfile，暴露 8000）
- celery_worker（backend/celery.Dockerfile，2 个副本）
- redis（redis:7-alpine，持久化 named volume）
- nginx（reverse proxy + SSL 终结）

四、deploy/nginx.conf
- upstream api → api:8000
- SSL 配置（Let's Encrypt 证书路径）
- 大文件上传：client_max_body_size 500M
- proxy_read_timeout 300s（长任务）
- CORS 头（虽然 FastAPI 也配了，作为兜底）

五、deploy/ec2/setup.sh
EC2 首次部署脚本：
- apt update && install docker, docker-compose, nginx, certbot
- 创建 /opt/pdfcompress/ 目录
- 拉取代码
- 生成 .env（提示用户填入 AWS 凭证等）
- certbot --nginx 配置 SSL
- docker-compose up -d

六、deploy/ec2/instance-config.md
文档：推荐的 EC2 配置
- 实例类型：t3.large（2 vCPU / 8GB RAM）作为起步
  重度使用：c6i.xlarge（4 vCPU / 8GB RAM，CPU 优化）
- EBS：gp3 卷 100GB（临时工作区）
- 安全组：22（SSH from your IP）、80/443（HTTP/HTTPS）
- IAM Role：如果启用 S3，需要 S3 读写权限
- CloudWatch Agent：收集 /var/log/pdfcompress/*.log

七、deploy/ec2/monitoring.md
监控建议：
- CloudWatch metrics：CPU、内存、磁盘、网络
- CloudWatch alarms：磁盘 > 80%、CPU > 90% 持续 5 分钟
- 应用日志：logrotate 每天，保留 7 天
- 关键业务指标（自定义 metric）：
  - 任务成功率
  - 平均收敛轮数
  - 平均处理时长

八、.gitignore 补充
- deploy/.env
- deploy/ec2/certs/

九、CI/CD 建议（不实现，只文档）
- GitHub Actions：push to main → 构建镜像 → 推送到 ECR → SSH 到 EC2 → docker-compose pull + restart

验证：
- 本地：docker-compose up 能成功启动整个 stack
- 云端：SSH 到 EC2 后跑 setup.sh，公网 IP 能访问健康检查端点
- 完整测试：从公网 URL 上传 100MB PDF，收到压缩结果
```

### 预期产出

- `backend/Dockerfile` + `backend/celery.Dockerfile`
- `deploy/docker-compose.yml`
- `deploy/nginx.conf`
- `deploy/ec2/setup.sh`
- `deploy/ec2/*.md`（文档）

---

## 附录 A：完整验收检查清单

### 铁律 grep 验证

```bash
# Phase D 内无文件 I/O（期望：无结果）
grep -rE "open\(|Path\(|os\.remove|shutil" backend/app/pipeline/decide.py

# Phase D 内无图像处理库（期望：无结果）
grep -rE "import PIL|import cv2|from PIL|from cv2|import fitz" backend/app/pipeline/decide.py

# Phase D 内无网络调用（期望：无结果）
grep -rE "import requests|import httpx|urllib" backend/app/pipeline/decide.py

# 契约类型没有被下游修改（期望：只在 contracts/ 目录下有 class 定义）
grep -rE "class RasterPlan|class VectorPlan|class CompressionPlan" backend/app/ | grep -v backend/app/contracts/

# 所有魔法数字应该在 config 里（期望：pipeline/ 下无硬编码的 quality/dpi 数字）
grep -rE "quality\s*=\s*[0-9]+|dpi\s*=\s*[0-9]+" backend/app/pipeline/ | grep -v "settings\.\|config\."
```

### 功能验证

- [ ] `pytest backend/tests/ -v` 全部通过
- [ ] 相同参数压缩两次，最终大小差异 < 1%（决策确定性验证）
- [ ] 20 份样本 × 4 个目标大小的端到端测试，收敛成功率 > 90%
- [ ] 用户 review 断点：AWAITING_REVIEW 状态下 30 分钟未响应，session 和 tmp_workspace 自动清理
- [ ] `curl POST /api/v1/upload -F "file=@big.pdf"` 返回 file_id
- [ ] `curl GET /api/v1/health` 返回 `{status: "ok"}`

### 安全检查

- [ ] `config.local.yaml` 在 `.gitignore` 中
- [ ] `git status` 确认 `.env` 未被追踪
- [ ] 上传的 PDF 处理完成后 5 分钟内被清理（`ls tmp/` 为空）
- [ ] API 层拒绝非 PDF 文件（Content-Type 校验 + 首字节 %PDF 校验）
- [ ] 上传大小限制生效（>500MB 返回 413）

### 部署验证

- [ ] `docker-compose up` 本地能启动整个 stack
- [ ] EC2 setup.sh 一键部署成功
- [ ] HTTPS 生效，Let's Encrypt 证书有效
- [ ] CloudWatch 能看到应用日志

---

## 附录 B：关键常量参考表（config.yaml 主要字段）

| 模块 | 参数 | 默认值 | 含义 |
|------|------|--------|------|
| 主控 | aggressiveness | 0.5 | 默认压缩激进度 |
| 主控 | area_sensitivity | 0.6 | 小图 vs 大图差别待遇 |
| 主控 | pixel_ceiling_ratio | 1.2 | 像素天花板安全余量 |
| 预设 | print.aggressiveness | 0.2 | "打印" 场景预设 |
| 预设 | screen.aggressiveness | 0.5 | "屏幕" 场景预设 |
| 预设 | email.aggressiveness | 0.8 | "邮件" 场景预设 |
| 位图 | quality_floor | 30 | JPEG 质量绝对下限 |
| 位图 | quality_ceiling | 95 | JPEG 质量绝对上限 |
| 位图 | dpi_floor | 72 | DPI 绝对下限 |
| 位图 | dpi_ceiling | 300 | DPI 绝对上限 |
| 矢量 | vector_ignore_area_ratio | 0.1 | area_ratio 低于此值无条件 keep |
| 矢量 | vector_simplify_threshold | 10000 | 控制点 > 此值考虑 simplify |
| 矢量 | vector_rasterize_threshold | 100000 | 控制点 > 此值考虑 rasterize |
| 收敛 | max_convergence_rounds | 5 | binary search 最大轮数 |
| 收敛 | convergence_tolerance | 0.15 | 达标容忍度（±15%） |
| 收敛 | aggressiveness_step_large | 0.15 | 超标 >30% 时的单轮调整量 |
| 收敛 | aggressiveness_step_small | 0.08 | 超标 <30% 时的单轮调整量 |
| 存储 | tmp_retention_seconds | 300 | 临时文件保留时间（秒） |
| 会话 | review_session_ttl | 1800 | 用户 review 会话超时（秒） |
| API | max_upload_mb | 500 | 上传大小上限 |

---

## 附录 C：可选扩展 — AI 分类模型（Phase Optional）

> 在核心系统（Phase 0-15）稳定后再考虑接入。核心铁律不变：Phase 6 的启发式分类器可以被替换，但输入输出契约（`List[PageElements]` → `List[ClassifiedPage]`）绝对不变。

AI 分类模型的目标：

- 更准确地识别页面类型（尤其是 mixed 类）
- 识别更细粒度的类型：封面页、目录页、章节分隔页、作品展示页、CV 页
- 输出 complexity_score 更准确

技术选型建议：

- 用现成的多模态模型（如 CLIP + 页面截图）
- 或轻量视觉分类模型（ResNet18 + 手工标注 500 张作品集页面）
- 或直接调用 Claude Vision API（简单，但有 API 成本）

集成方式：

```python
# backend/app/pipeline/classify.py

class HeuristicClassifier:  # V1
    def classify_pages(self, pages): ...

class AIClassifier:  # V2
    def __init__(self, model_path):
        self.model = load_model(model_path)
    def classify_pages(self, pages): ...

def get_classifier(config):
    if config.classifier_type == "ai" and config.classifier_model_path:
        return AIClassifier(config.classifier_model_path)
    return HeuristicClassifier()
```

**接口不变的价值**：Phase 8 的决策代码一行都不用改，即使换了分类器。

---

## 附录 D：可选扩展 — 用户账户系统

如果需要为用户提供历史记录、批量处理、订阅计费等功能，可以在 Phase 12 之前插入一个"账户与鉴权"Phase。参考 QuantSaaS 构建手册的 Phase 2 的 auth 部分。

对于纯工具型应用（用户不需要登录就能用），可以完全不做账户系统。

---

## 实施期架构演进（Phase 4-11 期间对原手册的重大偏离，2026-07-05 首建）

> **本章的地位**：原手册是设计期文档，本章记录实施期用真实数据推翻/扩展原设计的
> 重大架构决策。每条按固定格式：为什么原手册不能满足 / 发现过程 / 最终方案 /
> 契约与架构变化。**未来任何人接手本项目，先读三份真源文档，再读本章。**
> 维护规则（CLAUDE.md 工作方式约定）：每个 Phase 验收结束时，若发生原手册未预见的
> 重大决策，按同样格式追加至本章。

### 演进 1：分级降级压缩架构（Tier System，2026-07-04 产品决策）

**为什么原手册不能满足**：原手册假设"单策略压缩 + 收敛环"覆盖所有场景，收敛失败
按铁律 7 明确报错（TARGET_TOO_SMALL / CONVERGENCE_FAILED）。真实数据证伪了这个
假设：矢量重作品集会撞到物理下限——portfolio_2 的矢量内容流下限 8.2MB 本身就超过
5MB 目标，任何参数调优都无解。而在设计求职场景中"压到目标大小"是硬指标（申请
门户的上传上限），用户宁可"文字略模糊但达标"，也不要"质量高但交不上"——终态
报错在产品上是失败的。

**发现过程**：Phase 11 收敛环首次真实样本实测（三作品集 × 5/10MB 六场景），
p2@5MB 直接 TARGET_TOO_SMALL，其余五场景全部 CONVERGENCE_FAILED——收敛环在
单策略下对真实作品集近乎全军覆没。

**最终方案**：三级降级状态机（orchestrator 层）——
`in_place`（保真，现状 A2 原地手术）→ `hybrid`（贪心选出的重矢量页整页光栅化，
其余页保真；选页纯函数在 decide.py，字节说话+最小化光栅化页数）→ `full_raster`
（全部非 skip 页整页光栅化，独立质量下限 tier3_dpi_floor=72/tier3_quality_floor=30/
tier3_dpi_ceiling=150，与 Tier 1/2 的目视标定 floor 隔离）。review 断点只在 Tier 1
前触发一次，降级事后告知（tier_used + 警告文案 + 可重试更大目标）；三级耗尽才报
CONVERGENCE_FAILED，且必须携带可行动诊断（skip 页统计 + 真实最优可达大小）。
skip 页在任何 Tier 下不被触碰（铁律 4 升格为契约层 model_validator）。

**契约与架构变化**：contracts 层新增 `CompressionTier`（Literal + COMPRESSION_TIERS
有序 tuple，顺序即降级方向）、`WholePageRasterPlan`、`PagePlan.whole_page_plan`
（互斥约束构造即校验）、`CompressionPlan.tier`、`CompressionResult.tier_used`（Phase 9
原样回带）、`ConvergenceStep.tier`、`ConvergenceDiagnostics` + `PhaseErrorData.diagnostics`。
config 新增 tier2_budget_margin / tier3_* 四键。整页光栅化原语一次实现两级复用：
渲染归 compress.py、页内容替换归 assemble.py（先 insert 后清空原内容流，失败原子性）。
规格全文：《压缩决策引擎.md》§8.5-8.7、《系统架构设计.md》§3.5/§4.5。

### 演进 2：Tier 边界真实值判定 + 估算体系两次修正（2026-07-04/05）

**为什么原手册不能满足**：原手册 §8.1 收敛环用估算判断达标
（`total_raster_bytes + total_fixed_bytes`）。切换原地手术主干（演进 3）后，
`total_fixed_bytes` 的矢量口径仍按重建语义估算，而原地手术根本不重建——估算与
真实装配大小的偏差实测最高 +134%（p1），用估算做降级决策会把"实际已达标"的
任务误降级，平白损失质量。

**发现过程**：Phase 11 收敛实测的诊断轮——对每个场景在收敛环结束后强制执行一次
真实 assemble 对照，暴露估算 vs 真实的系统性断裂（p1 +127~135%、p2 +15%、p3 +0.2%，
偏差量与矢量含量正相关）。

**最终方案（三层递进）**：
1. **边界真实判定**（2026-07-04 裁决）：每个 Tier 结束执行真实 assemble + stat 文件
   大小，用真实值决定接受/降级；环内仍用估算求快。被接受 Tier 的 assemble 产物即
   最终输出（Tier 1 一次通过零额外开销），每任务最多多付 1-2 次 assemble。
2. **环内基准修正**（2026-07-05 裁决，"用错字段"性质）：环内 gap 的 fixed 基准从
   `pre.total_fixed_bytes` 改为 `target − plan.raster_budget`（decide 本就算对的
   Tier 一致口径 floor）。旧基准恒虚高 → 每 Tier 被推到激进度上限、参数钉死 floor，
   首轮验收 6 场景全落 full_raster 且欠冲 34-48%。
3. **JPEG 估算系数实测标定**（2026-07-05 破例把 R7/Phase 13 事项提前）：基准修正后
   系数偏差成为唯一拦路虎——§5.5 公式在工作区间高估整页 49-76%，decide 内部再平衡
   据此钳制参数与外层环打架。用三样本 764 个受控测量点标定 0.04/0.12 → 0.015/0.105
   （scripts/calibrate_jpeg_bpp.py，可复用）。标定后欠冲收敛至 -18~+0.7%，
   p2@10MB / p3@10MB 落在目标 ±1.2% 内。
架构级防御：环内基准不变量测试（total_bytes − raster ≡ target − raster_budget）
锁死回归路径。

**契约与架构变化**：orchestrator `_run_tier` / `_actual_size`；
《压缩决策引擎.md》§8.1 修订 + §5.5 标定记录；config 系数更新。
估算的剩余已知界限：q≥95 处线性公式低估 38-41%（PIL 色度子采样阶跃），方向安全
（低估→再平衡宽松→真实字节驱动的外层环兜底）；PNG 系数为死路径（A2 后 decide
只出 jpeg），R7 就此关闭。

### 演进 3：原地手术架构（Phase 10 从重建改为原地手术，2026-07-04 架构决策 A/A2）

**为什么原手册不能满足**：原手册 Phase 10 的组装语义是"重建"——新建空白 PDF，
逐页插入压缩后的位图、重绘矢量、重写文字。目视验收发现结构性死路：设计软件导出的
PDF 常含**无 ToUnicode 的字体**（如 AdobeSongStd），其文字层码点物理不可恢复
（QC 实测：源文字层 CJK 提取为 0 但嵌有中文字体）——重建路径无论如何实现都会
把这类中文变成乱码或丢失。

**发现过程**：Phase 10 一轮目视验收五项问题（白底变黑/整页全黑/中文乱码/空白页/
矢量被盖），前四项可修，唯独 ToUnicode 缺失是不可修的物理限制 → 用户裁决主干换路线。

**最终方案**：assemble.py 重写为"复制原 PDF + 原地替换"——split 保存 source.pdf
副本；extract 写 sidecar（images/xref_map.json：data_ref → 源 xref）；assemble 按
xref 定位替换位图流；字体子集化用 retain_gids=True 保字形 ID 稳定 + PUA/控制符
门禁。文字/矢量/z-order/链接/书签**一个字节不动——保真由构造保证**。
后续深化 A2（2026-07-04）：真透明图不再合并 RGBA PNG（合并重编码比源内
"JPEG 基底+独立 SMask"大 4-10 倍），改为 `xref_copy(keep=["SMask","Mask"])`
只替换基底流、SMask 引用原样保留——透明与压缩解耦。

**契约与架构变化**：extract 增加 xref sidecar（工作区内部工件，非契约）；
重建路径退役为 assemble_rebuild.py（保留供未来"双轨选项 C"，其真透明合成能力
在 A2 后已知退化，对应测试 xfail 标注）。已知代价 R9：原地语义下元素级矢量策略
（simplify/rasterize）不被执行，矢量原样保留——该缺口后来正是由演进 1 的 Tier 2
整页光栅化补上（选页函数的保留成本按原地真实语义计算）。三项演进环环相扣：
演进 3 造成的估算口径断裂由演进 2 修复，造成的矢量缺口由演进 1 补齐。

### 演进 4（小项）：Celery FAILURE 结果的序列化约束（2026-07-05，Phase 12 实测）

**为什么原手册不能满足**：手册 Phase 11 伪码 `self.update_state(state="FAILURE",
meta={"error": e.model_dump()})` 在 JSON 结果后端写出裸 dict 形状的 FAILURE 结果；
Celery 约定 FAILURE 结果必须是其自身序列化的异常（exc_type/exc_module/exc_message），
`AsyncResult` 读回时直接抛 `Exception information must include the exception type`。

**发现过程**：Phase 12 test_api 的 FAILURE 状态查询用例首跑即崩。

**最终方案**：任务只 `raise`，交 Celery 序列化；`PhaseError.args` 对齐构造签名
（diagnostics 以 dict 形式入 args 保 JSON 可序列化，构造时 pydantic 自动校验回模型），
JSON 后端 `cls(*args)` 无损重建；`__str__` 显式定义保持人类可读格式。

**教训**：FAILURE 状态的结果形状是框架级契约，不可自定义 meta——设计伪码
遇到框架实际约束时以现实为准，并把偏离记录在案。
