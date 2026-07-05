# PROJECT_STATE.md — 会话锚点

> **用途**：新对话恢复上下文的唯一入口。配合 CLAUDE.md（宪法/铁律/工作规则）+
> docs/ 三份真源文档使用，无需回看历史验收报告。
> **维护规则**：每个 Phase 验收通过后更新本文件（状态节 + Phase 概览行）。
> 最后更新：2026-07-04（Phase 11 完成，待用户验收，见 §9）。

---

## 1. 已完成 Phase 概览

| Phase | 目标 | commit |
|-------|------|--------|
| 0 | 环境初始化 + AI 约束文件（CLAUDE.md/.cursorrules/目录骨架/依赖清单） | `4e76f50` |
| 1 | 三份真源文档（docs/系统架构设计、压缩决策引擎、数据契约） | `fe39421` |
| 2 | 基础设施层（pydantic-settings+YAML 配置 / StorageBackend ABC / Celery+Redis） | `ee40a1e` |
| 3 | 数据契约层（26 个 Pydantic v2 类型 + 4 处 Producer-Consumer 补丁） | `3cbda97` |
| 4 | PDF 拆页 split.py（加密/损坏检测、单页容错、resolve_ref 卡口） | `3c159b8` |
| 5 | 元素提取 extract.py（alpha_type 三态 + 共享 data_ref 去重） | `e90ab9b` |
| 6 | 页面分类器 classify.py（启发式 V1，阈值全部 config 驱动） | `e17b77d` |
| 7 | 预处理 preprocess.py（字体子集化 + 固定开销 + vector_bytes 语义修复） | `afa3c58` |
| 8 | 决策引擎 decide.py（纯函数 + F1 成本守卫） | `8ae760c` |
| 9 | 压缩执行 compress.py（单轮，共享 data_ref 去重） | `b4149ac` |
| 10 | PDF 重组 assemble.py（原地手术主干，架构决策 A） | `193045d` |
| 10.5 | 视觉质量校准 + 架构决策 A2（base+SMask 原地保留取代 RGBA PNG 合并） | `9fcd58d` |
| 11 | 任务编排 orchestrator.py + queue/tasks.py（收敛循环 + Celery + review 断点，**待验收**，见 §9） | WIP |

远程：https://github.com/Xiao-Y-09/Portfolio_PDF_Compressor （main，已推送至 `9fcd58d`；Phase 11 待验收后 squash push）

## 2. 关键架构决策记录（只列结论 + 出处，不复述）

1. Celery 而非 RQ（进度回调/任务链）→ `backend/app/queue/celery_app.py` docstring
2. file_id = UUID v4 不可枚举 + 每次拼路径前强制校验 → `backend/app/storage/base.py`
3. 存储接口 = 显式 ABC，定版不改；save_from_path 按 look-ahead 预纳入 → 同上
4. cleanup_expired 只按文件 mtime（不依赖 Redis）；工作区目录归 orchestrator 管 → `storage/local.py`
5. data_ref = workspace 相对 POSIX 路径，resolve_ref() 唯一解析卡口（防穿越）→ 数据契约.md §1.6
6. PhaseErrorData(模型) + PhaseError(异常载体) 双层 → 数据契约.md §1.4
7. 面积修正用连续公式（离散表仅校验参考）→ 压缩决策引擎.md §4.3 决策理由记录
8. 收敛调整用全局 base_aggressiveness ± step（1 维搜索）→ 压缩决策引擎.md §8.2
9. 契约 4 补丁：RasterImage/VectorPath.original_bytes、Plan/Result.base_aggressiveness、ConvergenceStep + convergence_history → 数据契约.md 修订记录
10. alpha_type 三态（none/opaque/translucent），has_alpha ≡ translucent 别名 → 数据契约.md §3.1 + 决策引擎.md §5.4
11. 共享 data_ref：每页每 xref 只写盘一次；Phase 9 按 data_ref 去重压缩、Phase 10 xref 共享插入 → 数据契约.md §3.1.1
12. V1 矢量按页聚合为单组（策略语义单位）→ `pipeline/extract.py` docstring
13. flatten 策略暂缓（含当初不做的推理 + 重启条件）→ 决策引擎.md §5.4 决策记录
14. 分类阈值 9 项入 config `classifier:` 段 → config.yaml + `pipeline/classify.py`
15. VectorPath.original_bytes 语义 = 内容流预估（cp × vector_bytes_per_control_point，默认 3.0，Phase 10 校准）；不是 pickle 大小 → 数据契约.md 修订记录
16. raster_budget = target − 不可削减固定 − 矢量计划成本 − skip 页位图；TARGET_TOO_SMALL 基于不可削减下限 → 决策引擎.md §7.1
17. Phase D 派生常量（容差/光栅化 DPI/估算系数/再平衡线等 13 项）全部迁入 config → 决策引擎.md 第 9 章
18. 预算与再平衡按"唯一流口径"（每页每唯一 data_ref 取最大估算）→ `pipeline/decide.py` docstring
19. 工程规则五条：显式声明直接依赖 / 接口 look-ahead / Producer-Consumer 审计 / WIP checkpoint / 数据驱动前瞻风险 → CLAUDE.md 工作方式约定

