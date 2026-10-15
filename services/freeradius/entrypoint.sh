#!/bin/sh
# 启动前置：处理「配置卷首次挂载为空」的情况，并以监督循环方式托管 freeradius。
# freeradius 缺 clients.conf/authorize 会启动失败或拒绝一切请求，因此启动前若配置缺失，
# 就从镜像内置默认配置播种一份，保证 cd services/freeradius && docker compose up -d 即可独立运行。
set -eu

CONF_DIR="/etc/freeradius/3.0/bmc"
SEED_DIR="/usr/share/bmc-freeradius"
RADIUSD_CONF="/etc/freeradius/3.0/radiusd.conf"
SPLITTER="/usr/local/bin/splitter.py"

mkdir -p "${CONF_DIR}" /var/log/freeradius
if [ ! -f "${CONF_DIR}/clients.conf" ]; then
    echo "entrypoint: 配置卷缺少 clients.conf，播种内置默认配置"
    cp "${SEED_DIR}/clients.conf.default" "${CONF_DIR}/clients.conf"
fi
if [ ! -f "${CONF_DIR}/authorize" ]; then
    echo "entrypoint: 配置卷缺少 authorize，播种内置默认配置"
    cp "${SEED_DIR}/authorize.default" "${CONF_DIR}/authorize"
fi

# 打开认证结果日志（默认 auth = no，等于一条认证记录都不写）。
# 为什么在这里 sed 而不是放进渲染目录：manifest.config_dir 只挂了 bmc/ 子目录，
# 镜像自带的 radiusd.conf / sites-enabled 保持原样；而 log{} 段不能靠 $INCLUDE 二次定义
# （radiusd 会报重复段、直接起不来），所以这个开关只能在启动前就地补。
# 幂等：形如 "^<空白>auth = no$" 才替换；已改过的（auth = yes）再跑一次无副作用。
# 只开 auth（= auth_accept + auth_reject）：**不**开 auth_badpass/auth_goodpass，
# 那两个会把口令写进日志（实测 auth_goodpass=yes 时明文口令进日志，见任务取证）。
# 顺带一提：不能改用 radiusd -X 取来源 IP——实测 -X 会打印明文 User-Password。
if grep -qE '^[[:space:]]*auth = no$' "${RADIUSD_CONF}"; then
    echo "entrypoint: 打开 radiusd.conf 的 auth 认证日志（不开 badpass/goodpass，避免口令入日志）"
    sed -i 's/^\([[:space:]]*\)auth = no$/\1auth = yes/' "${RADIUSD_CONF}"
fi
grep -qE '^[[:space:]]*auth = yes$' "${RADIUSD_CONF}" \
    || echo "entrypoint: 警告——radiusd.conf 里没看到 auth = yes，认证日志可能不会写"

# 监督循环：reload.sh 终止 freeradius 后，循环检测到退出并带新配置重新拉起；
# docker stop 的 SIGTERM 由 stop_handler 转发给 splitter（splitter 再转发给 radiusd）后退出容器
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
    # 每轮都从渲染产物重读运行时参数（clients.conf 顶部的 "# runtime:" 注释行）：
    # reload.sh 只杀守护进程、不重启容器，在循环外读一次会让改动静默不生效。
    RUNTIME_LOG_ROOT="$(sed -n 's/^# runtime: log_root=//p' "${CONF_DIR}/clients.conf" | tail -n 1)"
    RUNTIME_DATE_DIR="$(sed -n 's/^# runtime: date_dir=//p' "${CONF_DIR}/clients.conf" | tail -n 1)"
    RUNTIME_FILENAME_BY_IP="$(sed -n 's/^# runtime: filename_by_ip=//p' "${CONF_DIR}/clients.conf" | tail -n 1)"
    LOG_ROOT="${RUNTIME_LOG_ROOT:-/var/log/freeradius}"
    DATE_DIR="${RUNTIME_DATE_DIR:-daily}"
    FILENAME_BY_IP="${RUNTIME_FILENAME_BY_IP:-true}"
    mkdir -p "${LOG_ROOT}"
    # -f：前台运行（不 fork，监督循环才能等到它）；-l stdout：日志走 stdout
    #   交给 splitter 分流到 <LOG_ROOT>/<日期段>/<来源>.log，并原样透传进 docker logs。
    # 不写 -X：那是全量调试模式，实测会把明文 User-Password 打进日志，且逐条认证几十行噪声。
    python3 "${SPLITTER}" \
        --service freeradius \
        --log-root "${LOG_ROOT}" \
        --date-dir "${DATE_DIR}" \
        --filename-by-ip "${FILENAME_BY_IP}" \
        -- freeradius -f -l stdout &
    CHILD_PID=$!
    wait "${CHILD_PID}" || true
    CHILD_PID=""
    echo "entrypoint: freeradius 已退出，2 秒后带新配置重启"
    sleep 2
done
