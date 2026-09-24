#!/bin/sh
# 启动前置：处理"配置卷首次挂载为空"的情况，并以监督循环方式托管 rsyslogd。
# rsyslogd 缺少配置文件会直接启动失败，因此启动前若配置缺失，
# 就从镜像内置默认配置播种一份，保证 cd services/rsyslog && docker compose up -d 即可独立运行。
set -eu

CONF_DIR="/etc/rsyslog"
CONF_FILE="${CONF_DIR}/rsyslog.conf"
SEED_FILE="/usr/share/bmc-rsyslog/rsyslog.conf.default"

if [ ! -f "${CONF_FILE}" ]; then
    echo "entrypoint: 配置卷缺少 rsyslog.conf，播种内置默认配置"
    mkdir -p "${CONF_DIR}"
    cp "${SEED_FILE}" "${CONF_FILE}"
fi

# 归档根目录兜底创建（数据卷挂载点）；模板里日期/来源 IP 子目录由 rsyslogd
# 按 DynaFile 目录段自动创建（受 $DirCreateMode 控制），无需在此预建
mkdir -p /var/log/bmc

# 监督循环：reload.sh 终止 rsyslogd 后，循环检测到退出并带新配置重新拉起；
# docker stop 的 SIGTERM 由 stop_handler 转发给 rsyslogd 后退出容器
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
    # -n：前台不 fork；-iNONE：不写 pidfile（容器内无意义，且避免重启循环残留旧 pid 误判）
    rsyslogd -n -iNONE -f "${CONF_FILE}" &
    CHILD_PID=$!
    wait "${CHILD_PID}" || true
    CHILD_PID=""
    echo "entrypoint: rsyslogd 已退出，2 秒后带新配置重启"
    sleep 2
done
