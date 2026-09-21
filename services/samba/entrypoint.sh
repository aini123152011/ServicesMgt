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
sync_users() {
    if [ -f "${CONFIG_DIR}/users.txt" ]; then
        while IFS=: read -r username password || [ -n "${username}" ]; do
            # 跳过空行或注释
            [[ -z "${username}" || "${username}" =~ ^# ]] && continue
            if ! id -u "${username}" >/dev/null 2>&1; then
                useradd -M -s /usr/sbin/nologin "${username}" || true
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

STOPPED=0
trap 'STOPPED=1; pkill -TERM smbd || true; exit 0' SIGTERM SIGINT

echo "entrypoint: 启动 Samba 监督循环..."
while [ "${STOPPED}" -eq 0 ]; do
    sync_users
    smbd -F --no-process-group -s "${CONFIG_DIR}/smb.conf" &
    SMBD_PID=$!
    wait "${SMBD_PID}" || true
    if [ "${STOPPED}" -eq 1 ]; then
        break
    fi
    echo "entrypoint: smbd 已退出，1 秒后以新配置拉起..."
    sleep 1
done
