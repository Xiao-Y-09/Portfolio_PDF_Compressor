# PROJECT_STATE.md — 会话锚点

> **用途**：新对话恢复上下文的唯一入口。配合 CLAUDE.md（宪法/铁律/工作规则）+
> docs/ 三份真源文档使用，无需回看历史验收报告。
> **维护规则**：每个 Phase 验收通过后更新本文件（状态节 + Phase 概览行）。
> 最后更新：2026-07-04（Phase 8 进行中，见文末状态节）。

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
| 8 | 决策引擎 decide.py（**进行中**，见文末状态节） | WIP `21a6953`→`56b9dd1` |

远程：https://github.com/Xiao-Y-09/Portfolio_PDF_Compressor （main，已推送至 afa3c58）

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
- 验收通过后：squash 为 "Phase 9: compression executor (single-round)" + push
