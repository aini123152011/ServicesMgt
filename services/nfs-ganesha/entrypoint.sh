#!/bin/bash
# nfs-ganesha 服务启动入口：
# 1. 初始化空配置卷
# 2. 准备数据目录
# 3. 启动 rpcbind 守护进程
# 4. 监督循环拉起 ganesha.nfsd（支持 reload 杀死后自动重启）
set -euo pipefail

CONFIG_DIR="/etc/ganesha-bmc"
DEFAULTS_DIR="/etc/ganesha-bmc.default"
DATA_DIR="/data/nfs"

# 1. 空卷播种
if [ ! -f "${CONFIG_DIR}/ganesha.conf" ]; then
    echo "entrypoint: 配置目录为空，从默认模板初始化..."
    mkdir -p "${CONFIG_DIR}"
    if [ -d "${DEFAULTS_DIR}" ]; then
        cp -a "${DEFAULTS_DIR}/." "${CONFIG_DIR}/"
    fi
fi

# 2. 数据目录准备
mkdir -p "${DATA_DIR}"
chmod 777 "${DATA_DIR}"

# 运行目录：ganesha.nfsd 启动时要写 /var/run/ganesha/ganesha.pid，目录不存在会
# 直接 FATAL 退出（errno 2）并陷入监督循环重启。容器内 /var/run 是空 tmpfs，
# 镜像构建期建不出来，必须在这里补建
mkdir -p /var/run/ganesha

# 3. 启动 rpcbind
mkdir -p /run/rpcbind
if ! pgrep rpcbind >/dev/null 2>&1; then
    rpcbind
fi

STOPPED=0
trap 'STOPPED=1; pkill -TERM ganesha.nfsd || true; exit 0' SIGTERM SIGINT

echo "entrypoint: 启动 NFS-Ganesha 监督循环..."
while [ "${STOPPED}" -eq 0 ]; do
    ganesha.nfsd -F -L STDOUT -f "${CONFIG_DIR}/ganesha.conf" &
    GANESHA_PID=$!
    wait "${GANESHA_PID}" || true
    if [ "${STOPPED}" -eq 1 ]; then
        break
    fi
    echo "entrypoint: ganesha.nfsd 已退出，1 秒后带新配置重启..."
    sleep 1
done
