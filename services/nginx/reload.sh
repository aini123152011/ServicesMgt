#!/bin/bash
# 统一生效入口：平台通过 docker exec fx-nginx /reload.sh 触发配置生效。
# 热加载依据：nginx 收到 SIGHUP 会重读主配置文件并平滑加载新 worker 进程，
# 当前正在传输的文件不中断，实现零停机热重载。
set -euo pipefail

CONF_FILE="/etc/nginx-bmc/nginx.conf"

if [ ! -f "${CONF_FILE}" ]; then
    echo "reload: 配置文件 ${CONF_FILE} 不存在" >&2
    exit 1
fi

# 在热加载前执行配置语法测试，防止错误配置打崩运行中的主进程
if ! nginx -t -c "${CONF_FILE}" >/dev/null 2>&1; then
    echo "reload: nginx 配置语法校验失败：" >&2
    nginx -t -c "${CONF_FILE}" >&2 || true
    exit 1
fi

# 找 nginx 主进程 PID
NGINX_PID=""
if [ -f /run/nginx.pid ]; then
    NGINX_PID="$(cat /run/nginx.pid 2>/dev/null || true)"
fi

if [ -z "${NGINX_PID}" ] && command -v pidof >/dev/null 2>&1; then
    NGINX_PID="$(pidof nginx | awk '{print $NF}' || true)"
fi

if [ -z "${NGINX_PID}" ]; then
    echo "reload: 未发现运行中的 nginx 主进程，无法执行热重载" >&2
    exit 1
fi

kill -HUP "${NGINX_PID}"
echo "reload: 已向 nginx(PID ${NGINX_PID}) 发送 SIGHUP，配置热重载完成"
