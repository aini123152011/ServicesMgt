#!/bin/bash
# 统一生效入口：平台通过 docker exec bmc-webdav /reload.sh 触发配置生效。
# 为什么是重启而非 SIGUSR1 平滑重启：监听端口、DAV 根目录等指令无法经
# SIGUSR1 热生效（已有监听套接字被保留），且用户清单变更需要 entrypoint
# 重建 htpasswd，因此 manifest 声明 reload_mode=restart。
# 实现方式：终止当前 apache2，entrypoint 的监督循环检测到退出后自动带新配置拉起。
set -euo pipefail

# 找 apache2 进程号：bookworm-slim 精简掉 procps，pidof 不保证存在，
# 优先用 pidof，缺失时回退扫描 /proc，保持零额外依赖。
# WARNING: apache2 有一个主进程和多个工作子进程，必须全部终止，
# 否则可能只杀掉子进程、主进程仍存活，监督循环不会重启
APACHE_PID=""
if command -v pidof >/dev/null 2>&1; then
    APACHE_PID="$(pidof apache2 || true)"
else
    for pdir in /proc/[0-9]*; do
        if [ "$(cat "${pdir}/comm" 2>/dev/null)" = "apache2" ]; then
            APACHE_PID="${APACHE_PID} ${pdir#/proc/}"
        fi
    done
fi

# 进程不存在说明 apache2 未运行，reload 无从谈起，非 0 退出让平台感知失败
if [ -z "${APACHE_PID}" ]; then
    echo "reload: 未发现运行中的 apache2 进程，无法生效" >&2
    exit 1
fi

# 不加引号：pidof 命中多个进程时按空格展开逐个终止
kill ${APACHE_PID}
echo "reload: 已终止 apache2(PID${APACHE_PID})，监督循环将带新配置重启"
