#!/usr/bin/env bash
# EC2 首次配置脚本（手册 Phase 15 第五项；Ubuntu 24.04 LTS）。
# 在全新实例上以 ubuntu 用户执行：
#   curl -fsSL https://raw.githubusercontent.com/Xiao-Y-09/Portfolio_PDF_Compressor/main/deploy/ec2/setup.sh | bash
# 或 clone 后 bash deploy/ec2/setup.sh
# 幂等：重复执行安全。完成后按 RUNBOOK §6 继续（.env + 证书 + 启动）。

set -euo pipefail

REPO_URL="https://github.com/Xiao-Y-09/Portfolio_PDF_Compressor.git"
APP_DIR="/opt/pdfcompress"

echo "==> [1/5] 系统更新 + Docker 安装"
sudo apt-get update -y
sudo apt-get install -y ca-certificates curl git
if ! command -v docker >/dev/null 2>&1; then
  curl -fsSL https://get.docker.com | sudo sh
fi
sudo usermod -aG docker "$USER"

echo "==> [2/5] 2GB swap（t3.medium 4GB 内存的溢出保险，已存在则跳过）"
if ! sudo swapon --show | grep -q swapfile; then
  sudo fallocate -l 2G /swapfile
  sudo chmod 600 /swapfile
  sudo mkswap /swapfile
  sudo swapon /swapfile
  echo '/swapfile none swap sw 0 0' | sudo tee -a /etc/fstab
fi

echo "==> [3/5] 拉取代码到 ${APP_DIR}"
if [ ! -d "${APP_DIR}/.git" ]; then
  sudo mkdir -p "${APP_DIR}"
  sudo chown "$USER":"$USER" "${APP_DIR}"
  git clone "${REPO_URL}" "${APP_DIR}"
else
  git -C "${APP_DIR}" pull
fi

echo "==> [4/5] 生成 .env（若不存在）"
if [ ! -f "${APP_DIR}/deploy/.env" ]; then
  cp "${APP_DIR}/deploy/.env.example" "${APP_DIR}/deploy/.env"
  echo "    !! 请编辑 ${APP_DIR}/deploy/.env 填写 CORS_ORIGINS 与 AWS_REGION"
fi

echo "==> [5/5] nginx 引导配置（HTTP-only，证书签发前）"
if [ ! -f "${APP_DIR}/deploy/nginx/active.conf" ]; then
  cp "${APP_DIR}/deploy/nginx/api.http.conf" "${APP_DIR}/deploy/nginx/active.conf"
fi

echo ""
echo "setup 完成。后续步骤（RUNBOOK §6）："
echo "  1. 重新登录 SSH（使 docker 组生效）"
echo "  2. 编辑 ${APP_DIR}/deploy/.env"
echo "  3. cd ${APP_DIR}/deploy && docker compose up -d --build"
echo "  4. 签发证书（RUNBOOK §6.3）后切换 SSL 配置"
