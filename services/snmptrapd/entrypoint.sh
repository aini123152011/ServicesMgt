#!/bin/sh
# 启动前置：处理"配置卷首次挂载为空"的情况，并以监督循环方式托管 snmptrapd。
# snmptrapd 缺少配置文件只会拒绝社区认证而无法按预期工作，因此启动前若配置缺失，
# 就从镜像内置默认配置播种一份，保证 cd services/snmptrapd && docker compose up -d 即可独立运行。
set -eu

CONF_DIR="/etc/snmp"
CONF_FILE="${CONF_DIR}/snmptrapd.conf"
SEED_FILE="/usr/share/bmc-snmptrapd/snmptrapd.conf.default"
SPLITTER="/usr/local/bin/splitter.py"

if [ ! -f "${CONF_FILE}" ]; then
    echo "entrypoint: 配置卷缺少 snmptrapd.conf，播种内置默认配置"
    mkdir -p "${CONF_DIR}"
    cp "${SEED_FILE}" "${CONF_FILE}"
fi

# 记录格式（-F）：<来源传输地址> TAB <本地时间戳> TAB <变量绑定>。
# 为什么是这个形状：snmptrapd 自身不能按来源命名文件，落盘交给下面托管的 splitter.py，
# 而它按「行首字段是传输地址」解析来源，两者是一对契约，必须一起改。
#   %b  = 传输地址（v4 "UDP: [源IP]:端口->[本机IP]:162"，v6 "UDP/IPv6: [源IP]:端口"）
#   %A  = agent-addr（实测在本版本只对 v1 TRAP 有效，v2c 恒为 0.0.0.0，不能用）
#   %y/%m/%l %h:%j:%k = 本地 年-月-日 时:分:秒；%v = 变量绑定（受 -O 选项影响）
# 用 printf 拼是为了让 TAB 是真实制表符（% 需写成 %%）；分节符与 -O 的 varbind 分隔符一致。
TRAP_FORMAT="$(printf '%%b\t%%y-%%m-%%l %%h:%%j:%%k\t%%v')"

# 监督循环：reload.sh 终止 snmptrapd 后，循环检测到退出并带新配置重新拉起；
# docker stop 的 SIGTERM 由 stop_handler 转发给 splitter（splitter 再转发给 snmptrapd）后退出容器
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
    # 每轮都从渲染产物重读运行时参数：监听地址/日志目标/日志布局/输出选项只能通过命令行指定，
    # 模板把它们回写为 "# runtime:" 注释行，这里按约定解析（缺省回落 schema 默认值）。
    # **必须在循环内读**：reload.sh 只杀守护进程、不重启容器，若在循环外读一次，
    # 后续重启会一直用容器启动时的旧值——改监听地址/日志布局都会静默不生效（实测踩到：
    # blackhole_drop 的 listen=127.0.0.1 不生效，外部 Trap 照样被收到）。
    RUNTIME_LOG_TARGET="$(sed -n 's/^# runtime: log_target=//p' "${CONF_FILE}" | tail -n 1)"
    RUNTIME_LOG_ROOT="$(sed -n 's/^# runtime: log_root=//p' "${CONF_FILE}" | tail -n 1)"
    RUNTIME_DATE_DIR="$(sed -n 's/^# runtime: date_dir=//p' "${CONF_FILE}" | tail -n 1)"
    RUNTIME_FILENAME_BY_IP="$(sed -n 's/^# runtime: filename_by_ip=//p' "${CONF_FILE}" | tail -n 1)"
    RUNTIME_LISTEN="$(sed -n 's/^# runtime: listen=//p' "${CONF_FILE}" | tail -n 1)"
    RUNTIME_OUTPUT_OPTS="$(sed -n 's/^# runtime: output_opts=//p' "${CONF_FILE}" | tail -n 1)"
    LOG_TARGET="${RUNTIME_LOG_TARGET:-/dev/stdout}"
    LOG_ROOT="${RUNTIME_LOG_ROOT:-/var/log/snmp}"
    DATE_DIR="${RUNTIME_DATE_DIR:-daily}"
    FILENAME_BY_IP="${RUNTIME_FILENAME_BY_IP:-true}"
    LISTEN="${RUNTIME_LISTEN:-0.0.0.0:162}"
    mkdir -p "${LOG_ROOT}"
    # -Lf /dev/stdout：日志（含 Trap 记录）写 stdout，由监督循环里的 splitter 分流到
    #   <LOG_ROOT>/<日期段>/<来源>.log 并原样透传，docker logs 照旧可见。
    #   注意不能用 "-Lf -"：实测（net-snmp 5.9.3）那是「写到名为 - 的文件」，不是 stdout。
    # -f：前台不 fork；-n：不对来源做反解（容器内无解析价值，日志直接记 IP）；
    #   -n 不影响 %b 的数值化输出（%b 本来就是数值传输地址）。
    # RUNTIME_OUTPUT_OPTS 不加引号是故意的：空值展开为零个参数，非空值按空格拆分。
    # IPv6：v4 监听是通配（0.0.0.0）时额外监听 udp6:162，让 v6 客户端也能发 Trap。
    # 指定了具体 v4 地址（如黑洞模式只监听 127.0.0.1）时不加 v6 —— 那正是"只收回环"的故障语义。
    case "${LISTEN}" in
        0.0.0.0:*|*:0.0.0.0:*) LISTEN_V6="udp6:162" ;;
        *) LISTEN_V6="" ;;
    esac
    # LISTEN_V6 不加引号是故意的：空值展开为零个参数
    # splitter 托管 snmptrapd：splitter 退出（snmptrapd 死掉导致管道 EOF，或收到信号）后循环重来
    python3 "${SPLITTER}" \
        --service snmptrapd \
        --log-root "${LOG_ROOT}" \
        --date-dir "${DATE_DIR}" \
        --filename-by-ip "${FILENAME_BY_IP}" \
        -- snmptrapd -Lf "${LOG_TARGET}" -f -n ${RUNTIME_OUTPUT_OPTS} -F "${TRAP_FORMAT}" "${LISTEN}" ${LISTEN_V6} &
    CHILD_PID=$!
    wait "${CHILD_PID}" || true
    CHILD_PID=""
    echo "entrypoint: snmptrapd 已退出，2 秒后带新配置重启"
    sleep 2
done
