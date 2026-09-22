#!/usr/bin/env bash
# 目标机平台更新：把本地打包好的 platform 更新包部署到目标机并重建平台容器。
#
# 用法（本地，在仓库根目录）：
#   tar --exclude='__pycache__' -czf /tmp/platform-update.tgz \
#     platform/backend/app platform/backend/scripts platform/frontend/dist scripts/verify_bmc_platform_e2e.py
#   ssh root@<目标机> 'cat > /tmp/platform-update.tgz' < /tmp/platform-update.tgz
#   ssh root@<目标机> 'VERSION=0.4.7 bash -s' < scripts/deploy_platform_remote.sh
#
# 设计要点：**运行参数从现有容器读回**（环境变量、端口、网络、挂载），脚本里不写任何密钥——
# 平台容器的 DATABASE_URL / FIRST_SUPERUSER_PASSWORD / SECRET_KEY 只存在于容器运行时，
# 不落到仓库与脚本里。凭据有变动时改容器即可，脚本无需跟着改。
set -euo pipefail

VERSION="${VERSION:-dev}"
DEPLOY_DIR="${DEPLOY_DIR:-/opt/bmc-servicesmgt-deploy}"
CONTAINER="${CONTAINER:-bmc-platform-backend}"
PACKAGE="${PACKAGE:-/tmp/platform-update.tgz}"

cd "$DEPLOY_DIR"

echo "==> 解包 $PACKAGE"
tar xzf "$PACKAGE"
cp -f scripts/verify_bmc_platform_e2e.py "$DEPLOY_DIR/verify_bmc_platform_e2e.py"

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
port=$(docker inspect "$CONTAINER" --format '{{range $p, $conf := .HostConfig.PortBindings}}{{range $conf}}{{.HostPort}}:{{$p}}{{end}}{{end}}')
network=$(docker inspect "$CONTAINER" --format '{{.HostConfig.NetworkMode}}')
restart=$(docker inspect "$CONTAINER" --format '{{.HostConfig.RestartPolicy.Name}}')

echo "==> 构建 bmc-platform:$VERSION（基于现有 latest 的分层构建）"
# Dockerfile.platform.fast 是 `FROM bmc-platform:latest` 的分层增量构建，每次叠约 5 层；
# overlay2 的下层上限是 128 层，累计到 120+ 层时构建会直接失败（报 "max depth exceeded"，
# 实测 122 层触发）。超过阈值就先把 latest 压成单层：docker commit 只会在原层上再加一层，
# 真正压平要用 export（导出完整文件系统）+ import（单层重建）。
MAX_LAYERS="${MAX_LAYERS:-100}"
layers=$(docker inspect --format '{{len .RootFS.Layers}}' bmc-platform:latest 2>/dev/null || echo 0)
if [ "${layers:-0}" -gt "$MAX_LAYERS" ]; then
  echo "==> latest 已有 $layers 层（阈值 $MAX_LAYERS），先压成单层"
  flat_cid=$(docker create bmc-platform:latest)
  docker export "$flat_cid" -o /tmp/bmc-platform-flat.tar
  docker rm "$flat_cid" >/dev/null
  # import 不继承镜像配置（ENV/WORKDIR 会丢，PATH 丢了容器就找不到 fastapi），逐条搬过来
  mapfile -t flat_envs < <(docker inspect bmc-platform:latest \
    --format '{{range .Config.Env}}{{println .}}{{end}}' | grep -v '^$')
  flat_changes=()
  for entry in "${flat_envs[@]}"; do
    flat_changes+=(--change "ENV $entry")
  done
  docker import "${flat_changes[@]}" --change 'WORKDIR /app/backend' \
    /tmp/bmc-platform-flat.tar bmc-platform:flat >/dev/null
  docker tag bmc-platform:flat bmc-platform:latest
  rm -f /tmp/bmc-platform-flat.tar
  echo "==> 压缩完成：$(docker inspect --format '{{len .RootFS.Layers}}' bmc-platform:latest) 层"
fi

docker build -f Dockerfile.platform.fast \
  -t "bmc-platform:$VERSION" \
  --build-arg APP_VERSION="$VERSION" \
  --build-arg APP_BUILD="$(date +%Y%m%d%H%M)" \
  . 2>&1 | tail -3
docker tag "bmc-platform:$VERSION" bmc-platform:latest

echo "==> 重建容器（沿用原运行参数：网络 $network / 端口 $port / 重启策略 $restart）"
docker rm -f "$CONTAINER" >/dev/null
# 环境变量逐个作为独立数组元素传递：值里可能含 !# 之类的字符，拼字符串会被 shell 再解释一次
env_args=()
for entry in "${envs[@]}"; do
  env_args+=(-e "$entry")
done
# shellcheck disable=SC2086  # binds 需要按空格拆分
docker run -d --name "$CONTAINER" --restart "$restart" \
  --network "$network" -p "$port" \
  $binds \
  "${env_args[@]}" \
  "bmc-platform:$VERSION" >/dev/null

echo "==> 等待健康检查"
for _ in $(seq 1 40); do
  status=$(docker inspect -f '{{if .State.Health}}{{.State.Health.Status}}{{else}}none{{end}}' "$CONTAINER" 2>/dev/null || echo missing)
  [ "$status" = healthy ] && break
  sleep 3
done
docker inspect -f '{{.State.Health.Status}} image={{.Config.Image}}' "$CONTAINER"
