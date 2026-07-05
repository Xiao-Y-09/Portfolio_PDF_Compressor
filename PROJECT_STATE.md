# PROJECT_STATE.md — 会话锚点

> **用途**：新对话恢复上下文的唯一入口。配合 CLAUDE.md（宪法/铁律/工作规则）+
> docs/ 三份真源文档使用，无需回看历史验收报告。
> **维护规则**：每个 Phase 验收通过后更新本文件（状态节 + Phase 概览行）。
> 最后更新：2026-07-05（**全部 15 个 Phase 完成**，见 §13 终版总结；下一步：用户按 deploy/ec2/RUNBOOK.md 上线）。

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
| 11 | 任务编排（Tier 分级降级状态机 + Celery + review 断点 + 估算体系三段修复） | `6dbbe22` |
| 12 | REST API 层（/api/v1 六路由 + CORS + PhaseError→HTTP 映射 + Celery FAILURE 序列化修正） | `f9275d4` |
| 13 | 测试与验证（e2e 矩阵 12/12 + 真实并发 10/10 + 性能基线 + 覆盖率 85%） | `1320865` |
| 14 | Web 前端（Next.js 五态机 + review 网格 + 可行动错误 + tier 警告 + Playwright e2e） | `da3098f` |
| 15 | AWS EC2 部署（Docker ×2 + compose + nginx 双配置 + CloudWatch + RUNBOOK） | `6c8826e` |

远程：https://github.com/Xiao-Y-09/Portfolio_PDF_Compressor （main，已推送至 `6c8826e`）

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

## 9. Phase 11 状态：✅ 完成并收官（2026-07-05，正式 commit `6dbbe22` 已 push）

**范围**（12 个 WIP squash）：编排层（orchestrator + tasks 替换 stub）+ Tier 分级
压缩系统（产品决策）+ 估算体系三段修复 + 手册"实施期架构演进"章节。
目视验收结论：**质量能上线**。全量 204 passed + 1 skipped + 1 xfailed。

**架构一览**：
- orchestrator.py：run_until_classify（A→B→C，AWAITING_REVIEW 断点）→
  resume_after_review（D0 一次 → Tier 降级状态机）；工作区/会话生命周期 +
  30 分钟过期扫描接入 main.py 定时清理
- Tier 状态机：in_place → hybrid（decide.select_whole_pages 纯函数贪心选页，
  保留成本按原地真实语义）→ full_raster（tier3 独立 floor 72/30/150）；
  review 只断一次，降级事后告知（tier_used/warning）；耗尽才
  CONVERGENCE_FAILED + ConvergenceDiagnostics（可行动文案）
- 整页光栅化原语：渲染归 compress、替换归 assemble（先 insert 后清空，失败原子）
- 估算三段修复：①Tier 边界真实 assemble 判定（裁决 1）②环内基准
  target−plan.raster_budget（"用错字段"修复）③JPEG bpp 实测标定
  0.04/0.12→0.015/0.105（scripts/calibrate_jpeg_bpp.py，764 点，工作区间偏差 <20%）

**6 场景验收（三轮对比，最终产物 preview_output/tier_final_*.pdf）**：

| 场景 | 双 bug 首轮 | 基准修复后 | 标定后（最终） | tier_used |
|------|------|------|------|------|
| p1@5MB | 3.12（-40.6%） | 3.12（-40.6%） | **4.27（-18.5%）** | full_raster |
| p1@10MB | 6.08（-42.0%） | 6.08（-42.0%） | **8.79（-16.2%）** | full_raster |
| p2@5MB | 3.46（-34.1%） | 3.46（-34.1%） | **4.80（-8.4%）✅** | full_raster |
| p2@10MB | 6.50（-38.0%） | 6.50（-38.0%） | **10.36（-1.2%）✅** | full_raster |
| p3@5MB | 2.75（-47.6%） | 3.19（-39.1%） | **4.77（-9.0%）✅** | full_raster |
| p3@10MB | 6.86（-34.6%） | 6.24（-40.5%） | **10.56（+0.7%）✅** | full_raster |

