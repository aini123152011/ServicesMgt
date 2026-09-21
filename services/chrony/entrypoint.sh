#!/bin/sh
# 启动前置：处理"配置卷首次挂载为空"的情况。
# chronyd 缺少 /etc/chrony/chrony.conf 会直接启动失败，因此启动前若配置缺失，
# 就从镜像内置默认配置播种一份，保证 cd services/chrony && docker compose up -d 即可独立运行。
set -eu

CONF_DIR="/etc/chrony"
CONF_FILE="${CONF_DIR}/chrony.conf"
FAKETIME_CONF="${CONF_DIR}/faketime.conf"
FAKETIME_ACTIVE="/run/chrony/faketime.active"
SEED_FILE="/usr/share/bmc-chrony/chrony.conf.default"

if [ ! -f "${CONF_FILE}" ]; then
    echo "entrypoint: 配置卷缺少 chrony.conf，播种内置默认配置"
    mkdir -p "${CONF_DIR}"
    cp "${SEED_FILE}" "${CONF_FILE}"
fi

# 上次异常退出可能残留 pidfile，导致重启循环里误报"已有 chronyd 运行"
rm -f /run/chrony/chronyd.pid

# 支持 BMC 故障注入：平台把时间偏移渲染进 faketime.conf（不注入时为空值）；
# 文件缺失（配置卷尚未被平台写过）同样视为不注入
FAKETIME_OFFSET=""
if [ -f "${FAKETIME_CONF}" ]; then
    FAKETIME_OFFSET="$(sed -n 's/^FAKETIME_OFFSET=//p' "${FAKETIME_CONF}" | head -n 1)"
fi
# 记录本次进程真正生效的偏移，供 /reload.sh 判断偏移是否变化（变化必须重启进程）
mkdir -p /run/chrony
printf '%s' "${FAKETIME_OFFSET}" > "${FAKETIME_ACTIVE}"

if [ -n "${FAKETIME_OFFSET}" ]; then
    echo "entrypoint: 激活 BMC 故障注入，注入时间偏移: ${FAKETIME_OFFSET}"
    # 直接用 libfaketime 预加载，不经 faketime 包装器：包装器会额外留一层进程，
    # 使 chronyd 不再是 1 号进程，/reload.sh 的 kill -TERM 1 打到包装器上不生效，
    # 容器无法按新偏移重启（包装器不转发信号）。
    # $LIB 由动态链接器展开为架构目录（aarch64-linux-gnu / x86_64-linux-gnu），多架构通用。
    export LD_PRELOAD='/usr/$LIB/faketime/libfaketime.so.1'
    export FAKETIME="${FAKETIME_OFFSET}"
fi

# exec 让 chronyd 取代 shell 成为 1 号进程，容器信号（停止/重启）直达进程。
# -x：不调整本机时钟（容器默认无 CAP_SYS_TIME，宿主机时钟也不该被容器修改），
# 只对外提供 NTP 服务；需要让容器校准宿主机时钟时，在 compose 里加
# cap_add: [SYS_TIME] 并去掉 -x。
exec chronyd -n -x -f "${CONF_FILE}"
