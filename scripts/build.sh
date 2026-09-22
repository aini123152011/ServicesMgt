#!/usr/bin/env bash
# services 镜像统一构建入口 —— 唯一事实来源，GitHub Actions 与 GitLab CI 均调用本脚本
#
# 用法:
#   scripts/build.sh                          # 本地构建全部服务（当前平台，--load 入本地镜像）
#   scripts/build.sh chrony nginx             # 只构建指定服务
#   scripts/build.sh --check                  # 多架构(arm64+amd64)构建校验，不产出镜像
#   scripts/build.sh --list                   # 列出全部可构建服务
#   PLATFORMS=... REGISTRY=... PUSH=1 scripts/build.sh   # 多架构构建并推送
#   BUILD_ARGS="APT_MIRROR=mirrors.aliyun.com" scripts/build.sh   # 国内网络换源加速
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SERVICES_DIR="${ROOT_DIR}/services"

# 可用环境变量覆盖的参数
PLATFORMS="${PLATFORMS:-}"   # buildx 目标架构；留空 = 当前平台
REGISTRY="${REGISTRY:-}"     # 镜像仓库前缀（如 ghcr.io/org）；留空 = 仅本地 tag
TAG="${TAG:-latest}"         # 镜像 tag
PUSH="${PUSH:-0}"            # 1 = 构建后推送（需同时设置 REGISTRY）
# 额外 build-arg，空格分隔（如 "APT_MIRROR=mirrors.aliyun.com"）。
# 留空 = 用 Dockerfile 里的默认值，与 CI 行为完全一致；国内网络本地构建时可用它换源加速。
BUILD_ARGS="${BUILD_ARGS:-}"

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

usage() {
  sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
  exit 0
}

# 列出所有含 Dockerfile 的服务目录名（每行一个）；目录不存在则返回空
list_services() {
  [ -d "$SERVICES_DIR" ] || return 0
  local d
  for d in "$SERVICES_DIR"/*/; do
    [ -f "${d}Dockerfile" ] && basename "$d"
  done
}

CHECK=0
SERVICES=()
while [ $# -gt 0 ]; do
  case "$1" in
    --check) CHECK=1 ;;
    --list) list_services; exit 0 ;;
    -h|--help) usage ;;
    -*) die "未知参数: $1" ;;
    *) SERVICES+=("$1") ;;
  esac
  shift
done

# 未指定服务则构建全部
if [ ${#SERVICES[@]} -eq 0 ]; then
  mapfile -t SERVICES < <(list_services)
fi

if [ "$PUSH" = "1" ] && [ -z "$REGISTRY" ]; then
  die "PUSH=1 需要同时设置 REGISTRY"
fi

for svc in "${SERVICES[@]}"; do
  context="${SERVICES_DIR}/${svc}"
  [ -f "${context}/Dockerfile" ] || die "服务 ${svc} 缺少 Dockerfile: ${context}"

  image="${svc}:${TAG}"
  [ -n "$REGISTRY" ] && image="${REGISTRY}/${svc}:${TAG}"

  cmd=(docker buildx build "$context" --tag "$image")
  [ -n "$PLATFORMS" ] && cmd+=(--platform "$PLATFORMS")
  for arg in ${BUILD_ARGS}; do
    cmd+=(--build-arg "$arg")
  done

  if [ "$PUSH" = "1" ]; then
    cmd+=(--push)
  elif [ "$CHECK" = "1" ]; then
    # 校验模式：两个平台都真实构建但只进缓存，不产出/不推送（规避多架构 --load 限制）
    cmd+=(--output type=cacheonly)
  elif [ -n "$PLATFORMS" ]; then
    die "本地模式不支持多 PLATFORMS（--load 仅单平台）。多架构请用 --check 或 PUSH=1"
  else
    cmd+=(--load)
  fi

  info "${cmd[*]}"
  "${cmd[@]}"
done

info "完成: ${SERVICES[*]:-（无服务）}"
