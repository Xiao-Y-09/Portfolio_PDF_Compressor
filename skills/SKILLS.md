# SKILLS.md — 项目 AI 协作 Skill 定义

> 与 AI 协作时，按当前任务所属领域引用对应 Skill 的视角和检查清单。

---

## Skill 1：系统架构师

**职责**：守护整体架构一致性，是七条铁律的第一责任人。

**视角**：
- 任何改动先问："这违反哪条铁律吗？契约有没有被下游修改？"
- 决策（Phase D）与执行（Phase E）的边界是否清晰：decide.py 只算数，compress.py 只干活
- Phase 间数据流只能通过 `backend/app/contracts/` 中的 Pydantic 类型传递
- 把确定性操作前置（字体子集化），把不确定性留给收敛循环

**检查清单**：
- [ ] 新代码是否只消费上游契约、不私自扩展字段
- [ ] 可调参数是否全部来自 config.yaml，无魔法数字
- [ ] 错误是否走 PhaseError，收敛失败是否显式报错而非静默输出

---

## Skill 2：PDF 处理专家

**职责**：Phase 4-7、9-10 中所有 PyMuPDF / fontTools 相关实现。

**视角**：
- PDF 坐标单位是 point（1/72 英寸）；DPI 反算公式：`dpi = image_pixel_width / (bbox.w / 72)`
- 已知的坑：嵌套 XObject、CID 编码字体、SMask 透明通道、旋转图片、伪透明（alpha 全 255）
- 单页/单元素失败要容忍（warning + 继续），不阻塞整个流水线
- 重组时保持 bbox 位置、书签、超链接、表单字段不丢失
- `doc.save(garbage=4, clean=True, deflate=True, deflate_images=False)`——图片已压缩过，不重复压缩

**检查清单**：
- [ ] 加密 PDF、损坏 PDF 是否抛出正确的 PhaseError
- [ ] 提取的 data_ref 指向的文件是否真实存在
- [ ] 输出 PDF 能否被 fitz 重新打开、页数正确

---

## Skill 3：Python 后端专家

**职责**：FastAPI、Celery、Pydantic、存储抽象、测试体系。

**视角**：
- 所有 Phase 间类型用 Pydantic BaseModel，禁止裸 dict / dataclass
- 长任务全部异步化（Celery + Redis），API 层只做触发和查询
- 临时文件生命周期严格管理：任务成败都要清理，定时任务兜底（铁律 5）
- 测试验证正确性而非覆盖率；纯函数模块（decide.py）必须验证确定性（同输入跑 3 次结果一致）

**检查清单**：
- [ ] pytest 全绿；契约类型能 JSON round-trip
- [ ] 上传校验：Content-Type + 首字节 %PDF + 大小限制
- [ ] 失败路径有清理逻辑，session 有 TTL

---

## Skill 4：AWS 部署专家

**职责**：Phase 15 的 EC2 / Docker / Nginx / CloudWatch 部署与运维。

**视角**：
- EC2 起步 t3.large，CPU 密集升 c6i.xlarge；EBS gp3 100GB 做临时工作区
- Nginx：client_max_body_size 500M、proxy_read_timeout 300s、SSL 终结（Let's Encrypt）
- 密钥只走环境变量 / IAM Role，永不进代码和镜像
- CloudWatch：磁盘 >80%、CPU >90% 持续 5 分钟告警；业务指标（任务成功率、平均收敛轮数、平均时长）

**检查清单**：
- [ ] docker-compose up 本地全 stack 可启动
- [ ] .env / certs 不在 git 中
- [ ] 公网健康检查端点可达，日志进 CloudWatch
