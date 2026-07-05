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
# 临时工作区挂载点（compose 里 api 与 worker 共享同一 volume——上传/产物互通）
RUN mkdir -p /data/tmp
ENV STORAGE__TMP_DIR=/data/tmp
EXPOSE 8000
CMD ["python", "-m", "uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
