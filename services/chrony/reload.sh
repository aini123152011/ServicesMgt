#!/bin/bash
# 统一生效入口：平台通过 docker exec bmc-chrony /reload.sh 触发配置生效。
# 热加载依据：chronyd 收到 SIGHUP 会重读 /etc/chrony/chrony.conf 并重新应用配置，
# NTP 服务不中断（chronyd 手册 SIGNALS 说明）。
# NOTE: 少数指令（如 bindcmdaddress、user、pidfile）不支持热加载，改动后需重启容器。
set -euo pipefail

# 找 chronyd 进程号：bookworm-slim 精简掉 procps，pidof 不保证存在，
# 优先用 pidof，缺失时回退扫描 /proc，保持零额外依赖
CHRONYD_PID=""
if command -v pidof >/dev/null 2>&1; then
    CHRONYD_PID="$(pidof chronyd || true)"
else
    for pdir in /proc/[0-9]*; do
        if [ "$(cat "${pdir}/comm" 2>/dev/null)" = "chronyd" ]; then
            CHRONYD_PID="${pdir#/proc/}"
            break
        fi
    done
fi

# 进程不存在说明 chronyd 未运行，reload 无从谈起，非 0 退出让平台感知失败
if [ -z "${CHRONYD_PID}" ]; then
    echo "reload: 未发现运行中的 chronyd 进程，无法热加载" >&2
    exit 1
fi

kill -HUP "${CHRONYD_PID}"
echo "reload: 已向 chronyd(PID ${CHRONYD_PID}) 发送 SIGHUP，配置热加载完成"
