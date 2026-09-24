#!/bin/bash
# 统一生效入口：平台通过 docker exec fx-chrony /reload.sh 触发配置生效。
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
# 的限制；/reload.sh 退出 0 表示新进程已稳定就绪，平台据此判定配置已生效。
set -euo pipefail

SUPERVISOR_PIDFILE="/run/chrony/supervisor.pid"
# 等待预算（0.5s 一轮）：进程出现 + 稳定观察各占一段，总预算约 15s
WAIT_ROUNDS=30
# 新进程出现后再观察这么久才判定「稳住了」：配置非法时 chronyd 会在毫秒级退出，
# 只看一眼 /proc 会把「起不来」误判成已生效
SETTLE_ROUNDS=3

# 定位 chronyd 进程号：优先读 entrypoint 监督循环写下的 pidfile；缺失（旧容器、文件被清）
# 时回退 pidof / 扫描 /proc，保持零额外依赖（bookworm-slim 精简掉 procps，pidof 不保证存在）
find_chronyd() {
    local pid=""
    if [ -s "${SUPERVISOR_PIDFILE}" ]; then
        pid="$(cat "${SUPERVISOR_PIDFILE}")"
    fi
    if [ -n "${pid}" ] && [ -d "/proc/${pid}" ]; then
        printf '%s' "${pid}"
        return 0
    fi
    if command -v pidof >/dev/null 2>&1; then
        pidof chronyd || true
        return 0
    fi
    local pdir
    for pdir in /proc/[0-9]*; do
        if [ "$(cat "${pdir}/comm" 2>/dev/null)" = "chronyd" ]; then
            printf '%s' "${pdir#/proc/}"
            return 0
        fi
    done
}

# 上一次 reload 结束进程后，监督循环要 1 秒才拉起新的：这里等一会儿再判失败，
# 否则「紧接着的第二次下发」会被误报成 502（实测踩到过：每隔一次下发报错）
OLD_PID=""
for _ in $(seq 1 ${WAIT_ROUNDS}); do
    OLD_PID="$(find_chronyd)"
    if [ -n "${OLD_PID}" ]; then
        break
    fi
    sleep 0.5
done

if [ -z "${OLD_PID}" ]; then
    echo "reload: 等待 15 秒仍未发现运行中的 chronyd 进程，无法生效新配置" >&2
    exit 1
fi

kill -TERM "${OLD_PID}"

# 等监督循环把新进程拉起来，并观察一小段确认它没有立刻退出：
# 新进程稳定存在 = 新配置已被完整读取且服务可用
for _ in $(seq 1 ${WAIT_ROUNDS}); do
    sleep 0.5
    NEW_PID="$(find_chronyd)"
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
            echo "reload: chronyd 已带新配置重启并稳定运行（PID ${OLD_PID} -> ${NEW_PID}）"
            exit 0
        fi
        echo "reload: 新进程 ${NEW_PID} 启动后随即退出，配置可能非法，继续等待监督循环重试" >&2
    fi
done

echo "reload: chronyd 重启后 15 秒内未稳定就绪，新配置未生效" >&2
exit 1
