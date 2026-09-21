#!/bin/bash
# 统一生效入口：平台通过 docker exec bmc-chrony /reload.sh 触发配置生效。
# 热加载依据：chronyd 收到 SIGHUP 会重读 /etc/chrony/chrony.conf 并重新应用配置，
# NTP 服务不中断（chronyd 手册 SIGNALS 说明）。
# NOTE: 少数指令（如 bindcmdaddress、user、pidfile）不支持热加载，改动后需重启容器。
# NOTE: 故障注入的时间偏移由 libfaketime 在进程启动时经 LD_PRELOAD 注入，无法热加载，
# 偏移变化时只能重启 chronyd 进程（见下方 offset 分支）。
set -euo pipefail

FAKETIME_CONF="/etc/chrony/faketime.conf"
FAKETIME_ACTIVE="/run/chrony/faketime.active"

# 平台渲染的期望偏移（文件缺失或空值都表示不注入）
DESIRED_OFFSET=""
if [ -f "${FAKETIME_CONF}" ]; then
    DESIRED_OFFSET="$(sed -n 's/^FAKETIME_OFFSET=//p' "${FAKETIME_CONF}" | head -n 1)"
fi
# entrypoint 启动时记录的、当前进程真正生效的偏移
ACTIVE_OFFSET="$(cat "${FAKETIME_ACTIVE}" 2>/dev/null || true)"

if [ "${DESIRED_OFFSET}" != "${ACTIVE_OFFSET}" ]; then
    # 偏移变化：libfaketime 在进程启动时经 LD_PRELOAD 注入，无法热加载，只能让 chronyd
    # 带新偏移重新启动。chronyd 就是容器 1 号进程（entrypoint 直接 exec），向它发 SIGTERM
    # 会让容器退出，再由 compose 的 restart: unless-stopped 拉起并应用新偏移。
    echo "reload: 故障注入偏移 '${ACTIVE_OFFSET}' -> '${DESIRED_OFFSET}'，重启 chronyd 进程以生效"
    kill -TERM 1
    exit 0
fi

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
