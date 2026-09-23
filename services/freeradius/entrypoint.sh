#!/bin/sh
# 启动前置：处理「配置卷首次挂载为空」的情况，并以监督循环方式托管 freeradius。
# freeradius 缺 clients.conf/authorize 会启动失败或拒绝一切请求，因此启动前若配置缺失，
# 就从镜像内置默认配置播种一份，保证 cd services/freeradius && docker compose up -d 即可独立运行。
set -eu

CONF_DIR="/etc/freeradius/3.0/bmc"
SEED_DIR="/usr/share/bmc-freeradius"

mkdir -p "${CONF_DIR}" /var/log/freeradius
if [ ! -f "${CONF_DIR}/clients.conf" ]; then
    echo "entrypoint: 配置卷缺少 clients.conf，播种内置默认配置"
    cp "${SEED_DIR}/clients.conf.default" "${CONF_DIR}/clients.conf"
fi
if [ ! -f "${CONF_DIR}/authorize" ]; then
    echo "entrypoint: 配置卷缺少 authorize，播种内置默认配置"
    cp "${SEED_DIR}/authorize.default" "${CONF_DIR}/authorize"
fi

# 监督循环：reload.sh 终止 freeradius 后，循环检测到退出并带新配置重新拉起；
# docker stop 的 SIGTERM 由 stop_handler 转发给 freeradius 后退出容器
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
    # -f：前台运行（不 fork，监督循环才能等到它）；-l stdout：日志走 stdout 进 docker logs
    # （容器里没有 syslog；-l stdout 后 -X 调试输出也一并可见，排障方便）
    # 不写 -X：那是全量调试模式，认证逐条打日志会很吵；需要时临时 docker exec 起一个 -X 实例
    freeradius -f -l stdout &
    CHILD_PID=$!
    wait "${CHILD_PID}" || true
    CHILD_PID=""
    echo "entrypoint: freeradius 已退出，2 秒后带新配置重启"
    sleep 2
done