零超标（铁律 7 全程成立）；p2@5MB 从硬性 TARGET_TOO_SMALL 变为达标交付。
hybrid 结论：在 p1/p2@10MB 上物理上限 +17~25%（Tier 2 标准 floor + 未选页保留
内容决定），降级判断正确——不是调优问题。

**风险台账更新**：R7（估算系数）✅ 关闭（实测标定；PNG 系数为死路径一并关闭）；
R9（原地矢量缺口）✅ 由 Tier 2 整页光栅化补上；R10（rebuild 真透明退化）不变，
xfail 在案；R4（固定开销 ±10%）遗留 Phase 13；R5（裸 CFF）、R6（git 身份）不变。

**上线后优化清单（不阻塞 Phase 12，按价值排序）**：
1. hybrid 可用性：两个 10MB 场景 hybrid 上限在带外——可探索调低 tier2_budget_margin
   多选页、或 Tier 2 允许 dpi 降至 120（需目视裁决），让更多任务停在文字保留档
2. q≥95 估算非线性（PIL 色度子采样阶跃，低估 38-41%，方向安全）——Phase 13 可做
   分段模型或压低实际使用的 quality_ceiling 到 92
3. in_place/hybrid 层 est-real 残差（floor 用 planned 成本 vs 原地保留 original）——
   烧轮次不伤正确性；Phase 13 与 R4 一起校准
4. 元素级 bpp 比整页高 20-30%（单一系数折中）——如需更准可分上下文 config 键
5. 多 Tier 下 progress 50-90% 带被复用（前端观感）——Phase 14 议题
6. tier_acceptance.py 扩 15/20MB 档进例行回归
7. 前端 UI 精修（2026-07-05 用户定：收集真实用户反馈后统一改，本期只求功能对）

**Phase 12（REST API 层）入口条件**：

| 需要 | 来源 | 状态 |
|------|------|------|
| compress_pdf_task / resume_compression_task（AWAITING_REVIEW/SUCCESS 载荷含 tier_used/warning） | Phase 11 tasks.py | ✅ |
| PhaseError.model_dump()（含 diagnostics）进错误响应体 | 契约层 | ✅ |
| storage.save_upload/get_path/delete + cleanup_expired 定时任务 | Phase 2 | ✅ |
| config api 段（max_upload_mb=500 / cors_origins） | Phase 2 config | ✅ |
| main.py lifespan（清理循环）可挂路由 | Phase 2/11 | ✅ |
| 手册 Phase 12 规格（upload/compress/tasks/resume/download/health + CORS + 错误中间件 + test_api） | 手册 §Phase 12 | ✅ 在案 |

## 10. Phase 12 状态：✅ 完成并收官（2026-07-05，正式 commit `f9275d4` 已 push）

- /api/v1 六路由：upload（三关校验 415/415/413）/ compress（404 早失败）/
  tasks 五态映射（SUCCESS meta 带 tier_used/warning；FAILURE meta 带完整
  PhaseErrorData 含 diagnostics）/ resume / download（attachment，复用 300s
  retention 清扫）/ health（storage 写探针 + Redis TCP 探针）
- CORS 读 config；PhaseError→HTTP：SESSION_EXPIRED=410、CONVERGENCE_FAILED=422
  （诊断进响应体）、INVALID_PDF/ENCRYPTED_PDF/TARGET_TOO_SMALL=422、其余 500；
  未捕获异常 500 不泄露堆栈
- 披露性偏离（手册"实施期架构演进"演进 4）：Celery JSON 后端 FAILURE 结果必须是
  序列化异常，手册伪码的 update_state(FAILURE, meta=dict) 读回即崩 → 任务只 raise，
  PhaseError.args 对齐构造签名（diagnostics 转 dict）实现无损重建
- 测试：test_api 10 用例（httpx ASGITransport + eager Celery，完整流程 + 三关负例 +
  错误映射 + health）；真实 uvicorn 启动 curl 验证；全量 214 passed + 1 skipped + 1 xfailed

**Phase 13（测试与验证）入口条件**：

