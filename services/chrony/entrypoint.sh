#!/bin/sh
# 启动前置：处理"配置卷首次挂载为空"的情况。
# chronyd 缺少 /etc/chrony/chrony.conf 会直接启动失败，因此启动前若配置缺失，
# 就从镜像内置默认配置播种一份，保证 cd services/chrony && docker compose up -d 即可独立运行。
#
# 进程模型：本脚本是 1 号进程，在监督循环里反复拉起 chronyd（见文件末尾说明），
# /reload.sh 通过 /run/chrony/supervisor.pid 找到当前 chronyd 发信号。
set -eu

CONF_DIR="/etc/chrony"
CONF_FILE="${CONF_DIR}/chrony.conf"
FAKETIME_CONF="${CONF_DIR}/faketime.conf"
SUPERVISOR_PIDFILE="/run/chrony/supervisor.pid"
CHRONYD_PIDFILE="/run/chrony/chronyd.pid"
SEED_FILE="/usr/share/bmc-chrony/chrony.conf.default"

if [ ! -f "${CONF_FILE}" ]; then
    echo "entrypoint: 配置卷缺少 chrony.conf，播种内置默认配置"
    mkdir -p "${CONF_DIR}"
    cp "${SEED_FILE}" "${CONF_FILE}"
fi

# 运行目录：chronyd 在降权（PRIVDROP）前以 root 在此写 pidfile 与命令套接字，
# 目录不存在会直接启动失败；保持 root 属主（与发行版默认一致），不要改成 _chrony——
# 实测那样 chronyd 会报 "Wrong permissions on /run/chrony" 并禁用命令套接字。
mkdir -p /run/chrony

# 容器停止：把信号转给 chronyd 再退出，避免留下孤儿进程（1 号进程收到的是 docker stop 的 SIGTERM）
CHRONYD_PID=""
stop_handler() {
    if [ -n "${CHRONYD_PID}" ]; then
        kill -TERM "${CHRONYD_PID}" 2>/dev/null || true
        wait "${CHRONYD_PID}" 2>/dev/null || true
    fi
    exit 0
}
trap stop_handler TERM INT

while :; do
    # 每轮都重读渲染产物：/reload.sh 结束 chronyd 后由本循环带新配置重启它，因此配置
    # 每次都会完整重读。选择「循环内重启」而不是「让容器退出交给 restart 策略」有两个
    # 实机踩到的理由：
    #   1) Docker 对重启策略有指数退避（RestartCount 越大等待越久，上限 1 分钟），连续切换
    #      故障模式会让每次生效越来越慢（实测重启 26 次后单次等待约 30 秒）；
    #   2) 容器重启期间 docker exec 被 409 拒绝，平台会把"正在生效"误报成下发失败。
    FAKETIME_OFFSET=""
    if [ -f "${FAKETIME_CONF}" ]; then
        FAKETIME_OFFSET="$(sed -n 's/^FAKETIME_OFFSET=//p' "${FAKETIME_CONF}" | head -n 1)"
    fi
    # 上次异常退出可能残留 chronyd 自己的 pidfile，导致重启时误报"已有 chronyd 运行"
    rm -f "${CHRONYD_PIDFILE}"

    if [ -n "${FAKETIME_OFFSET}" ]; then
        echo "entrypoint: 激活 BMC 故障注入，注入时间偏移: ${FAKETIME_OFFSET}"
        # 直接用 libfaketime 预加载，不经 faketime 包装器：包装器会额外留一层进程，
        # 信号打不到 chronyd 上（包装器不转发信号），偏移变更无法生效。
        # $LIB 由动态链接器展开为架构目录（aarch64-linux-gnu / x86_64-linux-gnu），多架构通用。
        # 变量只作用于这一条命令、不 export：否则 sed/printf/sleep 也会被假时间影响。
        LD_PRELOAD='/usr/$LIB/faketime/libfaketime.so.1' FAKETIME="${FAKETIME_OFFSET}" \
            chronyd -n -x -f "${CONF_FILE}" &
    else
        # -x：不调整本机时钟（容器默认无 CAP_SYS_TIME，宿主机时钟也不该被容器修改），
        # 只对外提供 NTP 服务；需要让容器校准宿主机时钟时，在 compose 里加
        # cap_add: [SYS_TIME] 并去掉 -x。
        chronyd -n -x -f "${CONF_FILE}" &
    fi
    CHRONYD_PID=$!
    # 用独立文件名记录监督循环的 pid，避免与 chronyd 自己的 pidfile 互相覆盖
    printf '%s' "${CHRONYD_PID}" > "${SUPERVISOR_PIDFILE}"
    wait "${CHRONYD_PID}" || true
    CHRONYD_PID=""
    echo "entrypoint: chronyd 已退出，1 秒后带新配置重启（偏移 ${FAKETIME_OFFSET:-无}）"
    sleep 1
done
