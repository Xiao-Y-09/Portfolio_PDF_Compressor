# Celery worker 容器（手册 Phase 15 第二项）：与 API 同基座、同布局。
#   docker build -f backend/celery.Dockerfile -t pdfcompress-worker .
# 并发说明（deploy/ec2/instance-config.md）：t3.medium(4GB) 默认 --concurrency=1
# （单份 100MB 级作品集峰值内存可观）；t3.large+ 可升到 2。

FROM python:3.11-slim AS builder
WORKDIR /build
COPY backend/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

FROM python:3.11-slim
COPY --from=builder /install /usr/local
WORKDIR /app/backend
COPY config.yaml /app/config.yaml
COPY backend/app /app/backend/app
# 不装系统字体包：pymupdf 静态编译 MuPDF，get_pixmap 从不查询 fontconfig/
# 系统字体（内置 base14 + CJK 回退），实测容器有无字体包渲染逐像素一致。
# "压缩后文字消失"真因在字体子集化链路（见 backend/Dockerfile 同注释）。
RUN mkdir -p /data/tmp
ENV STORAGE__TMP_DIR=/data/tmp
CMD ["python", "-m", "celery", "-A", "app.queue.celery_app", "worker", \
     "--loglevel=info", "--concurrency=1"]
