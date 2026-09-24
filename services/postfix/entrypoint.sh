#!/bin/sh
# 启动前置：处理"配置卷首次挂载为空"的情况，修复 chroot 所需目录，
# 并以监督循环方式托管 postfix（start-fg 前台运行）。
set -eu

CONF_DIR="/etc/postfix"
SEED_DIR="/usr/share/fx-postfix"

# 回填 postfix 必需支撑文件（如 postfix-files, post-install, postfix-script 等）
if [ -d "${SEED_DIR}/seed_etc" ]; then
    cp -a -n "${SEED_DIR}/seed_etc/." "${CONF_DIR}/" 2>/dev/null || cp -a -u "${SEED_DIR}/seed_etc/." "${CONF_DIR}/" 2>/dev/null || true
fi

# 平台只渲染 main.cf；master.cf / dynamicmaps.cf 是包自带的必需支撑配置，
# 缺失时从镜像内置快照回填（逐文件播种，兼容平台先渲染后启动的"非空卷"场景）
for f in main.cf master.cf dynamicmaps.cf; do
    if [ ! -f "${CONF_DIR}/${f}" ]; then
        echo "entrypoint: 配置卷缺少 ${f}，播种内置默认配置"
        mkdir -p "${CONF_DIR}"
        cp "${SEED_DIR}/${f}.default" "${CONF_DIR}/${f}"
    fi
done

# chroot 目录修复：Debian 默认 master.cf 让 smtpd 等守护进程在队列目录 chroot 内运行，
# chroot 后读不到 /etc，需把解析所依赖的文件预置进去；bookworm 的 glibc 2.36 已把
# nss_dns/nss_files 编入 libc，无需再复制共享库
mkdir -p /var/spool/postfix/etc
cp -f /etc/resolv.conf /etc/hosts /etc/services /var/spool/postfix/etc/
if [ -f /etc/localtime ]; then
    cp -f /etc/localtime /var/spool/postfix/etc/
fi

# 队列子目录与属主兜底：平台先建卷后启动（无镜像内容拷贝）或异常卷时，
# postfix 对 public/maildrop 有固定属主/权限要求，预先修正避免启动失败
mkdir -p /var/spool/postfix/public /var/spool/postfix/maildrop
chown postfix:postdrop /var/spool/postfix/public /var/spool/postfix/maildrop 2>/dev/null || true

# 生成别名库（main.cf 的 alias_database 指向 /etc/aliases），缺失时 postfix 启动告警
newaliases

# 监督循环：reload.sh 执行 postfix stop 后，循环检测到退出并带新配置重新拉起；
# docker stop 的 SIGTERM 由 stop_handler 转发给 master 后退出容器
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
    # start-fg：前台启动 master（postfix 3.3+ 支持），日志走 stderr 由 docker logs 捕获
    postfix start-fg &
    CHILD_PID=$!
    wait "${CHILD_PID}" || true
    CHILD_PID=""
    echo "entrypoint: postfix master 已退出，2 秒后带新配置重启"
    sleep 2
done
