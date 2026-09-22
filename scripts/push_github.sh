#!/usr/bin/env bash
# 经跳板机把本仓库推送到 GitHub 镜像。
#
# 为什么需要跳板机：本机到 github.com 不通（实测握手要 13 秒以上且不稳定），
# 而目标机 <目标机地址> 可以直连 GitHub。做法是用 SSH 动态转发（SOCKS5）把 git 的
# HTTPS 流量借道跳板机——代理只配在 github 这个 remote 上（remote.github.proxy），
# 内网 origin（<内网 GitLab 主机>）照常直连，不受影响。
#
# 用法（凭据只走环境变量，不写进 git config、不落盘）：
#   GITHUB_USER='<账号或 PAT>' GITHUB_PASS='<口令或 PAT>' bash scripts/push_github.sh
#   GITHUB_USER='...' GITHUB_PASS='...' bash scripts/push_github.sh main feat/xxx
#
# 不带分支参数时：推 main 与当前分支。分支在本地不存在但 origin/<分支> 存在时，
# 推远端跟踪引用（仓库只在工作区 checkout 了特性分支的情形）。
#
# 可覆盖的环境变量：JUMP_HOST（默认 root@<目标机地址>）、SOCKS_PORT（默认 1080）、
# GITHUB_REMOTE（默认 github）、GITHUB_URL（默认远端已配置的地址）。
set -euo pipefail

JUMP_HOST="${JUMP_HOST:-root@<目标机地址>}"
SOCKS_PORT="${SOCKS_PORT:-1080}"
REMOTE="${GITHUB_REMOTE:-github}"
DEFAULT_BRANCHES=(main "$(git rev-parse --abbrev-ref HEAD)")

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

: "${GITHUB_USER:?请通过环境变量提供 GITHUB_USER（GitHub 账号或 PAT）}"
: "${GITHUB_PASS:?请通过环境变量提供 GITHUB_PASS（口令或 PAT）}"

git rev-parse --git-dir >/dev/null 2>&1 || die "当前目录不是 git 仓库"

# 远端与代理：代理指向本地 SOCKS，需与下面建立的转发端口一致
if ! git remote get-url "$REMOTE" >/dev/null 2>&1; then
  die "远端 $REMOTE 不存在，先执行：git remote add $REMOTE <GitHub 仓库地址>"
fi
git config "remote.$REMOTE.proxy" "socks5h://127.0.0.1:${SOCKS_PORT}"
info "远端 $REMOTE -> $(git remote get-url "$REMOTE")（代理 socks5h://127.0.0.1:${SOCKS_PORT}）"

# 跳板机上的 SOCKS5 转发：端口已通就复用，没通就现起一个后台隧道
if (echo >"/dev/tcp/127.0.0.1/${SOCKS_PORT}") 2>/dev/null; then
  info "复用已有 SOCKS 转发（127.0.0.1:${SOCKS_PORT}）"
else
  info "建立到 ${JUMP_HOST} 的 SOCKS 转发（127.0.0.1:${SOCKS_PORT}）"
  nohup ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    -N -D "${SOCKS_PORT}" "${JUMP_HOST}" >/tmp/push-github-socks.log 2>&1 &
  for _ in $(seq 1 20); do
    sleep 0.5
    (echo >"/dev/tcp/127.0.0.1/${SOCKS_PORT}") 2>/dev/null && break
  done
  (echo >"/dev/tcp/127.0.0.1/${SOCKS_PORT}") 2>/dev/null \
    || die "SOCKS 转发未建立，见 /tmp/push-github-socks.log"
fi

# 一次性 askpass：口令只存在于环境变量与临时文件里，推送后立刻删除
ASKPASS="$(mktemp)"
trap 'rm -f "${ASKPASS}"' EXIT
cat >"${ASKPASS}" <<'SH'
#!/bin/sh
case "$1" in
  Username*) printf '%s' "${GIT_ASKPASS_USER}" ;;
  *) printf '%s' "${GIT_ASKPASS_PASS}" ;;
esac
SH
chmod 700 "${ASKPASS}"

BRANCHES=("$@")
[ "${#BRANCHES[@]}" -eq 0 ] && BRANCHES=("${DEFAULT_BRANCHES[@]}")

for branch in "${BRANCHES[@]}"; do
  if git show-ref --verify --quiet "refs/heads/${branch}"; then
    refspec="refs/heads/${branch}:refs/heads/${branch}"
  elif git show-ref --verify --quiet "refs/remotes/origin/${branch}"; then
    refspec="refs/remotes/origin/${branch}:refs/heads/${branch}"
  else
    die "找不到分支 ${branch}（本地与 origin 都没有）"
  fi
  info "推送 ${refspec}"
  GIT_ASKPASS="${ASKPASS}" GIT_ASKPASS_USER="${GITHUB_USER}" GIT_ASKPASS_PASS="${GITHUB_PASS}" \
    git push "$REMOTE" "$refspec"
done

info "完成。远端分支："
GIT_ASKPASS="${ASKPASS}" GIT_ASKPASS_USER="${GITHUB_USER}" GIT_ASKPASS_PASS="${GITHUB_PASS}" \
  git ls-remote --heads "$REMOTE"
