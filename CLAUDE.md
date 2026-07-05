# CLAUDE.md — 项目宪法（每次 AI 对话自动加载）

> 本文件是本项目所有 AI 协作的最高约束。任何代码改动前先读本文件；与本文件冲突的实现一律不得进入代码库。

---

## 一、唯一功能真源

功能只依据 `docs/` 下三份文档实现，**文档未定义的功能不进入实现**：

1. `docs/系统架构设计.md` — 整体架构、Phase 划分、数据流、部署拓扑
2. `docs/压缩决策引擎.md` — Phase D（决策引擎）的完整规格
3. `docs/数据契约.md` — 所有 Phase 间传递的数据类型定义

> 在 Phase 1 完成、三份文档就绪之前，以项目根目录的《PDF压缩SaaS构建手册.md》与《PhaseD_压缩参数决策引擎.md》为唯一真源。

---

## 二、七条铁律（不可推翻，违反任何一条立即停止）

1. **决策与执行分离**：Phase D（decide.py）不做压缩，Phase E（compress.py）不做决策。决策是纯函数，可反复调用；执行是昂贵操作，只在决策稳定后触发。
2. **Phase D 纯函数**：`backend/app/pipeline/decide.py` 内部禁止文件 I/O、网络调用、图像处理库调用（PIL/OpenCV/fitz），只做数学运算和 config 读取。相同输入必须产出完全相同的输出。
3. **只降不升**：任何参数调整不能让元素变得比原始更大——不升采样、不升质量、不升 DPI。
4. **面积感知**：压缩力度必须考虑元素的 `area_ratio = element_bbox_area / page_area`，小图和大图不同对待。
5. **临时文件强制清理**：用户上传的 PDF 处理完成后（无论成功失败）必须在 5 分钟内删除，永不持久化。
6. **输入输出契约不可下游修改**：Phase N 输出的数据结构一旦定义（`backend/app/contracts/`），Phase N+1 只能消费不能修改；如需调整必须回到契约层重新定义。
7. **收敛失败不静默输出**：Binary search 5 轮内未达标必须返回明确错误提示，不允许输出超出用户目标 15% 以上的文件。

---

## 三、代码目录职责

| 路径 | 职责 |
|------|------|
| `backend/app/main.py` | FastAPI 入口 |
| `backend/app/config/` | 配置加载（pydantic-settings + YAML） |
| `backend/app/api/` | REST 路由 handler |
| `backend/app/contracts/` | 所有 Phase 间的数据类型定义（Pydantic 模型，禁止裸 dict/dataclass） |
| `backend/app/storage/` | 文件存储抽象（本地 + S3 可选） |
| `backend/app/pipeline/split.py` | Phase 4 拆页 |
| `backend/app/pipeline/extract.py` | Phase 5 元素提取 |
| `backend/app/pipeline/classify.py` | Phase 6 页面分类 |
| `backend/app/pipeline/preprocess.py` | Phase 7 字体子集化 + 固定开销 |
| `backend/app/pipeline/decide.py` | Phase 8 压缩决策（**纯函数**） |
| `backend/app/pipeline/compress.py` | Phase 9 压缩执行（**唯一允许调用 PIL 的地方**） |
| `backend/app/pipeline/assemble.py` | Phase 10 PDF 重组 |
| `backend/app/pipeline/orchestrator.py` | Phase 11 流水线编排 + 收敛循环 |
| `backend/app/queue/` | 任务队列（Celery） |
| `frontend/` | Next.js 前端 |

---

## 四、验证命令

```bash
# 单元测试
cd backend && pytest tests/ -v

# 应用自检
cd backend && python -m app.main --check
```

### 铁律 grep 检查（附录 A，期望全部无结果）

```bash
# Phase D 内无文件 I/O
grep -rE "open\(|Path\(|os\.remove|shutil" backend/app/pipeline/decide.py

# Phase D 内无图像处理库
grep -rE "import PIL|import cv2|from PIL|from cv2|import fitz" backend/app/pipeline/decide.py

# Phase D 内无网络调用
grep -rE "import requests|import httpx|urllib" backend/app/pipeline/decide.py

# 契约类型没有被下游重复定义（期望：只在 contracts/ 目录下有 class 定义）
grep -rE "class RasterPlan|class VectorPlan|class CompressionPlan" backend/app/ | grep -v contracts

# pipeline/ 下无硬编码的 quality/dpi 魔法数字
grep -rE "quality\s*=\s*[0-9]+|dpi\s*=\s*[0-9]+" backend/app/pipeline/ | grep -v "settings\.\|config\."
```

---

## 五、工作方式约定

- 严格按 Phase 顺序执行（Phase 0 → 15），当前 Phase 未通过验证不得开始下一个。
- **WIP checkpoint**：每完成一个 Phase 内部的显著里程碑（主要文件写完 / 测试全绿），主动 commit 一个 WIP checkpoint，message 前缀 "WIP:"。Phase 验收通过后用 `git reset --soft` 合并为正式 commit。目的：任何时刻会话中断都有明确恢复点。
- **显式声明直接依赖**：任何被我们代码直接 import 的包，必须在 `backend/requirements.txt` / `frontend/package.json` 中显式声明，不允许依赖传递依赖（transitive dependency）。
- **接口 look-ahead**：定义抽象接口/基类/契约类型时，实现方必须预先扫描本项目手册中后续 Phase 的调用点，把所有会被调用的方法/字段一次性纳入。禁止后期 Phase 追加接口方法或契约字段——如需追加，必须回到接口定义 Phase 显式修订，不允许静默扩展。
- **Producer-Consumer 审计**：每个 Phase 完成后，对本 Phase 新增/修改的所有字段执行 Producer-Consumer 审计：
  1. 每个字段找出其所有 Producer 位置和 Consumer 位置
  2. 验证每个 Consumer 需求都能被 Producer 满足（时机、精度、单位）
  3. 报告中列出：孤儿字段（无 Consumer，可考虑删除）、凭空字段（无 Producer，必须补 Producer）
  4. 任何无法在本 Phase 解决的 DAG 断链，停下报告，不擅自修改契约。
- **数据驱动的前瞻风险**：每个 Phase 验收报告可选包含"数据驱动的前瞻风险"章节——不做决策，只记录本 Phase 采集的真实数据对下游 Phase 的暗示。用户选择立即处理或推到对应 Phase。
- **实施期架构演进记录**（2026-07-05 新增）：每个 Phase 验收结束时，若本 Phase 发生了**原手册未预见的重大架构决策**（换路线、新机制、推翻原设计假设），必须按固定格式（为什么原手册不能满足 / 发现过程 / 最终方案 / 契约与架构变化）追加到《PDF压缩SaaS构建手册.md》末尾的"实施期架构演进"章节。这是未来接手者的关键背景，与三份真源文档同等重要。
- 所有可调参数从 `config.yaml`（`settings.compression`）读取，代码中不出现魔法数字。
- 引入手册未列出的新依赖、修改已完成 Phase、契约不够用时：**停下来问用户**，不自作主张。
- 测试用样本 PDF 必须由用户提供真实作品集，不得用生成的假 PDF 蒙混。