| 需要 | 来源 | 状态 |
|------|------|------|
| 全链路可编程调用（orchestrator 直调 + API 层） | Phase 11/12 | ✅ |
| 6 场景验收脚本可扩展为 e2e 矩阵 | scripts/tier_acceptance.py | ✅ |
| 真实样本 3 份（71/99/33MB；无 300MB+ 超大样本，边界矩阵按现有 3 份跑） | fixtures | ✅ |
| 真实 Celery worker 验证方法（PowerShell 分离进程 + 隔离 db） | Phase 11 手动验证先例 | ✅ |
| pytest-cov（含 html 报告） | Phase 11 步骤 4 引入 | ✅ |

## 11. Phase 13 状态：✅ 完成并收官（2026-07-05，正式 commit `1320865` 已 push）

- e2e：错误路径恒跑（加密/非 PDF/损坏/截断）+ RUN_E2E=1 矩阵 3 样本 × 5/10/15/20MB
  = 12/12 全过（零超标/页数守恒/链接书签保留/单组 <300s）
- 并发：真实 worker（threads×4，隔离 db14）10 任务同时提交——10/10 完成、
  标记审计 0 混淆、工作区全清（scripts/concurrency_check.py 可复用）
- 性能基线（scripts/perf_baseline.py + .pytest_tmp/perf_baseline.json）：
  p1 100.3s / p2 53.4s / p3 10.6s；extract 主矛盾（33-55s），p1 assemble 38s
  （garbage=4 全量重写），decide 恒 0.00s——Phase 15 部署后的回归锚点
- 覆盖率（含矩阵）：TOTAL 85%；assemble 62→88%（真实样本自然覆盖字体替换）、
  tasks 100%、orchestrator 97%、decide 98%。已知低覆盖均为退役/未启用路径
  （assemble_rebuild 47% / s3 25% / main lifespan 41%——启动已由真实 uvicorn 验证）
- 边界：session TTL 到期全链路（SESSION_EXPIRED + 超时扫描清工作区）、
  截断 PDF 不挂起不崩溃、同 file_id 并发隔离
- 披露：无 300MB+ 超大样本（用户提供后可入矩阵）；手册 5 份合成样本类别
  按"真实样本铁律"以 3 份真实作品集执行

**Phase 14（Web 前端）入口条件**：

| 需要 | 来源 | 状态 |
|------|------|------|
| REST API 全量可用（upload/compress/tasks 五态/resume/download/health） | Phase 12 | ✅ |
| AWAITING_REVIEW 载荷（classified + session_id，complexity_score 供高亮） | Phase 11/12 | ✅ |
| SUCCESS 载荷（download_id/final_size_mb/tier_used/warning） | Phase 11/12 | ✅ |
| FAILURE 载荷（PhaseErrorData 含 CONVERGENCE_FAILED diagnostics 可行动建议） | Phase 11/12 | ✅ |
| 进度百分比分配（split 10/extract 30/classify 40/preprocess 50/converge 90/assemble 100） | 架构设计 §3.4 | ✅ |
| CORS 允许 localhost:3000 | config api 段 | ✅ |
| 手册 Phase 14 规格 + 用户 6 项关注点（含 client session token 新增） | 手册 + 2026-07-05 指示 | ✅ 在案 |

## 12. Phase 14 状态：✅ 完成并收官（2026-07-05，正式 commit `da3098f` 已 push，目视通过）

- 前端：Next.js 14 App Router + TS + Tailwind + Zustand + Axios；首页（dropzone/
  目标选择自动预设场景/场景卡）+ /task/[taskId] 五态机（2s 轮询、后端驱动进度、
  review 网格含真实缩略图+分类下拉+跳过/激进+complexity≥0.5 高亮+光栅化预告、
  成功视图 tier_used/warning + 重试更大目标入口、失败视图逐 code 文案 +
  CONVERGENCE_FAILED 诊断可行动建议——用户永远有下一步，绝无堆栈）
- 失败页面语义（用户确认）：失败 = 当前选择做不到而非绝对不可能；三场景各有
  对应建议（选更大目标/减少 skip/降级重试）
