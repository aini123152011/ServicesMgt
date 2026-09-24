#!/bin/sh
# 启动前置：处理"配置卷首次挂载为空"的情况，同步本地 FTP 账号，
# 并以监督循环方式托管 vsftpd（standalone 前台运行）。
# 配置或用户清单缺失时从镜像内置默认播种，保证 cd services/vsftpd && docker compose up -d 即可独立运行。
set -eu

CONF_DIR="/etc/vsftpd"
SEED_DIR="/usr/share/bmc-vsftpd"
USERS_FILE="${CONF_DIR}/local_users.txt"

# 空卷播种：缺配置无法启动；用户清单缺失则播种默认账号（bmcuser/ChangeMe123）
for f in vsftpd.conf local_users.txt; do
    if [ ! -f "${CONF_DIR}/${f}" ]; then
        echo "entrypoint: 配置卷缺少 ${f}，播种内置默认配置"
        mkdir -p "${CONF_DIR}"
        cp "${SEED_DIR}/${f}.default" "${CONF_DIR}/${f}"
    fi
done
# 明文密码清单仅 root 可读；平台渲染写入后权限不保证，这里每次启动收紧
chmod 600 "${USERS_FILE}"

# 家目录根 = 数据卷挂载点（manifest data_dir），FTP 用户目录固定建在其下
ROOT_DIR="/data"

# secure_chroot_dir：vsftpd 需要一个空目录作为安全 chroot 落脚点（Debian 编译默认
# /var/run/vsftpd/empty）。/var/run 在容器内是空 tmpfs，镜像构建期建不出来，缺失时
# vsftpd 对任何连接都直接回 "500 OOPS: not found: directory given in
# 'secure_chroot_dir'"，因此每次启动都要补建（要求属 root 且 ftp 用户不可写）。
mkdir -p /var/run/vsftpd/empty
chown root:root /var/run/vsftpd/empty
chmod 755 /var/run/vsftpd/empty

# 从渲染的用户清单同步本地账号。放在监督循环内执行：reload.sh 终止 vsftpd 后
# 循环会带最新 local_users.txt 重建账号再拉起，平台改用户无需重建容器。
# 幂等：已存在的用户只更新密码；单行异常仅告警跳过，不阻断其余用户
# 由用户名派生一个稳定的 uid（20000–29999）。
# 为什么不能用 useradd 的默认分配：它按镜像内现状取下一个可用 uid，容器重建后会变，
# 而数据卷在多次重建间共享——旧文件对新 uid 不可写（实测 sftp 上传直接 Permission denied）。
# 同一用户名必须永远映射到同一 uid。
stable_uid() {
    printf '%s' "$1" | cksum | awk '{print 20000 + $1 % 10000}'
}

provision_users() {
    while IFS= read -r line || [ -n "${line}" ]; do
        case "${line}" in
            ''|'#'*) continue ;;
            *)
                user="${line%%:*}"
                pass="${line#*:}"
                if [ -z "${user}" ] || [ -z "${pass}" ]; then
                    echo "entrypoint: 跳过无法解析的用户行"
                    continue
                fi
                if ! id -u "${user}" >/dev/null 2>&1; then
                    # nologin：FTP 专用账号；PAM 已改为纯 pam_unix，不会因 shell 白名单拒登
                    useradd -u "$(stable_uid "${user}")" -d "${ROOT_DIR}/${user}" -s /usr/sbin/nologin "${user}"
                fi
                # chpasswd 经 pam_unix 写 /etc/shadow，容器内无 systemd 也可用
                printf '%s:%s\n' "${user}" "${pass}" | chpasswd
                # 家目录即 FTP 登录根：属主设为用户本身，登录后可直接读写
                # （vsftpd 侧已配 allow_writeable_chroot 放行可写 chroot 目录）
                mkdir -p "${ROOT_DIR}/${user}"
                # -R：历史文件可能是旧 uid 建的（重建前 uid 会漂移），一并纠正

                chown -R "${user}:${user}" "${ROOT_DIR}/${user}"
                chmod 755 "${ROOT_DIR}/${user}"
                ;;
        esac
    done < "${USERS_FILE}"
}

# 监督循环：reload.sh 终止 vsftpd 后，循环检测到退出并重建账号、带新配置重新拉起；
# docker stop 的 SIGTERM 由 stop_handler 转发给 vsftpd 后退出容器
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
    provision_users
    # 显式指定配置卷内的配置文件；standalone 模式且 background 默认 NO，进程保持前台
    vsftpd "${CONF_DIR}/vsftpd.conf" &
    CHILD_PID=$!
    wait "${CHILD_PID}" || true
    CHILD_PID=""
    echo "entrypoint: vsftpd 已退出，2 秒后带新配置重启"
    sleep 2
done
