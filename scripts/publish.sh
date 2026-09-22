#!/usr/bin/env bash
# 镜像发布入口（本地与 CI 共用）：把 11 个服务镜像与平台镜像以**多架构 manifest**推到镜像仓库。
#
# 用法：
#   # 推到一家（前缀含结尾斜杠）
#   REGISTRIES="docker.io/aini123152008/" TAG=latest \
#     DOCKERHUB_USER=... DOCKERHUB_TOKEN=... bash scripts/publish.sh
#
#   # 推多家（逗号分隔，按顺序逐个推送）
#   REGISTRIES="docker.io/aini123152008/,ghcr.io/aini123152011/,<内网 GitLab 容器仓库>/" \
#     TAG=0.6.2 GHCR_USER=... GHCR_TOKEN=... GITLAB_USER=... GITLAB_TOKEN=... bash scripts/publish.sh
#
#   # 只发布服务镜像 / 只发布平台镜像
#   SERVICES_ONLY=1 ... bash scripts/publish.sh
#   PLATFORM_ONLY=1 ... bash scripts/publish.sh
#
# 凭据只从环境变量读，**不写进仓库、不落盘**；某家仓库没给凭据就跳过（不失败），
# 这样同一份脚本在「只有 GITHUB_TOKEN 的 CI」和「本地手动发布」两种场景下都能用。
#
# 可覆盖的环境变量：PLATFORMS（默认 linux/arm64,linux/amd64）、TAG、NAME_PREFIX、
#   BUILD_ARGS（透传给服务镜像构建，如 APT_MIRROR=mirrors.aliyun.com）、
#   SKIP_FRONTEND=1（跳过前端构建，CI 里前端已单独构建过时用）、
#   SERVICES_ONLY=1 / PLATFORM_ONLY=1、DRY_RUN=1（只打印不推送）。
set -uo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT_DIR"

REGISTRIES="${REGISTRIES:-}"
TAG="${TAG:-latest}"
PLATFORMS="${PLATFORMS:-linux/arm64,linux/amd64}"
NAME_PREFIX="${NAME_PREFIX:-bmc-}"
SERVICES_ONLY="${SERVICES_ONLY:-0}"
PLATFORM_ONLY="${PLATFORM_ONLY:-0}"
SKIP_FRONTEND="${SKIP_FRONTEND:-0}"
DRY_RUN="${DRY_RUN:-0}"
PLATFORM_IMAGE="${NAME_PREFIX}platform"

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

[ -n "$REGISTRIES" ] || die "请用 REGISTRIES 指定目标仓库（逗号分隔，前缀含结尾斜杠）"

# --------------------------------------------------------------------------- #
# 凭据：按仓库主机名匹配对应的账号/令牌环境变量；没有就跳过该仓库
# --------------------------------------------------------------------------- #
# 返回 0 = 已登录（或无需登录），1 = 无凭据应跳过
login_registry() {
  local registry="$1"
  local host="${registry%%/*}"
  local user="" token=""

  case "$host" in
    docker.io|index.docker.io|registry-1.docker.io)
      user="${DOCKERHUB_USER:-}"; token="${DOCKERHUB_TOKEN:-}" ;;
    ghcr.io)
      user="${GHCR_USER:-}"; token="${GHCR_TOKEN:-}" ;;
    *)
      # 其它（含内网 GitLab 与自建仓库）统一用 GITLAB_*/REGISTRY_* 两组兜底
      user="${GITLAB_USER:-${REGISTRY_USER:-}}"; token="${GITLAB_TOKEN:-${REGISTRY_TOKEN:-}}" ;;
  esac

  if [ -z "$user" ] || [ -z "$token" ]; then
    info "跳过 ${host}：没有对应凭据（docker.io→DOCKERHUB_*，ghcr.io→GHCR_*，其它→GITLAB_*/REGISTRY_*）"
    return 1
  fi
  if [ "$DRY_RUN" = "1" ]; then
    info "[dry-run] docker login ${host} -u ${user}"
    return 0
  fi
  if printf '%s' "$token" | docker login "$host" -u "$user" --password-stdin >/dev/null 2>&1; then
    info "已登录 ${host}（用户 ${user}）"
    return 0
  fi
  echo "WARN: ${host} 登录失败，跳过该仓库" >&2
  return 1
}

# --------------------------------------------------------------------------- #
# 1) 前端产物：平台镜像要 COPY platform/frontend/dist
# --------------------------------------------------------------------------- #
build_frontend() {
  [ "$SKIP_FRONTEND" = "1" ] && { info "跳过前端构建（SKIP_FRONTEND=1）"; return 0; }
  if [ -f platform/frontend/dist/index.html ] && [ "${FORCE_FRONTEND:-0}" != "1" ]; then
    info "前端产物已存在（platform/frontend/dist），跳过构建（FORCE_FRONTEND=1 可强制重建）"
    return 0
  fi
  command -v pnpm >/dev/null 2>&1 || die "缺少 pnpm，无法构建前端产物（或用 SKIP_FRONTEND=1 跳过）"
  info "构建前端产物（pnpm build）"
  (cd platform/frontend && pnpm install --frozen-lockfile >/dev/null 2>&1 || pnpm install >/dev/null; pnpm build)
}

# --------------------------------------------------------------------------- #
# 2) 平台镜像：用 Dockerfile.platform（权威定义）构建多架构并推送
# --------------------------------------------------------------------------- #
publish_platform() {
  local registry="$1" image="${1}${PLATFORM_IMAGE}:${TAG}"
  local cmd=(docker buildx build . --file Dockerfile.platform --tag "$image" --platform "$PLATFORMS" --push)
  info "${cmd[*]}"
  [ "$DRY_RUN" = "1" ] && return 0
  "${cmd[@]}"
}

# --------------------------------------------------------------------------- #
# 主流程
# --------------------------------------------------------------------------- #
build_frontend

IFS=',' read -r -a REG_LIST <<<"$REGISTRIES"
for registry in "${REG_LIST[@]}"; do
  [ -n "$registry" ] || continue
  # build.sh 的 REGISTRY 不带结尾斜杠
  reg_trimmed="${registry%/}"
  info "==================== 仓库：${reg_trimmed} ===================="
  login_registry "$registry" || continue

  if [ "$PLATFORM_ONLY" != "1" ]; then
    info "构建并推送 11 个服务镜像（${PLATFORMS}）"
    if [ "$DRY_RUN" = "1" ]; then
      info "[dry-run] REGISTRY=${reg_trimmed} TAG=${TAG} NAME_PREFIX=${NAME_PREFIX} PUSH=1 bash scripts/build.sh"
    else
      REGISTRY="$reg_trimmed" TAG="$TAG" NAME_PREFIX="$NAME_PREFIX" \
        PLATFORMS="$PLATFORMS" PUSH=1 BUILD_ARGS="${BUILD_ARGS:-}" \
        bash scripts/build.sh || die "服务镜像推送到 ${reg_trimmed} 失败"
    fi
  fi

  if [ "$SERVICES_ONLY" != "1" ]; then
    publish_platform "$registry"
  fi
done

info "发布完成：TAG=${TAG} PLATFORMS=${PLATFORMS} 仓库=${REGISTRIES}"
