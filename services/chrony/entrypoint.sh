#!/bin/sh
# 启动前置：处理"配置卷首次挂载为空"的情况。
# chronyd 缺少 /etc/chrony/chrony.conf 会直接启动失败，因此启动前若配置缺失，
# 就从镜像内置默认配置播种一份，保证 cd services/chrony && docker compose up -d 即可独立运行。
set -eu

CONF_DIR="/etc/chrony"
CONF_FILE="${CONF_DIR}/chrony.conf"
SEED_FILE="/usr/share/bmc-chrony/chrony.conf.default"

if [ ! -f "${CONF_FILE}" ]; then
    echo "entrypoint: 配置卷缺少 chrony.conf，播种内置默认配置"
    mkdir -p "${CONF_DIR}"
    cp "${SEED_FILE}" "${CONF_FILE}"
fi

# 上次异常退出可能残留 pidfile，导致重启循环里误报"已有 chronyd 运行"
rm -f /run/chrony/chronyd.pid

# exec 让 chronyd 取代 shell 成为 1 号进程，容器信号（停止/重启）直达进程。
# -x：不调整本机时钟（容器默认无 CAP_SYS_TIME，宿主机时钟也不该被容器修改），
# 只对外提供 NTP 服务；需要让容器校准宿主机时钟时，在 compose 里加
# cap_add: [SYS_TIME] 并去掉 -x。
exec chronyd -n -x -f "${CONF_FILE}"