## 3. 已知前瞻风险（未决事项）

| # | 风险 | 状态 |
|---|------|------|
| R1 | alpha 分布：按唯一 SMask 52.6% 真透明（重度）；flatten 暂缓，若 Phase 8/9 实跑 email/5MB 不收敛则重启评估（用户可选开关方案） | 决策引擎.md §5.4 |
| R2 | vector_bytes_per_control_point = 3.0 未经真实输出校准 | Phase 10 首次 assemble 时标定（test_preprocess.py 有 deferred 测试占位） |
| R3 | raster_budget 不可削减下限判定 | Phase 8 已实现（WIP2），验收中验证 |
| R4 | total_fixed_bytes 估算 ±10% 无 ground truth | Phase 10/13 端到端校准 |
| R5 | 裸 CFF 字体（含中文 AdobeSongStd）不可子集化，保原件 | V1 限制；如需啃：CFF→OTF 包装 |
| R6 | git 作者身份仍是默认值（Your Name <you@example.com>） | 用户配置 user.name/email（历史 commit 可不改） |

## 4. 当前项目状态（2026-07-04）

- HEAD：`56b9dd1`（WIP: Phase 8 stage 2）；正式基线 `afa3c58`（Phase 7，已推送）
- git 追踪文件：56；commit 数：10
- 测试：**103 passed + 1 skipped**（截至 afa3c58 全量绿）；test_decide.py 新增 17 用例**未运行**
- 本机环境：Python 3.14.3（backend/.venv）、pymupdf 1.28.0、fonttools 4.63.0、Docker 容器 pdf-redis（redis:7-alpine，--restart unless-stopped）
- 真实样本：`backend/tests/fixtures/portfolios/portfolio_{1,2,3}.pdf`（71/99/33MB，**gitignored——换机器需重新放置**）
- pytest 注意：basetemp 已固化在 backend/pytest.ini（Windows %TEMP% ACL 问题）

## 5. Phase 8 入口条件（前置产物清单）

| 需要 | 来源 | 状态 |
|------|------|------|
| PageElements[]（含 alpha_type/original_bytes/共享 data_ref） | Phase 5 extract | ✅ |
| ClassifiedPage[]（决策签名参数；连续公式下仅 review 用途） | Phase 6 classify | ✅ |
| PreprocessResult（total_fixed_bytes 语义修复后量级正常：p2 = 19.8MB） | Phase 7 preprocess | ✅ |
| 契约：CompressionPlan/Result（含 base_aggressiveness）、PhaseError | Phase 3 contracts | ✅ |
| config：compression 段全部键（含 13 项派生常量 + gap_large_threshold） | Phase 8 前置迁移 | ✅ |
| decide 不读 settings/磁盘，config 由调用方传参 | 纯函数铁律 | ✅ 已按此实现 |

## 6. Phase 8 状态：✅ 完成（含 F1 成本守卫，用户已验收）

