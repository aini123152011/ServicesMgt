#!/bin/bash
# 统一生效入口：平台通过 docker exec fx-nfs-ganesha /reload.sh 触发配置生效。
# 实现方式：终止运行中的 ganesha.nfsd，由 entrypoint 启动脚本检测后带新配置重新拉起。
set -euo pipefail

GANESHA_PID=""
if command -v pidof >/dev/null 2>&1; then
    GANESHA_PID="$(pidof ganesha.nfsd || true)"
else
    for pdir in /proc/[0-9]*; do
        if [ "$(cat "${pdir}/comm" 2>/dev/null)" = "ganesha.nfsd" ]; then
            GANESHA_PID="${pdir#/proc/}"
            break
        fi
    done
fi

if [ -z "${GANESHA_PID}" ]; then
    echo "reload: 未发现运行中的 ganesha.nfsd 进程" >&2
    exit 1
fi

kill -TERM "${GANESHA_PID}"
echo "reload: 已终止 ganesha.nfsd(PID ${GANESHA_PID})，监督循环将以新配置重启"
