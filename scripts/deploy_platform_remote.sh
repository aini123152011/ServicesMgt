#!/usr/bin/env bash
# 目标机平台更新：把本地打包好的 platform 更新包部署到目标机并重建平台容器。
#
# 用法（本地，在仓库根目录）：
#   tar --exclude='__pycache__' -czf /tmp/platform-update.tgz \
#     platform/backend/app platform/backend/scripts platform/frontend/dist scripts/verify_bmc_platform_e2e.py
#   ssh root@<目标机> 'cat > /tmp/platform-update.tgz' < /tmp/platform-update.tgz
#   ssh root@<目标机> 'VERSION=0.4.7 bash -s' < scripts/deploy_platform_remote.sh
#
# 镜像来源：默认**从镜像仓库拉取**平台镜像（IMAGE_PREFIX/IMAGE_TAG，见 .env），
# 不再在目标机构建；需要离线或紧急本地构建时用 BUILD_LOCAL=1。
#
# 设计要点：**运行参数从现有容器读回**（环境变量、端口、网络、挂载），脚本里不写任何密钥——
# 平台容器的 DATABASE_URL / FIRST_SUPERUSER_PASSWORD / SECRET_KEY 只存在于容器运行时，
# 不落到仓库与脚本里。凭据有变动时改容器即可，脚本无需跟着改。
set -euo pipefail

VERSION="${VERSION:-dev}"
# 镜像来源：IMAGE_PREFIX 含结尾斜杠（如 docker.io/<账号>/），IMAGE_TAG 默认与 VERSION 一致
IMAGE_PREFIX="${IMAGE_PREFIX:-}"
IMAGE_TAG="${IMAGE_TAG:-$VERSION}"
IMAGE_REF="${IMAGE_PREFIX}fx-platform:${IMAGE_TAG}"
BUILD_LOCAL="${BUILD_LOCAL:-0}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/fx-deploy}"
CONTAINER="${CONTAINER:-fx-platform}"
PACKAGE="${PACKAGE:-/tmp/platform-update.tgz}"

cd "$DEPLOY_DIR"

echo "==> 解包 $PACKAGE"
tar xzf "$PACKAGE"
# 验收套件与探针脚本都放到部署根：套件按**自身所在目录**找探针（v6_probe.py / dhcp_probe.py），
# 少拷一个就会出现「套件在跑但整段探针用例 FAIL 缺脚本」——实测踩过（dhcp 阶段 18.2–18.21 全挂）
cp -f scripts/verify_bmc_platform_e2e.py "$DEPLOY_DIR/verify_bmc_platform_e2e.py"
cp -f scripts/v6_probe.py "$DEPLOY_DIR/v6_probe.py"
cp -f scripts/dhcp_probe.py "$DEPLOY_DIR/dhcp_probe.py"

