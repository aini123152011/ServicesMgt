#!/bin/sh
# 启动前置：处理"配置卷首次挂载为空"的情况，准备主机密钥与用户账号，
# 并以监督循环方式托管 sshd。
# 配置或用户清单缺失时从镜像内置默认播种，保证 cd services/sftp && docker compose up -d 即可独立运行。
set -eu

CONF_DIR="/etc/ssh"
SEED_DIR="/usr/share/fx-sftp"
USERS_FILE="${CONF_DIR}/users.txt"

# 空卷播种：sshd 缺配置无法启动；用户清单缺失则播种默认账号（bmcadmin/ChangeMe123）
for f in sshd_config users.txt; do
    if [ ! -f "${CONF_DIR}/${f}" ]; then
        echo "entrypoint: 配置卷缺少 ${f}，播种内置默认配置"
        mkdir -p "${CONF_DIR}"
        cp "${SEED_DIR}/${f}.default" "${CONF_DIR}/${f}"
    fi
done
# 明文密码清单仅 root 可读；平台渲染写入后权限不保证，这里每次启动收紧
chmod 600 "${USERS_FILE}"

# 主机密钥：镜像构建时清空了 /etc/ssh，包 postinst 的密钥生成不会再执行，
# 由 ssh-keygen -A 按需补齐（幂等，只生成缺失的；写入配置卷后重建容器不丢失）
ssh-keygen -A

# 特权分离目录：sshd 启动的硬性要求，缺失会直接退出
mkdir -p /run/sshd

# 从渲染的用户清单同步本地账号。放在监督循环内执行：reload.sh 终止 sshd 后
# 循环会带最新 users.txt 重建账号再拉起，平台改用户无需重建容器。
# 幂等：已存在的用户只更新密码；单行异常仅告警跳过，不阻断其余用户
# 由用户名派生一个稳定的 uid（20000–29999）。
# 为什么不能用 useradd 的默认分配：它按镜像内现状取下一个可用 uid，容器重建后会变
# （实测 1001 → 1000），而数据卷在多次重建间共享——旧文件对新 uid 不可写，SFTP 上传直接
# Permission denied。同一用户名必须永远映射到同一 uid。
stable_uid() {
    printf '%s' "$1" | cksum | awk '{print 20000 + $1 % 10000}'
}

provision_users() {
    # root_dir 与 schema 同名字段对应，随用户清单一起渲染，缺省回退 /data
    ROOT_DIR="/data"
    while IFS= read -r line || [ -n "${line}" ]; do
        case "${line}" in
            ''|'#'*) continue ;;
            root_dir=*)
                ROOT_DIR="${line#root_dir=}"
                ;;
            *)
                user="${line%%:*}"
                pass="${line#*:}"
                if [ -z "${user}" ] || [ -z "${pass}" ]; then
                    echo "entrypoint: 跳过无法解析的用户行"
                    continue
                fi
                if ! id -u "${user}" >/dev/null 2>&1; then
                    # nologin：SFTP 专用账号不允许 shell 登录；
                    # 不用 -M 预建家目录，由下方按 ChrootDirectory 属主要求手工准备
                    useradd -u "$(stable_uid "${user}")" -d "${ROOT_DIR}/${user}"                         -s /usr/sbin/nologin "${user}"
                fi
                # chpasswd 经 pam_unix 写 /etc/shadow，容器内无 systemd 也可用
                printf '%s:%s\n' "${user}" "${pass}" | chpasswd
                # chroot 目录链必须属 root 且用户不可写（ChrootDirectory 硬性要求），
                # 实际读写放行到其下 data/ 子目录
                mkdir -p "${ROOT_DIR}/${user}/data"
                chown root:root "${ROOT_DIR}" "${ROOT_DIR}/${user}"
                chmod 755 "${ROOT_DIR}" "${ROOT_DIR}/${user}"
                # -R：历史文件可能是旧 uid 建的（重建前 uid 会漂移），一并纠正过来
                chown -R "${user}:${user}" "${ROOT_DIR}/${user}/data"
                ;;
        esac
    done < "${USERS_FILE}"
}

# 监督循环：reload.sh 终止 sshd 后，循环检测到退出并重建账号、带新配置重新拉起；
# docker stop 的 SIGTERM 由 stop_handler 转发给 sshd 后退出容器
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
    # NOTE: sshd 要求以绝对路径启动（内部 re-exec 校验 argv[0]），不能只写 sshd；
    # -D：前台不 fork；-e：日志走 stderr 由 docker logs 捕获
    /usr/sbin/sshd -D -e -f "${CONF_DIR}/sshd_config" &
    CHILD_PID=$!
    wait "${CHILD_PID}" || true
    CHILD_PID=""
    echo "entrypoint: sshd 已退出，2 秒后带新配置重启"
    sleep 2
done
