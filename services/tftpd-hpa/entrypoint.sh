#!/bin/sh
# 启动前置：处理"配置卷首次挂载为空"的情况，准备服务目录，
# 并以监督循环方式托管 in.tftpd。
# 配置缺失时从镜像内置默认播种，保证 cd services/tftpd-hpa && docker compose up -d 即可独立运行。
set -eu

CONF_DIR="/etc/tftpd-hpa"
CONF_FILE="${CONF_DIR}/tftpd-hpa.conf"
SEED_FILE="/usr/share/fx-tftpd-hpa/tftpd-hpa.conf.default"

if [ ! -f "${CONF_FILE}" ]; then
    echo "entrypoint: 配置卷缺少 tftpd-hpa.conf，播种内置默认配置"
    mkdir -p "${CONF_DIR}"
    cp "${SEED_FILE}" "${CONF_FILE}"
fi

# 监督循环：reload.sh 终止 in.tftpd 后，循环检测到退出并带新配置重新拉起；
# docker stop 的 SIGTERM 由 stop_handler 转发给 in.tftpd 后退出容器
CHILD_PID=""
stop_handler() {
    if [ -n "${CHILD_PID}" ]; then
        kill "${CHILD_PID}" 2>/dev/null || true
        wait "${CHILD_PID}" 2>/dev/null || true
    fi
    exit 0
}
trap stop_handler TERM INT

while :; do
    # 配置为 shell 变量形式（沿用 Debian /etc/default/tftpd-hpa 的格式习惯），
    # source 后得到 TFTP_USERNAME/DIRECTORY/ADDRESS/OPTIONS；
    # 每轮循环重新加载，平台改配置经 reload.sh 重启后即时生效
    . "${CONF_FILE}"
    # 服务目录兜底创建并交给 tftp 用户：开启 --create 后客户端可上传新文件，
    # 目录必须对该用户可写，否则写请求全部失败
    mkdir -p "${TFTP_DIRECTORY}"
    chown "${TFTP_USERNAME}:${TFTP_USERNAME}" "${TFTP_DIRECTORY}"
    # --listen：standalone 监听模式，in.tftpd 自己绑定 TFTP_ADDRESS:69。
    # NOTE: --foreground 只表示"不转入后台"，并不隐含监听模式；缺 --listen 时
    # in.tftpd 走 inetd 模式从 stdin 读请求，容器里没有 inetd 会立刻退出，
    # 表现为进程反复重启但 UDP 69 始终无应答。
    # --foreground：前台运行，日志走 stderr 由 docker logs 捕获；
    # TFTP_OPTIONS 有意不加引号，让 --secure --create 等标志逐词传给 in.tftpd
    in.tftpd --listen --foreground --user "${TFTP_USERNAME}" --address "${TFTP_ADDRESS}" ${TFTP_OPTIONS} "${TFTP_DIRECTORY}" &
    CHILD_PID=$!
    wait "${CHILD_PID}" || true
    CHILD_PID=""
    echo "entrypoint: in.tftpd 已退出，2 秒后带新配置重启"
    sleep 2
done
