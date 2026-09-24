#!/bin/sh
# 启动前置：处理"配置卷首次挂载为空"的情况，把渲染的用户清单转换为 htpasswd 凭据，
# 并以监督循环方式托管 apache2（-DFOREGROUND 前台运行）。
# 配置或用户清单缺失时从镜像内置默认播种，保证 cd services/webdav && docker compose up -d 即可独立运行。
set -eu

CONF_DIR="/etc/apache2-bmc"
SEED_DIR="/usr/share/bmc-webdav"
USERS_FILE="${CONF_DIR}/users.txt"
HTPASSWD_FILE="${CONF_DIR}/htpasswd"

# 空卷播种：缺配置无法启动；用户清单缺失则播种默认账号（bmc/ChangeMe123）
for f in apache2.conf users.txt; do
    if [ ! -f "${CONF_DIR}/${f}" ]; then
        echo "entrypoint: 配置卷缺少 ${f}，播种内置默认配置"
        mkdir -p "${CONF_DIR}"
        cp "${SEED_DIR}/${f}.default" "${CONF_DIR}/${f}"
    fi
done
# 明文密码清单仅 root 可读；目录对 apache 子进程(www-data)保持可进入以便读取凭据
chmod 600 "${USERS_FILE}"
chmod 755 "${CONF_DIR}"

# apache2 运行时目录：渲染配置中的 PidFile/互斥锁落在 /var/run/apache2
mkdir -p /var/run/apache2

# 把渲染的用户清单转为 Basic 认证凭据。放在监督循环内执行：reload.sh 终止 apache2 后
# 循环会带最新 users.txt 重建凭据再拉起，平台改用户无需重建容器。
# 幂等：每次全量重建；单行异常仅告警跳过，不阻断其余用户
build_htpasswd() {
    # root_dir 与 schema 同名字段对应，随用户清单一起渲染，缺省回退 /var/lib/dav
    ROOT_DIR="/var/lib/dav"
    tmp="$(mktemp)"
    count=0
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
                # -B：bcrypt 存储，不在磁盘留明文/弱散列；首个用户用 -c 创建文件
                if [ "${count}" -eq 0 ]; then
                    htpasswd -cbB "${tmp}" "${user}" "${pass}" >/dev/null
                else
                    htpasswd -bB "${tmp}" "${user}" "${pass}" >/dev/null
                fi
                count=$((count + 1))
                ;;
        esac
    done < "${USERS_FILE}"
    if [ "${count}" -eq 0 ]; then
        # 无用户：写空凭据文件；auth_enabled 开启时登录将全部失败，
        # 属平台配置问题，由平台侧 schema 校验拦截
        : > "${tmp}"
    fi
    # 原子替换，避免 apache 读到半成品；属主收敛为 root:www-data，子进程只需读
    mv "${tmp}" "${HTPASSWD_FILE}"
    chown root:www-data "${HTPASSWD_FILE}"
    chmod 640 "${HTPASSWD_FILE}"
    # DAV 根目录与文件锁目录兜底创建并交给 www-data：
    # 渲染配置的 DocumentRoot/DavLockDB 都指向这里，目录不可写会导致写请求 500
    mkdir -p "${ROOT_DIR}/.davlock"
    chown www-data:www-data "${ROOT_DIR}" "${ROOT_DIR}/.davlock"
    chmod 755 "${ROOT_DIR}" "${ROOT_DIR}/.davlock"
}

# 监督循环：reload.sh 终止 apache2 后，循环检测到退出并重建凭据、带新配置重新拉起；
# docker stop 的 SIGTERM 由 stop_handler 转发给 apache2 后退出容器
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
    build_htpasswd
    # -f：显式加载配置卷内的自包含配置；-DFOREGROUND：前台运行，
    # 日志由配置直通 stdout/stderr 由 docker logs 捕获
    apache2 -f "${CONF_DIR}/apache2.conf" -DFOREGROUND &
    CHILD_PID=$!
    wait "${CHILD_PID}" || true
    CHILD_PID=""
    echo "entrypoint: apache2 已退出，2 秒后带新配置重启"
    sleep 2
done