- decide.py：纯函数决策引擎（主控推导/位图/矢量+F1 成本守卫/不可削减下限预算/
  唯一流再平衡/收敛步进）；附录 A 五条 grep 全空集
- 测试：21 个 decide 用例（手册 10 必测 + 6 扩展 + 5 个 F1）；全量 124 passed + 1 skipped
- F1 已落地（决策引擎.md §6.3 成本守卫决策记录）：portfolio_2 从全档 TARGET_TOO_SMALL
  变为 10/15/20MB 可行、5MB 如实拒绝；guard_hits=3、正当 rasterize 保留 3
- 已知边界观察：p2@10MB 估算 2.96MB 略超 cap 2.93MB（PNG 不吃 quality、DPI 触底
  72）——下限约束下的最优，交收敛环兜底；rasterize DPI 降档备选推迟到 Phase 9 实测后
- Phase 8 正式 commit：`8ae760c`（已 push）

## 7. Phase 9 状态：compress.py 完成，等待验收（WIP `d7b047b` → `4d0ba7a`）

- compress.py：唯一图像库入口；按 data_ref 去重压缩（最保守参数：max quality/max
  dimension）；只降不升回退；成功率 <80% 熔断；矢量执行（simplify=自实现 DP，
  rasterize=split 单页 PDF клип渲染）；execute_compression 增加 config 参数（披露项）
- 测试 9 个（去重/保守参数/回退/容错/熔断/skip/DP/渲染/双轮握手）；全量 133+1s
- 真实单轮 @10MB：去重 12573 摆放 → 749 次实际压缩；压缩耗时 3-14s/份；
  raster 60.5→5.2 / 75.9→8.3 / 33.3→5.3 MB
- ⚠ R7 新前瞻风险：§5.5 估算系统性偏低（actual vs est：+26%/+68%/+181%），
  主因 png_bytes_per_pixel=0.35 对照片类 RGBA 过于乐观（实际 1-3 B/px）；
  收敛环可吸收但烧轮数；建议 Phase 13 每格式标定（或提前调 config 默认值，待用户裁决）
- Phase 9 正式 commit：`b4149ac`（已 push）

## 8a. Phase 10 二轮：架构决策 A —— 原地手术主干（2026-07-04 用户裁决，等待二次目视）

一轮目视 5 项问题 → 4 项修复（SMask 合并/白底/回退/glyph 检查）+ 1 项架构级发现：
**无 ToUnicode 字体文字层不可恢复** → 主干切换为原地手术（assemble.py 重写，
重建路线保留于 assemble_rebuild.py 供未来双轨选项 C）。

- 机制：split 存 source.pdf 副本；extract 写 sidecar images/xref_map.json
  （data_ref→源 xref，同内容多 xref 列表）；assemble 按 xref 定位 replace_image；
  字体子集 retain_gids=True + PUA/控制符门禁（可疑字符集放弃子集化保文字完整）
- QC 实证：95 页 0 黑页；文字空白归一化后 3 样本逐字相同；CAD 页渲染像素差
  0.29-1.04/255（clean=True 重排视觉等价，drawings 计数差异为良性规范化）
- @10MB 单轮大小：p1 13.53 / p2 32.42 / p3 7.09 MB（p2 超标：矢量原封不动 + R7
  估算偏差；收敛环 Phase 11 处理，未来增强：原地整页光栅化极端矢量页）
- 测试 148 passed + 1 skipped（原地语义 11 用例 + rebuild 保留 3 用例）

## 8e. ✅ A2 落地完成（2026-07-04）：base+SMask 原地保留，取代 RGBA PNG 合并方案

**任务 a（全量回归，守卫后首次完整跑）**：152 passed + 1 skipped，0 failed；附录 A
五条铁律 grep 复核全空集。

**任务 b（A2 落地）**：
- **机制**：extract.py 不再对 translucent 图调用 `_merge_smask`（函数已删除）——
  img_bytes 就是 base 本身；decide.py `_decide_raster` 的 output_format 统一改为
  `"jpeg"`（不再按 alpha_type 分流）；assemble.py 新增 `_replace_base_stream()`
  （`xref_copy(new_xref, xref, keep=["SMask","Mask"])` 替代 `page.replace_image()`）
  ——base 流被替换，旧 /SMask 引用因 `keep=` 保护不被清空，透明与压缩解耦、零改动
  保真。手工验证脚本证实：JPEG 替换后 `xref_get_key(xref,"SMask")` 与替换前一致。
