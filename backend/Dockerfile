# API 容器（手册 Phase 15 第一项）：多阶段构建。
# 构建上下文 = 仓库根目录（config.yaml 在根，settings.py 按 parents[3] 解析），
# 容器内布局 /app/config.yaml + /app/backend/app/... 与仓库一致。
#   docker build -f backend/Dockerfile -t pdfcompress-api .

FROM python:3.11-slim AS builder
WORKDIR /build
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim
COPY --from=builder /install /usr/local
WORKDIR /app/backend
COPY config.yaml /app/config.yaml
COPY backend/app /app/backend/app
# 不装系统字体包：pymupdf 静态编译 MuPDF（内置 base14 + CJK 回退字体），
# 渲染从不查询 fontconfig/系统字体——实测（2026-07-15）容器有无字体包渲染
# 逐像素一致。"压缩后文字消失"真因在字体子集化链路，见 pipeline/extract.py
# _extract_fonts 与 preprocess.py _subset_font 的修复注释。
# 临时工作区挂载点（compose 里 api 与 worker 共享同一 volume——上传/产物互通）
RUN mkdir -p /data/tmp
ENV STORAGE__TMP_DIR=/data/tmp
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
