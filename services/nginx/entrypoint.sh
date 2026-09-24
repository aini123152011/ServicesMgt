#!/bin/bash
# nginx 服务启动入口：
# 1. 初始化空配置卷（从 /etc/nginx-bmc.default/ 播种默认配置）
# 2. 准备自签 SSL/TLS 证书目录（若无平台下发的证书则自动生成自签证书兜底）
# 3. 准备数据目录 /data/nginx 权限
# 4. 前台启动 nginx 主进程
set -euo pipefail

CONFIG_DIR="/etc/nginx-bmc"
DEFAULTS_DIR="/etc/nginx-bmc.default"
SSL_DIR="${CONFIG_DIR}/ssl"
DATA_DIR="/data/nginx"

# 1. 空卷播种
if [ ! -f "${CONFIG_DIR}/nginx.conf" ]; then
    echo "entrypoint: 配置目录为空，从默认模板初始化..."
    mkdir -p "${CONFIG_DIR}"
    if [ -d "${DEFAULTS_DIR}" ]; then
        cp -a "${DEFAULTS_DIR}/." "${CONFIG_DIR}/"
    fi
fi

# 2. SSL/TLS 证书兜底检查
mkdir -p "${SSL_DIR}"
if [ ! -f "${SSL_DIR}/cert.pem" ] || [ ! -f "${SSL_DIR}/key.pem" ]; then
    echo "entrypoint: 未检测到已有 SSL 证书，生成默认自签名证书..."
    openssl req -x509 -newkey rsa:2048 -nodes -days 3650 \
        -subj "/CN=fx-nginx" \
        -out "${SSL_DIR}/cert.pem" \
        -keyout "${SSL_DIR}/key.pem" >/dev/null 2>&1
    chmod 600 "${SSL_DIR}/key.pem"
    chmod 644 "${SSL_DIR}/cert.pem"
fi

# 3. 数据目录权限准备
mkdir -p "${DATA_DIR}"
chown -R www-data:www-data "${DATA_DIR}"
chmod 775 "${DATA_DIR}"

# 4. 前台启动 nginx
echo "entrypoint: 启动 nginx 服务..."
exec nginx -g 'daemon off;' -c "${CONFIG_DIR}/nginx.conf"