- **文档修订**：《压缩决策引擎.md》§5.4（机制+决策记录，第二版方案标注已废弃）、
  §3 输出约束、§10 验证清单、flatten 决策记录追加"A2 后更新"；contracts/plan.py
  docstring 同步。
- **测试**：test_decide.py::test_07 更名为 `test_07_all_alpha_types_use_jpeg_base`
  （断言三态 alpha 均输出 jpeg）；test_assemble.py 全部用例（含
  `test_translucent_replacement_keeps_alpha`/`test_black_scrim_regression`）**未改
  断言、直接通过**——证明机制按预期工作。
- **意外发现 + 用户裁决**：assemble_rebuild.py（已退役重建路线）隐式依赖旧合并
  产物做整页重画合成，A2 后对 translucent 图会画成完全不透明，
  `test_rebuild_black_scrim_regression` 回归。用户裁决：**不修复该已退役模块**，
  改为 `@pytest.mark.xfail(strict=True)` 标注 + 模块 docstring 记录已知限制
  （未来若启用双轨选项 C 需自行在绘制前合并 base+SMask）。
- **全量回归（含 xfail 标注后）**：151 passed + 1 skipped + 1 xfailed，0 failed。
- **重产预览件**（单轮无收敛环，Phase 11 前基线；5MB→email 预设、10MB→screen 预设，
  与 §1.3 用户体验层映射一致）：

  | 样本 | 目标 | A2 前（守卫后） | A2 后 | Δ |
  |------|------|------|------|---|
  | p1 | 5MB | 19.12 | **17.02** | -11.0% |
  | p1 | 10MB | 20.48 | **20.03** | -2.2% |
  | p2 | 5MB | TARGET_TOO_SMALL | TARGET_TOO_SMALL（不变，矢量下限 8.2MB 本身超 5MB 目标，与 A2 无关） | — |
  | p2 | 10MB | 41.30 | **31.75** | **-23.1%** ← A2 核心命中目标 |
  | p3 | 5MB | 11.70 | **11.59** | -0.9% |
  | p3 | 10MB | 13.45 | **14.09** | +4.8%（见下）|

  p3@10MB 小幅上升的原因：A2 让 translucent 图的预算**估算**从 PNG 系数
  （0.35 B/px）降到 JPEG 系数（~0.1-0.16 B/px），§7.4 预算再平衡对"总估算"的感知
  变小，触发的全局 quality 下调幅度对同页 opaque/none 图变轻——本质是估算更准确
  后重新分配了 quality 预算，不是 bug；单轮无收敛环下这类页内偏移预期存在，
  Phase 11 的 binary search 会在后续轮次收紧。p2 从 41.30→31.75MB 印证 A2 对
  original 场景（PNG 膨胀病灶最重的样本）修复有效。
  预览件：`preview_output/portfolio_{1,2,3}_{5,10}mb.pdf`（p2 5mb 无文件，见上）。

**前瞻风险更新**：R1（flatten 暂缓）标注"触发条件已被 A2 消解"，产品判断维持不做；
R7（PNG 估算偏低）随 A2 消解（base 统一走 jpeg 估算，不再触碰 png_bytes_per_pixel）；
R8（translucent 原地膨胀）**已根治**；R9（p2 极端矢量页原地整页光栅化增强）未变、
仍是 Phase 11+ 议题；新增 R10：assemble_rebuild.py 真透明合成能力已知退化（已 xfail
标注，不阻塞主干，未来若启用选项 C 需自行处理）。

**已完成**：①用户目视确认预览件（质量好、透明保留、无黑页无乱码，超标符合
"单轮无收敛环"预期）→ ②squash 为正式 commit `9fcd58d`「Phase 10.5: visual
quality calibration (A2 base+SMask)」并 push → ③Phase 11 见 §9。

