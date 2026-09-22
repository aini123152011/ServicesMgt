#!/bin/bash
# 统一生效入口：平台通过 docker exec bmc-chrony /reload.sh 触发配置生效。
#
# 为什么是「重启 chronyd 进程」而不是 SIGHUP 热加载（实测结论，chrony 4.3 / bookworm）：
# chronyd 启动后按 PRIVDROP 降权到 _chrony，而重载配置时要重建自己的 pidfile 与命令套接字，
# 这两件事对 /run/chrony 的属主要求互相矛盾：
#   - 目录属 root：重载删不掉自己的 pidfile，报 "Could not remove
#     /run/chrony/chronyd.pid : Operation not permitted"，随后直接退出；
#   - 目录属 _chrony：chronyd 报 "Wrong permissions on /run/chrony" 并禁用命令套接字
#     （chronyc 健康检查随之失效），重载依旧以静默退出告终。
# 两种情况下 SIGHUP 都会杀死 chronyd——即"热加载"从未真正生效，每次下发都在重启进程。
# 因此这里统一走进程重启：结束当前 chronyd，由 entrypoint 的监督循环带新配置把它拉起来
# （容器本身不重启，NTP 中断约 1 秒）。这样配置一定被完整重读，也不受"哪些指令支持热加载"
# 的限制；/reload.sh 退出 0 表示新进程已就绪，平台据此判定配置已生效。
set -euo pipefail

SUPERVISOR_PIDFILE="/run/chrony/supervisor.pid"

# 定位 chronyd 进程号：优先读 entrypoint 监督循环写下的 pidfile；缺失（旧容器、文件被清）
# 时回退 pidof / 扫描 /proc，保持零额外依赖（bookworm-slim 精简掉 procps，pidof 不保证存在）
CHRONYD_PID=""
if [ -s "${SUPERVISOR_PIDFILE}" ]; then
    CHRONYD_PID="$(cat "${SUPERVISOR_PIDFILE}")"
fi
if [ -z "${CHRONYD_PID}" ] || [ ! -d "/proc/${CHRONYD_PID}" ]; then
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
fi

# 进程不存在说明 chronyd 未运行，reload 无从谈起，非 0 退出让平台感知失败
if [ -z "${CHRONYD_PID}" ]; then
    echo "reload: 未发现运行中的 chronyd 进程，无法生效新配置" >&2
    exit 1
fi

OLD_PID="${CHRONYD_PID}"
kill -TERM "${OLD_PID}"

# 等监督循环把新进程拉起来：新进程已就绪即代表新配置已完整加载（本次 reload 的成功判据）
for _ in $(seq 1 20); do
    sleep 0.5
    NEW_PID="$(cat "${SUPERVISOR_PIDFILE}" 2>/dev/null || true)"
    if [ -n "${NEW_PID}" ] && [ "${NEW_PID}" != "${OLD_PID}" ] && [ -d "/proc/${NEW_PID}" ]; then
        echo "reload: chronyd 已带新配置重启（PID ${OLD_PID} -> ${NEW_PID}）"
        exit 0
    fi
done

echo "reload: chronyd 重启后 10 秒内未就绪，新配置未生效" >&2
exit 1