# 二层夹具的网口提示（只读、只打印）：carrier=1 才是插了线的口。
# 不代写 .env、不代改宿主机网络——部署脚本是 `ssh 'bash -s' < script` 跑的，stdin 被脚本占用，
# 交互提问会卡死；改配置的事交给人。
echo "==> 宿主网口（carrier=1 才是插了线的）"
for d in /sys/class/net/*; do
  iface=$(basename "$d")
  case "$iface" in lo|docker*|br-*|veth*|virbr*|tun*|tap*) continue ;; esac
  carrier=$(cat "$d/carrier" 2>/dev/null || echo "?")
  speed=$(cat "$d/speed" 2>/dev/null || echo "-")
  addr=$(ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{print $4}' | head -1)
  printf "    %-16s carrier=%-3s speed=%-7s %s
" "$iface" "$carrier" "$speed" "${addr:--}"
done
candidates=$(for d in /sys/class/net/*; do
  iface=$(basename "$d")
  case "$iface" in lo|docker*|br-*|veth*|virbr*|tun*|tap*) continue ;; esac
  carrier=$(cat "$d/carrier" 2>/dev/null || echo 0)
  addr=$(ip -4 -o addr show dev "$iface" 2>/dev/null | awk '{print $4}' | head -1)
  # 候选：插了线、且当前没有地址（说明还没被管理/业务网占用）
  if [ "$carrier" = "1" ] && [ -z "$addr" ]; then echo "$iface"; fi
done | tr '
' ' ')
if [ -n "$candidates" ]; then
  echo "    二层夹具候选（插了线且无地址）：$candidates"
fi
echo "    提示：二层夹具（启用 / 切换 / 停用）现在可以在平台页面上做："
echo "          设置 → 宿主网口面板 → 选父口与网段 → 应用（平台会改写 .env 并重建 macvlan 网络）"
echo "          只有一步仍要人工：给宿主测试口配址（必须 never-default，否则会抢走宿主默认路由）"
echo "          nmcli con mod <测试口> ipv4.addresses <L2_GATEWAY>/<前缀> ipv4.gateway \"\" ipv4.never-default yes"

echo "==> 读回现有容器的运行参数"
if ! docker inspect "$CONTAINER" >/dev/null 2>&1; then
  echo "ERROR: 容器 $CONTAINER 不存在，首次部署请手工 docker run 后再用本脚本增量更新" >&2
  exit 1
fi
# 排除 PLATFORM_VERSION / PLATFORM_BUILD：这两个是镜像构建期注入的版本元数据（与
# app/container_rebuild.py 的 IMAGE_OWNED_ENV_KEYS 同一份名单）。照抄旧容器的值会让
# 新镜像继续报旧版本——实测过一次：跑着 0.5.0 的镜像却报 0.4.7。
mapfile -t envs < <(docker inspect "$CONTAINER" --format '{{range .Config.Env}}{{println .}}{{end}}' \
  | grep -v '^$' \
  | grep -vE '^(PLATFORM_VERSION|PLATFORM_BUILD)=')
binds=$(docker inspect "$CONTAINER" --format '{{range .HostConfig.Binds}}-v {{.}} {{end}}')
# 部署目录挂载（平台用它读写 .env 里的二层夹具参数）：老容器没有这一项，缺了就补上，
# 否则每次部署都会把「页面上启用/切换二层夹具」的能力丢掉（挂载无法后加，只能重建时带上）
case "$binds" in
  *":/host-deploy"*) ;;
  *) binds="${binds}-v ${DEPLOY_DIR}:/host-deploy " ;;
esac
port=$(docker inspect "$CONTAINER" --format '{{range $p, $conf := .HostConfig.PortBindings}}{{range $conf}}{{.HostPort}}:{{$p}}{{end}}{{end}}')
network=$(docker inspect "$CONTAINER" --format '{{.HostConfig.NetworkMode}}')
restart=$(docker inspect "$CONTAINER" --format '{{.HostConfig.RestartPolicy.Name}}')

if [ "$BUILD_LOCAL" = "1" ]; then
  echo "==> 本地构建 fx-platform:$VERSION（BUILD_LOCAL=1，基于现有 latest 的分层构建）"
else
  echo "==> 拉取平台镜像 $IMAGE_REF"
  docker pull "$IMAGE_REF" || {
    echo "ERROR: 拉取 $IMAGE_REF 失败。检查 .env 的 IMAGE_PREFIX/IMAGE_TAG、是否已 docker login、以及该 tag 是否已发布" >&2
    exit 1
  }
fi

if [ "$BUILD_LOCAL" = "1" ]; then
# Dockerfile.platform.fast 是 `FROM fx-platform:latest` 的分层增量构建，每次叠约 5 层；
# overlay2 的下层上限是 128 层，累计到 120+ 层时构建会直接失败（报 "max depth exceeded"，
# 实测 122 层触发）。超过阈值就先把 latest 压成单层：docker commit 只会在原层上再加一层，
# 真正压平要用 export（导出完整文件系统）+ import（单层重建）。
MAX_LAYERS="${MAX_LAYERS:-100}"
layers=$(docker inspect --format '{{len .RootFS.Layers}}' fx-platform:latest 2>/dev/null || echo 0)
if [ "${layers:-0}" -gt "$MAX_LAYERS" ]; then
  echo "==> latest 已有 $layers 层（阈值 $MAX_LAYERS），先压成单层"
  flat_cid=$(docker create fx-platform:latest)
  docker export "$flat_cid" -o /tmp/fx-platform-flat.tar
  docker rm "$flat_cid" >/dev/null
  # import 不继承镜像配置（ENV/WORKDIR 会丢，PATH 丢了容器就找不到 fastapi），逐条搬过来
  mapfile -t flat_envs < <(docker inspect fx-platform:latest \
    --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -v '^$')
  flat_changes=()
  for entry in "${flat_envs[@]}"; do
    flat_changes+=(--change "ENV $entry")
  done
  docker import "${flat_changes[@]}" --change 'WORKDIR /app/backend' \
    /tmp/fx-platform-flat.tar fx-platform:flat >/dev/null
  docker tag fx-platform:flat fx-platform:latest
  rm -f /tmp/fx-platform-flat.tar
  echo "==> 压缩完成：$(docker inspect --format '{{len .RootFS.Layers}}' fx-platform:latest) 层"
fi

docker build -f Dockerfile.platform.fast \
  -t "$IMAGE_REF" \
  --build-arg APP_VERSION="$VERSION" \
  --build-arg APP_BUILD="$(date +%Y%m%d%H%M)" \
  . 2>&1 | tail -3
docker tag "$IMAGE_REF" fx-platform:latest
fi   # BUILD_LOCAL

echo "==> 先跑数据库迁移（用新镜像，在切流之前）"
# 顺序有意为之：迁移必须由**新镜像**执行（它才带新迁移文件），且要在新容器接管流量前完成，
# 否则新代码会短暂跑在旧表结构上（新接口 500）。一次性容器只挂网络与环境变量，不碰 docker.sock。
# 环境变量逐个作为独立数组元素传递：值里可能含 !# 之类的字符，拼字符串会被 shell 再解释一次
env_args=()
for entry in "${envs[@]}"; do
  env_args+=(-e "$entry")
done
docker run --rm --network "$network" \
  "${env_args[@]}" \
  "fx-platform:$VERSION" alembic upgrade head

echo "==> 重建容器（沿用原运行参数：网络 $network / 端口 $port / 重启策略 $restart）"
docker rm -f "$CONTAINER" >/dev/null
# shellcheck disable=SC2086  # binds 需要按空格拆分
docker run -d --name "$CONTAINER" --restart "$restart" \
  --network "$network" -p "$port" \
  $binds \
  "${env_args[@]}" \
  "$IMAGE_REF" >/dev/null

echo "==> 等待健康检查"
for _ in $(seq 1 40); do
  status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER" 2>/dev/null || echo missing)
  [ "$status" = healthy ] && break
  sleep 3
done
docker inspect -f '{{.State.Health.Status}} image={{.Config.Image}}' "$CONTAINER"