## 8c. 压缩质量优化轮（Phase 11 前，用户指定；2026-07-04）

- 优化 1（opaque→JPEG）：Phase 8 §5.4 修订时已生效，本轮补 E2E 视觉无差别测试
- 优化 2（灰度降通道）：契约 +is_grayscale（默认 False）；extract 采样检测
  （容差 2 吸收 JPEG 色度噪声，披露性偏离 R==G==B 规格）；compress 转 L/LA。
  实测唯一图灰度检出 112/66/85 张；@10MB 尺寸 13.53→13.35 / 32.42→32.32 / 7.09→6.97
- 优化 3（quality 基准实验）：preview_output/quality_comparison/portfolio_3_page24_q{50..85}.pdf
  （0.82/0.92/1.06/1.27/1.44MB），等用户目视选基准后改 config。
  实验方法论教训：必须在 source.pdf 的 xref 空间替换（split 单页是重编号空间）
- 优化 4（PNG 参数）：实测 level≥6 体积零差别、optimize 无增益 → config 新增
  png_compress_level=6 / png_optimize=false，compress 改读 config
- 测试 151 passed + 1 skipped

## 8b. Phase 10 一轮记录（重建路线，已退役）：assemble_rebuild.py（WIP `38a496e` → `3471d0b` + 标定）

- assemble.py：xref 共享插入（§3.1.1 兑现）/ 矢量重绘（keep/simplify pickle、rasterize PNG）/
  文字+子集化字体（回退链 别名→china-s→跳过）/ 链接+书签+清理元数据 / 手册保存参数
- 修复（Phase 10 测试发现）：extract 按内容摘要去重——pymupdf get_image_rects 按摘要
  匹配摆放，同内容 N 个 xref 会 N×M 膨胀条目；早前"12573 摆放"系此 bug 虚增，
  真实值 749 摆放 / 502 唯一流
- R2 已闭环：vector_bytes_per_control_point 实测标定 3.0 → 4.5（p2 内容流 3.76MB/839k cp；
  用户预授权 >30% 即更新）；标定后 p2 仍 10/15/20 可行、5MB 拒绝
- 测试：assemble 5 用例（页数守恒/尺寸/单次嵌入/链接书签元数据/输出变小）；全量 138+1s
- 目视验收物：preview_output/portfolio_{1,2,3}_10mb.pdf（5.87 / 15.66 / 5.92 MB，单轮无收敛）
- V1 保真近似（目视预期）：层序固定 位图→矢量→文字；文字统一黑色+基线近似；
  书签压平一级；rasterize 区域含层叠内容；form_field/comment 不重建

## 9. Phase 11 状态：✅ 完成，待用户验收（2026-07-04）

**产出**：
- `backend/app/pipeline/orchestrator.py`（新）：`run_until_classify`（Phase A→B→C）+
  `resume_after_review`（Phase D0→收敛循环→F）+ `SessionData`（工作区内部工件，非契约）+
  `save_session`/`load_session`/`workspace_path`/`cleanup_workspace`/
  `cleanup_stale_workspaces`。不导入 celery/redis，可在无 Celery 环境下直接单元测试。
- `backend/app/queue/tasks.py`（替换 stub）：`compress_pdf_task`（AWAITING_REVIEW 断点）+
  `resume_compression_task`（SUCCESS/FAILURE 终结）。Redis session 指针
  `session:{task_id}` → `{tmp_workspace}`，TTL=`settings.session.review_session_ttl`；
  工作区目录名即 task_id，无需额外映射表。
- `backend/app/main.py`：定时清理循环追加 `cleanup_stale_workspaces()`（§4.4 超时策略，
  与 storage.cleanup_expired 同一防御性设计——按文件 mtime 判断，不依赖 Redis）。
- 文档：《系统架构设计.md》§5.1 错误码表新增 `SESSION_EXPIRED`。

