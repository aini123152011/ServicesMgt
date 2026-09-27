#!/bin/sh
# 证书服务（CA）启动入口：空卷播种 → 准备两个卷 → 准备/校验根 → 监督循环托管 API。
#
# 进程模型：本脚本是 1 号进程，在监督循环里反复拉起 api.py；/reload.sh 杀掉 api.py 后，
# 循环带新配置重启它（容器不重启，避免 Docker 重启退避与重启期间 docker exec 被 409 拒绝）。
#
# 私钥边界：/srv/pki-secrets 只放私钥（CA 私钥、签发出的私钥），manifest 不把该目录声明为
# data_dir —— 平台的数据浏览接口按 data_dir 递归读并原样返回，任何登录用户都能看。
set -eu

CONF_DIR="/etc/pki-bmc"
CONF_FILE="${CONF_DIR}/pki.conf"
SEED_FILE="/usr/share/bmc-pki/pki.conf.default"
SECRETS_DIR="/srv/pki-secrets"
DATA_DIR="/data/pki"
API_SCRIPT="/usr/local/bin/api.py"
SUPERVISOR_PIDFILE="/run/pki/api.pid"

# 1) 空卷播种：配置卷首次挂载为空时，用镜像内置默认配置补上
if [ ! -f "${CONF_FILE}" ]; then
    echo "entrypoint: 配置卷缺少 pki.conf，播种内置默认配置"
    mkdir -p "${CONF_DIR}"
    cp "${SEED_FILE}" "${CONF_FILE}"
fi

# 2) 两个卷的目录与权限：密钥目录收紧（私钥 0600、目录 0700），公开产物目录可浏览
mkdir -p "${SECRETS_DIR}/ca" "${SECRETS_DIR}/issued" \
         "${DATA_DIR}/ca" "${DATA_DIR}/issued" "${CONF_DIR}"
chmod 700 "${SECRETS_DIR}" "${SECRETS_DIR}/ca" "${SECRETS_DIR}/issued"
# /run 是容器运行时挂载的空 tmpfs，镜像构建期建的目录运行时不存在，必须在启动时补建
mkdir -p /run/pki

# 3) 启动前自检：解析配置 + 生成/安装根 + 校验证书与私钥配对。
#    失败即退出（不进入监督循环）——带着不配对的根启动只会让每次签发都失败，
#    明确失败比"进程活着但签不出证书"更容易被发现。
if ! python3 "${API_SCRIPT}" --config "${CONF_FILE}" --prepare-only; then
    echo "entrypoint: CA 准备/校验失败，拒绝带病启动（检查 ca_cert_pem / ca_key_pem 是否配对）" >&2
    exit 1
fi

# 4) 容器停止：把信号转给 api.py，避免留下孤儿进程（1 号进程收到的是 docker stop 的 SIGTERM）
API_PID=""
stop_handler() {
    if [ -n "${API_PID}" ]; then
        kill -TERM "${API_PID}" 2>/dev/null || true
        wait "${API_PID}" 2>/dev/null || true
    fi
    exit 0
}
trap stop_handler TERM INT

# 5) 监督循环：每轮都重新执行 api.py（进程启动时重读 pki.conf），因此 reload 即重读配置
while :; do
    python3 "${API_SCRIPT}" --config "${CONF_FILE}" &
    API_PID=$!
    printf '%s' "${API_PID}" > "${SUPERVISOR_PIDFILE}"
    wait "${API_PID}" || true
    API_PID=""
    echo "entrypoint: pki API 已退出，1 秒后带新配置重启"
    sleep 1
done
