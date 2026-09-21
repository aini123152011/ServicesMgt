#!/bin/bash
# 统一生效入口：平台通过 docker exec bmc-rsyslog /reload.sh 触发配置生效。
# 为什么是重启而非 SIGHUP：rsyslogd 收到 SIGHUP 只会关闭并重新打开所有输出文件
# （配合日志轮转使用，源码 tools/rsyslogd.c 的 bHadHUP 逻辑不会重读配置），
# 配置变更必须整体重启进程才能生效，因此 manifest 声明 reload_mode=restart。
# 实现方式：终止当前 rsyslogd，entrypoint 的监督循环检测到退出后自动带新配置拉起。
set -euo pipefail

# 找 rsyslogd 进程号：bookworm-slim 精简掉 procps，pidof 不保证存在，
# 优先用 pidof，缺失时回退扫描 /proc，保持零额外依赖
RSYSLOGD_PID=""
if command -v pidof >/dev/null 2>&1; then
    RSYSLOGD_PID="$(pidof rsyslogd || true)"
else
    for pdir in /proc/[0-9]*; do
        if [ "$(cat "${pdir}/comm" 2>/dev/null)" = "rsyslogd" ]; then
            RSYSLOGD_PID="${pdir#/proc/}"
            break
        fi
    done
fi

# 进程不存在说明 rsyslogd 未运行，reload 无从谈起，非 0 退出让平台感知失败
if [ -z "${RSYSLOGD_PID}" ]; then
    echo "reload: 未发现运行中的 rsyslogd 进程，无法生效" >&2
    exit 1
fi

kill "${RSYSLOGD_PID}"
echo "reload: 已终止 rsyslogd(PID ${RSYSLOGD_PID})，监督循环将带新配置重启"