**收敛循环落地**（《压缩决策引擎.md》§8，铁律 7）：gap_ratio ∈ [-tolerance,0] 达标 /
(0, tolerance/2] 微超标接受 / 5 轮耗尽后 gap_ratio>0 → `CONVERGENCE_FAILED`
（phase=orchestrator，recoverable=false）、gap_ratio≤0 接受（宁可小一点也不超标）。
`ConvergenceStep` 由 orchestrator（而非 Phase 9）在每轮 execute_compression 后追加进
`ctx.convergence_history`——契约文档标注的"Producer=Phase 9"是设计期措辞，Phase 9
签名本身缺 target_bytes/fixed_bytes 无法独立算出 gap_ratio，orchestrator 是唯一同时
掌握全部输入的位置；不算契约违反（字段/语义不变，只是产出时机在编排层）。

**测试**：`test_orchestrator.py`（11 用例，不涉及 Celery/Redis：会话存取往返、
收敛三种结局、skip override 生效、工作区清理）+ `test_tasks.py`（6 用例，Celery
eager 模式：完整链路 upload→AWAITING_REVIEW→resume→SUCCESS、进度阶段断言、review
修改生效、session 不存在/超时、CONVERGENCE_FAILED 仍触发清理、split 失败清理）。
全量回归 168 passed + 1 skipped + 1 xfailed（含 Phase 10.5 遗留的 assemble_rebuild
xfail）；附录 A 五条铁律 grep 复核全空集。

**手动验证**（真实 Celery worker + Redis broker，非 eager，模拟未来 Phase 12 会做的
事）：本机启动 `celery -A app.queue.celery_app worker --pool=solo`（Windows 不支持
prefork），用独立 storage tmp_dir + Redis db14 隔离，`compress_pdf_task.delay()` →
`AWAITING_REVIEW` → `resume_compression_task.delay()` → `SUCCESS`，下载产物有效
（2 页，533878 字节）。验证完成后已停止 worker、清空 db14、删除临时目录。

**Producer-Consumer 审计**：
- `ConvergenceStep`/`ctx.convergence_history`：曾是"凭空字段"（Phase 8/9 定义时无
  Producer），本 Phase 补齐 Producer（orchestrator），Consumer 目前是测试断言 +
  日志；Phase 13 收敛性能统计、Phase 14 前端图表待其消费——非本 Phase 断链。
- 发现并修复 2 处自查问题（未进最终报告前已改）：① Redis session 载荷曾多存一个
  从未被读取的 `file_id` 字段（孤儿字段），已删除，`tmp_workspace` 足够定位到
  `session_data.ctx.file_id`；② `tasks.py` 的 Redis 客户端曾用模块级单例缓存，
  与 `get_settings()` 在测试中可被 `cache_clear()` 重新配置（切换 db）的事实冲突——
  单例会绑定到过期连接串；改为每次调用现建（redis-py 连接本身是惰性的，无实质开销）。
- `modified_classified`（review 修改后的分类）：Consumer 是 `decide_compression` 的
  `classified` 形参，但 Phase 8 的 decide.py 内部当前并不读取该参数（page_type 尚未
  驱动决策，是 Phase 8 既有特征非本 Phase 引入）——接口满足、语义未来待用，非断链。
- `on_progress` 阶段串（split/extract/classify/preprocess/converge/assemble）与
  《系统架构设计.md》§3.4 的百分比完全对齐，测试已断言；终端 Consumer（前端进度条）
  要等 Phase 12 REST 层暴露 `GET /api/v1/tasks/:id` 才真正落地——不影响本 Phase 验收。

**已知限制/前瞻风险**：
- Phase 12（REST API）未建，"手册验证：用 curl 触发完整流程"改为直接 `.delay()` 走
  真实 worker+broker 验证（见上）；curl 端到端验证留给 Phase 12 完成后补做。
- decide.py 尚不消费 `classified.page_type`（Phase 8 遗留特征，非阻塞）。
- R9（p2 极端矢量页原地整页光栅化增强）仍未处理，收敛环本身对矢量页无能为力
  （assemble.py 目前不落地 vector_plans，见 8a 节）——真实作品集在 Phase 13
  端到端测试时需重新评估是否阻塞。

**待完成（按序）**：①用户验收本报告 → ②squash 为正式 Phase 11 commit 并 push →
③开始 Phase 12（REST API 层）。
