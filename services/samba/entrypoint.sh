#!/bin/bash
# samba 服务启动入口：
# 1. 初始化空配置卷
# 2. 从 users.txt 读取并创建/更新系统用户与 samba 密码
# 3. 准备数据目录与权限
# 4. 监督循环拉起 smbd（支持 reload 触发后自动重启）
set -euo pipefail

CONFIG_DIR="/etc/samba-bmc"
DEFAULTS_DIR="/etc/samba-bmc.default"
DATA_DIR="/data/samba"

# 1. 空卷播种
if [ ! -f "${CONFIG_DIR}/smb.conf" ]; then
    echo "entrypoint: 配置目录为空，从默认模板初始化..."
    mkdir -p "${CONFIG_DIR}"
    if [ -d "${DEFAULTS_DIR}" ]; then
        cp -a "${DEFAULTS_DIR}/." "${CONFIG_DIR}/"
    fi
fi

# 2. 同步 Samba 用户
# 由用户名派生一个稳定的 uid（20000–29999）。
# 为什么不能用 useradd 的默认分配：它按镜像内现状取下一个可用 uid，容器重建后会变，
# 而数据卷在多次重建间共享——旧文件对新 uid 不可写（实测 sftp 上传直接 Permission denied）。
# 同一用户名必须永远映射到同一 uid。
stable_uid() {
    printf '%s' "$1" | cksum | awk '{print 20000 + $1 % 10000}'
}

sync_users() {
    if [ -f "${CONFIG_DIR}/users.txt" ]; then
        while IFS=: read -r username password || [ -n "${username}" ]; do
            # 跳过空行或注释
            [[ -z "${username}" || "${username}" =~ ^# ]] && continue
            if ! id -u "${username}" >/dev/null 2>&1; then
                useradd -u "$(stable_uid "${username}")" -M -s /usr/sbin/nologin "${username}" || true
            fi
            printf "%s\n%s\n" "${password}" "${password}" | smbpasswd -a -s "${username}" >/dev/null 2>&1 || true
            smbpasswd -e "${username}" >/dev/null 2>&1 || true
            echo "entrypoint: 用户 ${username} 已就绪"
        done < "${CONFIG_DIR}/users.txt"
    fi
}

sync_users

# 3. 数据目录准备
mkdir -p "${DATA_DIR}"
chmod 777 "${DATA_DIR}"
# 历史文件可能是旧 uid 建的、且权限不含 other 写（重建前 uid 会漂移，实测 e2e_smb.txt 为 764），
# 共享目录下统一放开写权限——共享是给多个 samba 账号共用的，逐个 chown 到某个账号反而不对。
chmod -R a+rw "${DATA_DIR}"

# 3.5 运行期配置副本：把渲染产物里的 @BMC_DATE_DIR@ 换成「今天的日期段」。
# 为什么不在模板里直接用 Samba 的日期 token：实测（Samba 4.17 / bookworm）log file 里
# %Y 原样输出成字面量 "%Y"、%M/%m/%I/%D 全是客户端信息，没有年/月/日 token；
# 而且 smbd 不会自建缺失的父目录——目录不在时它静默不写任何日志（连报错都没有）。
# 所以日期只能在这里补，并且必须先把日期目录建好。
# 生成的副本放在 /run 下，平台渲染产物保持原样（渲染产物始终是唯一事实来源）。
LOG_ROOT_DEFAULT="/var/log/samba"
RUNTIME_CONF_DIR="/run/smbd-bmc"
RUNTIME_CONF="${RUNTIME_CONF_DIR}/smb.conf"

prepare_runtime_conf() {
    local log_root date_dir segment
    log_root="$(sed -n 's/^# runtime: log_root=//p' "${CONFIG_DIR}/smb.conf" | tail -n 1)"
    date_dir="$(sed -n 's/^# runtime: date_dir=//p' "${CONFIG_DIR}/smb.conf" | tail -n 1)"
    log_root="${log_root:-${LOG_ROOT_DEFAULT}}"
    case "${date_dir:-daily}" in
        daily) segment="$(date +%Y-%m-%d)" ;;
        monthly) segment="$(date +%Y-%m)" ;;
        *) segment="" ;;
    esac
    mkdir -p "${RUNTIME_CONF_DIR}" "${log_root}"
    if [ -n "${segment}" ]; then
        mkdir -p "${log_root}/${segment}"
        sed "s|@BMC_DATE_DIR@|${segment}|g" "${CONFIG_DIR}/smb.conf" > "${RUNTIME_CONF}"
    else
        # date_dir=none：模板本就不带占位符，这里顺带兼容（连前面的 '/' 一起去掉）
        sed "s|/@BMC_DATE_DIR@||g" "${CONFIG_DIR}/smb.conf" > "${RUNTIME_CONF}"
    fi
    echo "entrypoint: 日志目录 ${log_root}/${segment}（date_dir=${date_dir:-daily}）"
}

STOPPED=0
trap 'STOPPED=1; pkill -TERM smbd || true; exit 0' SIGTERM SIGINT

echo "entrypoint: 启动 Samba 监督循环..."
while [ "${STOPPED}" -eq 0 ]; do
    sync_users
    # 每轮都重算日期段：reload 会走到这里，跨零点后 reload 一次即可切到新日期目录
    prepare_runtime_conf
    smbd -F --no-process-group -s "${RUNTIME_CONF}" &
    SMBD_PID=$!
    wait "${SMBD_PID}" || true
    if [ "${STOPPED}" -eq 1 ]; then
        break
    fi
    echo "entrypoint: smbd 已退出，1 秒后以新配置拉起..."
    sleep 1
done
