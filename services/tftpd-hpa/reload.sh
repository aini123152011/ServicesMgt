#!/bin/bash
# 统一生效入口：平台通过 docker exec fx-tftpd-hpa /reload.sh 触发配置生效。
# 为什么是重启而非 SIGHUP：in.tftpd 没有配置热加载能力（无重读配置的信号处理），
# 监听端口、服务目录等参数只在启动时解析，因此 manifest 声明 reload_mode=restart。
# 实现方式：终止当前 in.tftpd，entrypoint 的监督循环检测到退出后自动带新配置拉起。
set -euo pipefail

# 找 in.tftpd 进程号：bookworm-slim 精简掉 procps，pidof 不保证存在，
# 优先用 pidof，缺失时回退扫描 /proc，保持零额外依赖
IN_TFTPD_PID=""
if command -v pidof >/dev/null 2>&1; then
    IN_TFTPD_PID="$(pidof in.tftpd || true)"
else
    for pdir in /proc/[0-9]*; do
        if [ "$(cat "${pdir}/comm" 2>/dev/null)" = "in.tftpd" ]; then
            IN_TFTPD_PID="${IN_TFTPD_PID} ${pdir#/proc/}"
        fi
    done
fi

# 进程不存在说明 in.tftpd 未运行，reload 无从谈起，非 0 退出让平台感知失败
if [ -z "${IN_TFTPD_PID}" ]; then
    echo "reload: 未发现运行中的 in.tftpd 进程，无法生效" >&2
    exit 1
fi

# 不加引号：pidof 命中多个进程时按空格展开逐个终止
kill ${IN_TFTPD_PID}
echo "reload: 已终止 in.tftpd(PID${IN_TFTPD_PID})，监督循环将带新配置重启"