- 后端增项（Phase 14 规格倒逼，已披露）：缩略图路由（渲染在 orchestrator，
  会话期有效）+ X-Client-Session 关联日志中间件
- 验证：npm build 干净；Playwright e2e 2/2（真实 uvicorn+worker 全流程含
  改分类+标跳过+下载校验）；后端回归 221+13s+1x；截图 preview_output/frontend/
- 环境注记：本机 3000 端口被无关应用占用，e2e 用 3001 + CORS env 覆盖

**Phase 15（AWS EC2 部署——最终 Phase）入口条件**：

| 需要 | 来源 | 状态 |
|------|------|------|
| 后端全链路容器化就绪（uvicorn 入口 + celery worker + redis 依赖明确） | Phase 2/11/12 | ✅ |
| 前端 Vercel 可部署（NEXT_PUBLIC_API_BASE 环境变量化） | Phase 14 | ✅ |
| CORS 来源 env 可覆盖（API__CORS_ORIGINS JSON） | Phase 12/14 实证 | ✅ |
| 性能基线（部署后对照） | Phase 13 | ✅ |
| 架构拓扑规格（Nginx/SSL/EBS/CloudWatch/IAM） | 架构设计 §6 | ✅ |
| 用户裁决：t3.medium 起步 / xiao-projects.com 子域方案 / Vercel+EC2 分域 | 2026-07-05 指示 | ✅ 在案 |

## 13. 🏁 项目终版总结（2026-07-05）：全部 15 个 Phase 完成

**部署入口**：`deploy/ec2/RUNBOOK.md`（菜单级精度，逐步含成功标志；
配套 instance-config.md 升级触发条件 / monitoring.md 指标查询模板）。

**Phase 15 状态**：✅ 完成并收官（正式 commit `6c8826e` 已 push）。
本地验证到手册上限：四容器栈 healthy、完整流程经 nginx 代理走通、
指标锚点实拍确认；云端步骤（AWS 账号/域名/EC2）物理上需用户亲手执行，
全部落在 RUNBOOK。三条披露性偏离（worker 副本 2→1 / logrotate 不适用 /
云端验证移交 Runbook）用户已批准。

**最终测试状态**：221 passed + 13 skipped（12 为 RUN_E2E 门控矩阵 + 1 deferred）
+ 1 xfailed；e2e 矩阵 12/12；真实并发 10/10 零混淆；覆盖率 85%；
Playwright 前端 e2e 2/2；容器化端到端 1/1。

**上线后迭代清单（汇总，按价值排序）**：
1. 前端 UI 精修——收集真实用户反馈后统一改（2026-07-05 用户定）
2. 估算精度 Phase 13 残留：q≥95 线性公式低估 38-41%（PIL 色度子采样阶跃，
   方向安全）；in_place/hybrid 层 est-real 残差（floor 用 planned 成本 vs
   原地保留 original，烧轮次不伤正确性）；元素级 bpp 比整页高 20-30%
   （单一系数折中，如需更准可分上下文 config 键）；R4 固定开销 ±10% 校准
3. 超大 PDF 样本覆盖：300MB+ 样本入 e2e 矩阵（等用户提供真实样本）
4. worker 扩容触发条件监控：上线后盯 CloudWatch（OOM→t3.large；
   CPU 持续 >80%→c6i.xlarge + worker×2，instance-config.md 有判定表）
5. hybrid 可用性调优：两个 10MB 场景 hybrid 物理上限在带外——可探索调低
   tier2_budget_margin 多选页、或 Tier 2 允许 dpi 降至 120（需目视裁决）
6. 多 Tier 下 progress 50-90% 带被复用（前端观感）
7. tier_acceptance.py 扩 15/20MB 档进例行回归；CI/CD（GitHub Actions→ECR→EC2）
   等发布频率上来再上

**关键架构资产（接手者阅读顺序）**：CLAUDE.md（铁律）→ 本文件 →
docs/ 三份真源 → 手册"实施期架构演进"章节（四条演进：Tier 系统 /
边界真实判定+估算三段修复 / 原地手术 / Celery FAILURE 序列化）。
