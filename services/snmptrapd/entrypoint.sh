#!/bin/sh
# 启动前置：处理"配置卷首次挂载为空"的情况，并以监督循环方式托管 snmptrapd。
# snmptrapd 缺少配置文件只会拒绝社区认证而无法按预期工作，因此启动前若配置缺失，
# 就从镜像内置默认配置播种一份，保证 cd services/snmptrapd && docker compose up -d 即可独立运行。
set -eu

CONF_DIR="/etc/snmp"
CONF_FILE="${CONF_DIR}/snmptrapd.conf"
SEED_FILE="/usr/share/bmc-snmptrapd/snmptrapd.conf.default"

if [ ! -f "${CONF_FILE}" ]; then
    echo "entrypoint: 配置卷缺少 snmptrapd.conf，播种内置默认配置"
    mkdir -p "${CONF_DIR}"
    cp "${SEED_FILE}" "${CONF_FILE}"
fi

# 空卷播种输出文件：-Lf 追加写但要求父目录存在，预先建好目录与文件避免首条 Trap 丢失
# （输出文件本身在循环内按渲染产物确定，这里先按缺省路径准备好目录）
mkdir -p /var/log/snmp
touch /var/log/snmp/traps.log

# 监督循环：reload.sh 终止 snmptrapd 后，循环检测到退出并带新配置重新拉起；
# docker stop 的 SIGTERM 由 stop_handler 转发给 snmptrapd 后退出容器
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
    # 每轮都从渲染产物重读运行时参数：输出文件/监听地址/输出格式只能通过命令行指定，
    # 模板把它们回写为 "# runtime:" 注释行，这里按约定解析（缺省回落 schema 默认值）。
    # **必须在循环内读**：reload.sh 只杀守护进程、不重启容器，若在循环外读一次，
    # 后续重启会一直用容器启动时的旧值——改监听地址/输出文件都会静默不生效（实测踩到：
    # blackhole_drop 的 listen=127.0.0.1 不生效，外部 Trap 照样被收到）。
    RUNTIME_OUTPUT_FILE="$(sed -n 's/^# runtime: output_file=//p' "${CONF_FILE}" | tail -n 1)"
    RUNTIME_LISTEN="$(sed -n 's/^# runtime: listen=//p' "${CONF_FILE}" | tail -n 1)"
    RUNTIME_OUTPUT_OPTS="$(sed -n 's/^# runtime: output_opts=//p' "${CONF_FILE}" | tail -n 1)"
    OUTPUT_FILE="${RUNTIME_OUTPUT_FILE:-/var/log/snmp/traps.log}"
    LISTEN="${RUNTIME_LISTEN:-0.0.0.0:162}"
    mkdir -p "$(dirname "${OUTPUT_FILE}")"
    touch "${OUTPUT_FILE}"
    # -Lf：日志追加写入文件；-f：前台不 fork；-n：不对 Trap 来源地址做反解
    # （容器内无 DNS 解析价值，纯开销，日志直接记 IP）；监听参数形如 <地址>:162。
    # RUNTIME_OUTPUT_OPTS 不加引号是故意的：空值展开为零个参数，非空值按空格拆分
    snmptrapd -Lf "${OUTPUT_FILE}" -f -n ${RUNTIME_OUTPUT_OPTS} "${LISTEN}" &
    CHILD_PID=$!
    wait "${CHILD_PID}" || true
    CHILD_PID=""
    echo "entrypoint: snmptrapd 已退出，2 秒后带新配置重启"
    sleep 2
done
