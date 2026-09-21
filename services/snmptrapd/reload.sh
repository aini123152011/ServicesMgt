#!/bin/bash
# 统一生效入口：平台通过 docker exec bmc-snmptrapd /reload.sh 触发配置生效。
# 为什么是重启而非 SIGHUP：snmptrapd 收到 SIGHUP 会重读 snmptrapd.conf（源码
# apps/snmptrapd.c 的 hup_handler -> trapd_update_config），但仅覆盖配置文件内指令
# （如 authCommunity）；监听地址、输出文件、输出格式是启动命令行参数，SIGHUP 无法应用，
# 因此 schema 任一字段变更统一走重启路径，manifest 声明 reload_mode=restart。
# 实现方式：终止当前 snmptrapd，entrypoint 的监督循环检测到退出后自动带新配置拉起。
set -euo pipefail

# 找 snmptrapd 进程号：bookworm-slim 精简掉 procps，pidof 不保证存在，
# 优先用 pidof，缺失时回退扫描 /proc，保持零额外依赖
SNMPTRAPD_PID=""
if command -v pidof >/dev/null 2>&1; then
    SNMPTRAPD_PID="$(pidof snmptrapd || true)"
else
    for pdir in /proc/[0-9]*; do
        if [ "$(cat "${pdir}/comm" 2>/dev/null)" = "snmptrapd" ]; then
            SNMPTRAPD_PID="${pdir#/proc/}"
            break
        fi
    done
fi

# 进程不存在说明 snmptrapd 未运行，reload 无从谈起，非 0 退出让平台感知失败
if [ -z "${SNMPTRAPD_PID}" ]; then
    echo "reload: 未发现运行中的 snmptrapd 进程，无法生效" >&2
    exit 1
fi

kill "${SNMPTRAPD_PID}"
echo "reload: 已终止 snmptrapd(PID ${SNMPTRAPD_PID})，监督循环将带新配置重启"
