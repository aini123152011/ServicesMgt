#!/bin/sh
# 启动前置：处理「配置卷首次挂载为空」的情况，并以监督循环方式托管 dnsmasq。
# dnsmasq 缺配置文件时会以空配置启动（不提供任何 DHCP/DNS 服务），因此启动前若配置缺失，
# 就从镜像内置默认配置播种一份，保证 cd services/dhcp && docker compose up -d 即可独立运行。
set -eu

CONF_DIR="/etc/dnsmasq"
CONF_FILE="${CONF_DIR}/dnsmasq.conf"
SEED_FILE="/usr/share/fx-dhcp/dnsmasq.conf.default"
LEASE_DIR="/var/lib/dnsmasq"
LEASE_FILE="${LEASE_DIR}/dnsmasq.leases"

if [ ! -f "${CONF_FILE}" ]; then
    echo "entrypoint: 配置卷缺少 dnsmasq.conf，播种内置默认配置"
    mkdir -p "${CONF_DIR}"
    cp "${SEED_FILE}" "${CONF_FILE}"
fi

# 租约文件放在持久化数据卷里：容器重建后 BMC 仍能拿到同一个地址（续租不换址）
mkdir -p "${LEASE_DIR}"
touch "${LEASE_FILE}"

# 监督循环：reload.sh 终止 dnsmasq 后，循环检测到退出并带新配置重新拉起；
# docker stop 的 SIGTERM 由 stop_handler 转发给 dnsmasq 后退出容器
CHILD_PID=""
stop_handler() {
    if [ -n "${CHILD_PID}" ]; then
        kill "${CHILD_PID}" 2>/dev/null || true
        wait "${CHILD_PID}" 2>/dev/null || true
    fi
    exit 0
}
trap stop_handler TERM INT

while :; do
    # --keep-in-foreground：前台运行，日志进 docker logs（否则 dnsmasq 会自己 daemon 化，
    #   监督循环立刻认为它退出了，形成重启风暴）
    # --log-facility=-：日志写 stderr 而不是 syslog（容器里没有 syslog）
    # --log-dhcp：把 DHCP 交互逐条记进日志，便于排查 BMC 取址问题
    # --conf-file：只读平台渲染的配置（不读 /etc/dnsmasq.d，避免镜像默认配置混入）
    # --user=root：配置由平台以 root 写入数据卷，dnsmasq 默认会降权到 nobody 而写不了租约文件
    dnsmasq --keep-in-foreground --log-facility=- --log-dhcp \
        --conf-file="${CONF_FILE}" \
        --dhcp-leasefile="${LEASE_FILE}" \
        --pid-file=/run/dnsmasq.pid \
        --user=root &
    CHILD_PID=$!
    wait "${CHILD_PID}" || true
    CHILD_PID=""
    echo "entrypoint: dnsmasq 已退出，1 秒后带新配置重启"
    sleep 1
done
