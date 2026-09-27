#!/bin/bash
# 统一生效入口：平台通过 docker exec fx-pki /reload.sh 触发配置生效。
#
# 实现方式：终止运行中的 api.py，由 entrypoint 的监督循环带新配置重启它（容器本身不重启）。
# api.py 在进程启动时读取 pki.conf，因此"杀掉 + 拉起"即完整重读配置；退出 0 表示新进程
# 已稳定就绪，平台据此判定配置已生效（配合 reload_mode: restart 的语义）。
set -euo pipefail

SUPERVISOR_PIDFILE="/run/pki/api.pid"
WAIT_ROUNDS=30   # 0.5s 一轮
SETTLE_ROUNDS=3  # 新进程出现后再观察这么久，避免"起不来"被误判成已生效

# 定位 api.py 服务进程：优先读监督循环写的 pidfile；缺失时扫描 /proc。
# 必须排除 --healthcheck / --prepare-only 这两个短命同名前缀进程，否则会杀错对象。
find_api() {
    local pid=""
    if [ -s "${SUPERVISOR_PIDFILE}" ]; then
        pid="$(cat "${SUPERVISOR_PIDFILE}")"
    fi
    if [ -n "${pid}" ] && [ -d "/proc/${pid}" ] && tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null | grep -q 'api.py'; then
        printf '%s' "${pid}"
        return 0
    fi
    local pdir cmdline
    for pdir in /proc/[0-9]*; do
        cmdline="$(tr '\0' ' ' < "${pdir}/cmdline" 2>/dev/null || true)"
        case "${cmdline}" in
            *api.py*) ;;
            *) continue ;;
        esac
        case "${cmdline}" in
            *--healthcheck*|*--prepare-only*) continue ;;
        esac
        printf '%s' "${pdir#/proc/}"
        return 0
    done
    return 0
}

OLD_PID=""
for _ in $(seq 1 ${WAIT_ROUNDS}); do
    OLD_PID="$(find_api)"
    if [ -n "${OLD_PID}" ]; then
        break
    fi
    sleep 0.5
done

if [ -z "${OLD_PID}" ]; then
    echo "reload: 等待 15 秒仍未发现运行中的 api.py，无法生效新配置" >&2
    exit 1
fi

kill -TERM "${OLD_PID}"

for _ in $(seq 1 ${WAIT_ROUNDS}); do
    sleep 0.5
    NEW_PID="$(find_api)"
    if [ -n "${NEW_PID}" ] && [ "${NEW_PID}" != "${OLD_PID}" ]; then
        settled=1
        for _ in $(seq 1 ${SETTLE_ROUNDS}); do
            sleep 0.5
            if [ ! -d "/proc/${NEW_PID}" ]; then
                settled=0
                break
            fi
        done
        if [ "${settled}" -eq 1 ]; then
            echo "reload: api.py 已带新配置重启并稳定运行（PID ${OLD_PID} -> ${NEW_PID}）"
            exit 0
        fi
        echo "reload: 新进程 ${NEW_PID} 启动后随即退出，配置可能非法，继续等待监督循环重试" >&2
    fi
done

echo "reload: api.py 重启后 15 秒内未稳定就绪，新配置未生效" >&2
exit 1
