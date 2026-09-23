#!/bin/bash
# 统一生效入口：平台通过 docker exec bmc-dhcp /reload.sh 触发配置生效。
# 为什么是重启而非 SIGHUP：dnsmasq 的 SIGHUP 只重读 /etc/hosts 与 DHCP 租约相关的部分配置，
# 地址池、RA 前缀、PXE 参数等都要重启进程才生效（且 --conf-file 是启动参数）。
# 实现方式：终止当前 dnsmasq，entrypoint 的监督循环检测到退出后自动带新配置拉起（容器不重启）。
set -euo pipefail

DNSMASQ_PID=""
if command -v pidof >/dev/null 2>&1; then
    DNSMASQ_PID="$(pidof dnsmasq || true)"
else
    for pdir in /proc/[0-9]*; do
        if [ "$(cat "${pdir}/comm" 2>/dev/null)" = "dnsmasq" ]; then
            DNSMASQ_PID="${pdir#/proc/}"
            break
        fi
    done
fi

if [ -z "${DNSMASQ_PID}" ]; then
    echo "reload: 未发现运行中的 dnsmasq 进程，无法生效" >&2
    exit 1
fi

kill "${DNSMASQ_PID}"

# 等监督循环把新进程拉起来：新进程就绪才算本次 reload 成功
for _ in $(seq 1 20); do
    sleep 0.5
    NEW_PID="$(pidof dnsmasq 2>/dev/null || true)"
    if [ -n "${NEW_PID}" ] && [ "${NEW_PID}" != "${DNSMASQ_PID}" ]; then
        echo "reload: dnsmasq 已带新配置重启（PID ${DNSMASQ_PID} -> ${NEW_PID}）"
        exit 0
    fi
done

echo "reload: dnsmasq 重启后 10 秒内未就绪" >&2
exit 1
