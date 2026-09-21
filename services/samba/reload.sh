#!/bin/bash
# 统一生效入口：平台通过 docker exec bmc-samba /reload.sh 触发配置生效。
# 实现方式：终止运行中的 smbd，由 entrypoint 监督循环重新同步账号并以新配置拉起。
set -euo pipefail

SMBD_PID=""
if command -v pidof >/dev/null 2>&1; then
    SMBD_PID="$(pidof smbd || true)"
else
    for pdir in /proc/[0-9]*; do
        if [ "$(cat "${pdir}/comm" 2>/dev/null)" = "smbd" ]; then
            SMBD_PID="${SMBD_PID} ${pdir#/proc/}"
        fi
    done
fi

if [ -z "${SMBD_PID}" ]; then
    echo "reload: 未发现运行中的 smbd 进程" >&2
    exit 1
fi

kill -TERM ${SMBD_PID}
echo "reload: 已终止 smbd(PID ${SMBD_PID})，监督循环将以新配置重启"
