#!/bin/bash
# 统一生效入口：平台通过 docker exec fx-freeradius /reload.sh 触发配置生效。
# 为什么是重启而非 HUP：freeradius 的 HUP 只重读部分内容，clients.conf 与 users 表都在启动期读入，
# 改密钥/用户必须重启进程（freeradius 3.2 里 -HUP 只做部分重载，实测改 clients 不生效）。
# 实现方式：终止当前 freeradius，entrypoint 的监督循环检测到退出后自动带新配置拉起（容器不重启）。
set -euo pipefail

RADIUS_PID=""
if command -v pidof >/dev/null 2>&1; then
    RADIUS_PID="$(pidof freeradius || true)"
else
    for pdir in /proc/[0-9]*; do
        if [ "$(cat "${pdir}/comm" 2>/dev/null)" = "freeradius" ]; then
            RADIUS_PID="${pdir#/proc/}"
            break
        fi
    done
fi

if [ -z "${RADIUS_PID}" ]; then
    echo "reload: 未发现运行中的 freeradius 进程，无法生效" >&2
    exit 1
fi

kill "${RADIUS_PID}"

# 等监督循环把新进程拉起来：新进程就绪才算本次 reload 成功
for _ in $(seq 1 20); do
    sleep 0.5
    NEW_PID="$(pidof freeradius 2>/dev/null || true)"
    if [ -n "${NEW_PID}" ] && [ "${NEW_PID}" != "${RADIUS_PID}" ]; then
        echo "reload: freeradius 已带新配置重启（PID ${RADIUS_PID} -> ${NEW_PID}）"
        exit 0
    fi
done

echo "reload: freeradius 重启后 10 秒内未就绪" >&2
exit 1
