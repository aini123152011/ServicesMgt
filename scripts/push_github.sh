#!/usr/bin/env bash
# 把当前仓库推送到 GitHub。
#
# 为什么需要脚本：
# 1) Windows 的 schannel 传大包会中途断开（`schannel: server closed abruptly`），
#    **修法是加大 http.postBuffer**（实测 500MB 后直连一次成功），比绕代理简单得多；
# 2) 真连不上时才退回跳板机 SOCKS 转发（网络受限环境用）。
#
# 用法：
#   bash scripts/push_github.sh                 # 推 main 与当前分支
#   bash scripts/push_github.sh main            # 只推指定分支
#   USE_JUMP=1 bash scripts/push_github.sh      # 强制走跳板机
#
# 凭据由 git 凭据管理器提供（不落盘、不进仓库）。可覆盖：JUMP_HOST、SOCKS_PORT、GITHUB_REMOTE。
set -euo pipefail

# 跳板机地址不写死（内网拓扑不进仓库）：只在需要回退时要求显式提供
JUMP_HOST="${JUMP_HOST:-}"
SOCKS_PORT="${SOCKS_PORT:-1080}"
REMOTE="${GITHUB_REMOTE:-github}"

info() { echo "==> $*"; }

git remote get-url "$REMOTE" >/dev/null 2>&1 \
  || { echo "ERROR: 远端 $REMOTE 不存在，先执行 git remote add $REMOTE <GitHub 仓库地址>" >&2; exit 1; }

USING_JUMP=0
setup_jump_proxy() {
  [ -n "${JUMP_HOST}" ] || { echo "ERROR: 需要回退到跳板机，但未设置 JUMP_HOST" >&2; exit 1; }
  info "走跳板机 SOCKS 转发（${JUMP_HOST}:${SOCKS_PORT}）"
  pkill -f "ssh .*-D ${SOCKS_PORT} " 2>/dev/null || true
  sleep 1
  nohup ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    -N -D "${SOCKS_PORT}" "${JUMP_HOST}" >/tmp/push-github-socks.log 2>&1 &
  for _ in $(seq 1 20); do
    sleep 0.5
    (echo >"/dev/tcp/127.0.0.1/${SOCKS_PORT}") 2>/dev/null && break
  done
  (echo >"/dev/tcp/127.0.0.1/${SOCKS_PORT}") 2>/dev/null \
    || { echo "ERROR: SOCKS 转发未建立，见 /tmp/push-github-socks.log" >&2; exit 1; }
  git config "remote.$REMOTE.proxy" "socks5h://127.0.0.1:${SOCKS_PORT}"
  USING_JUMP=1
}

# 加大 postBuffer：Windows schannel 对大 POST 体的经典问题（实测不加就 `server closed abruptly`）
git config http.postBuffer 524288000
git config --unset "remote.$REMOTE.proxy" 2>/dev/null || true
if [ "${USE_JUMP:-0}" = "1" ]; then
  setup_jump_proxy
elif git ls-remote --exit-code --heads "$REMOTE" >/dev/null 2>&1; then
  info "直连 GitHub 可用（推送失败会自动回退跳板机）"
else
  setup_jump_proxy
fi

push_ref() {
  if git push "$REMOTE" "$1"; then
    return 0
  fi
  [ "${USING_JUMP}" = "1" ] && return 1
  info "直连推送失败，改走跳板机重试"
  setup_jump_proxy
  git push "$REMOTE" "$1"
}

BRANCHES=("$@")
[ "${#BRANCHES[@]}" -eq 0 ] && BRANCHES=(main "$(git rev-parse --abbrev-ref HEAD)")

for b in "${BRANCHES[@]}"; do
  if git show-ref --verify --quiet "refs/heads/${b}"; then
    info "推送 ${b}"
    push_ref "refs/heads/${b}:refs/heads/${b}"
  else
    info "本地没有分支 ${b}，跳过"
  fi
done

info "完成。远端分支："
git ls-remote --heads "$REMOTE"
