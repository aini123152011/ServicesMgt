#!/bin/bash
# 统一生效入口：平台通过 docker exec bmc-vsftpd /reload.sh 触发配置生效。
# 为什么是重启而非 SIGHUP：vsftpd 没有配置热加载能力（收到信号不会重读
# vsftpd.conf），且用户清单（local_users.txt）变更需要 entrypoint 重新
# useradd/chpasswd 同步账号，因此 manifest 声明 reload_mode=restart。
# 实现方式：终止当前 vsftpd，entrypoint 的监督循环检测到退出后自动带新配置拉起。
set -euo pipefail

# 找 vsftpd 进程号：bookworm-slim 精简掉 procps，pidof 不保证存在，
# 优先用 pidof，缺失时回退扫描 /proc，保持零额外依赖。
# WARNING: vsftpd 每个会话各有一个同名子进程，必须全部终止，
# 否则可能只杀掉会话进程、监听进程仍存活，监督循环不会重启
VSFTPD_PID=""
if command -v pidof >/dev/null 2>&1; then
    VSFTPD_PID="$(pidof vsftpd || true)"
else
    for pdir in /proc/[0-9]*; do
        if [ "$(cat "${pdir}/comm" 2>/dev/null)" = "vsftpd" ]; then
            VSFTPD_PID="${VSFTPD_PID} ${pdir#/proc/}"
        fi
    done
fi

# 进程不存在说明 vsftpd 未运行，reload 无从谈起，非 0 退出让平台感知失败
if [ -z "${VSFTPD_PID}" ]; then
    echo "reload: 未发现运行中的 vsftpd 进程，无法生效" >&2
    exit 1
fi

# 不加引号：pidof 命中多个进程时按空格展开逐个终止
kill ${VSFTPD_PID}
echo "reload: 已终止 vsftpd(PID${VSFTPD_PID})，监督循环将带新配置重启"
