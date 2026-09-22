#!/usr/bin/env bash
# 把本仓库推送到 GitHub 镜像（默认推送「脱敏后的镜像历史」）。
#
# 网络路径：**先试直连**，直连推送失败（实测大包传输会被中途断开：schannel server closed
# abruptly / Empty reply from server）就自动切到跳板机的 SSH 动态转发（SOCKS5）重试一次。
# 代理只配在 github 这个 remote 上（remote.github.proxy），内网 origin 不受影响。
# USE_JUMP=1 可强制走跳板机。
#
# 为什么默认脱敏：GitHub 是公开仓库，而内网仓库的历史里有真实姓名与（历史提交里的）口令。
# 镜像历史按「同样的树、中性作者身份」重建，因此**镜像里的代码与内网逐字节一致，但历史
# 不带真实身份**。内网仓库保持原样（main 是受保护分支，也无法重写）。
#
# 用法（凭据只走环境变量，不写进 git config、不落盘）：
#   GITHUB_USER='<账号或 PAT>' GITHUB_PASS='<口令或 PAT>' bash scripts/push_github.sh
#   GITHUB_USER='...' GITHUB_PASS='...' bash scripts/push_github.sh feat/bmc-services-platform
#   GITHUB_USER='...' GITHUB_PASS='...' bash scripts/push_github.sh --push-main
#   GITHUB_USER='...' GITHUB_PASS='...' bash scripts/push_github.sh --raw <branch>   # 推原始历史（真名，慎用）
#
# 不带分支参数时：推 main 与当前分支。镜像的 main 是**脱敏历史自己的根提交**（不是内网 main），
# 只在镜像里不存在时推送；要更新用 --push-main。
#
# 可覆盖的环境变量：JUMP_HOST、SOCKS_PORT、GITHUB_REMOTE、MIRROR_NAME、MIRROR_EMAIL。
set -euo pipefail

JUMP_HOST="${JUMP_HOST:-root@<目标机地址>}"
SOCKS_PORT="${SOCKS_PORT:-1080}"
REMOTE="${GITHUB_REMOTE:-github}"
# 镜像历史的作者身份：与 GitHub 账号一致，不带真实姓名
MIRROR_NAME="${MIRROR_NAME:-aini123152011}"
MIRROR_EMAIL="${MIRROR_EMAIL:-aini123152008@qq.com}"
# 镜像分支在本地的前缀（只存在于本仓库，不推内网）
MIRROR_REF_PREFIX="github-mirror"

die() { echo "ERROR: $*" >&2; exit 1; }
info() { echo "==> $*"; }

RAW=0
PUSH_MAIN=0
BRANCHES=()
for arg in "$@"; do
  case "$arg" in
    --raw) RAW=1 ;;
    --push-main) PUSH_MAIN=1 ;;
    -*) die "未知参数：$arg" ;;
    *) BRANCHES+=("$arg") ;;
  esac
done

: "${GITHUB_USER:?请通过环境变量提供 GITHUB_USER（GitHub 账号或 PAT）}"
: "${GITHUB_PASS:?请通过环境变量提供 GITHUB_PASS（口令或 PAT）}"
git rev-parse --git-dir >/dev/null 2>&1 || die "当前目录不是 git 仓库"
git remote get-url "$REMOTE" >/dev/null 2>&1 \
  || die "远端 $REMOTE 不存在，先执行：git remote add $REMOTE <GitHub 仓库地址>"

# --------------------------------------------------------------------------- #
# 一次性 askpass：口令只存在于环境变量与临时文件里，脚本退出即删除
# --------------------------------------------------------------------------- #
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

git_remote() {
  GIT_ASKPASS="${ASKPASS}" GIT_ASKPASS_USER="${GITHUB_USER}" GIT_ASKPASS_PASS="${GITHUB_PASS}" \
    git "$@"
}

# --------------------------------------------------------------------------- #
# 网络路径：直连优先，推送失败自动回退跳板机
# --------------------------------------------------------------------------- #
USING_JUMP=0

setup_jump_proxy() {
  info "走跳板机 SOCKS 转发（${JUMP_HOST}:${SOCKS_PORT}）"
  # 清掉可能残留的僵尸隧道（端口还在听但连接已被对端关闭，实测会让 push 报
  # "Failed to receive SOCKS response, proxy closed connection"）
  pkill -f "ssh .*-D ${SOCKS_PORT} " 2>/dev/null || true
  sleep 1
  nohup ssh -o BatchMode=yes -o ExitOnForwardFailure=yes -o ServerAliveInterval=30 \
    -N -D "${SOCKS_PORT}" "${JUMP_HOST}" >/tmp/push-github-socks.log 2>&1 &
  for _ in $(seq 1 20); do
    sleep 0.5
    (echo >"/dev/tcp/127.0.0.1/${SOCKS_PORT}") 2>/dev/null && break
  done
  (echo >"/dev/tcp/127.0.0.1/${SOCKS_PORT}") 2>/dev/null \
    || die "SOCKS 转发未建立，见 /tmp/push-github-socks.log"
  git config "remote.$REMOTE.proxy" "socks5h://127.0.0.1:${SOCKS_PORT}"
  USING_JUMP=1
}

git config --unset "remote.$REMOTE.proxy" 2>/dev/null || true
if [ "${USE_JUMP:-0}" = "1" ]; then
  setup_jump_proxy
elif git ls-remote --exit-code --heads "$REMOTE" >/dev/null 2>&1; then
  info "直连 GitHub 可用（推送失败会自动回退跳板机）"
else
  setup_jump_proxy
fi

# 推送一个 refspec；直连失败（大包被中途断开）就切跳板机重试一次
push_ref() {
  if git_remote push --force "$REMOTE" "$1"; then
    return 0
  fi
  if [ "${USING_JUMP}" = "1" ]; then
    return 1
  fi
  info "直连推送失败，改走跳板机重试"
  setup_jump_proxy
  git_remote push --force "$REMOTE" "$1"
}

# --------------------------------------------------------------------------- #
# 镜像历史：按「本地提交的树 + 中性作者身份」追加到镜像分支
# --------------------------------------------------------------------------- #
# 与 github-mirror 比对出「还没进镜像的本地提交」，逐个用 commit-tree 重建：
# 树完全取自本地提交（内容逐字节一致），作者/提交者换成镜像身份。
# 不重写整段历史，因此每次推送只处理新增提交，秒级完成。
refresh_mirror() {
  local src_branch="$1"
  local mirror_ref="refs/heads/${MIRROR_REF_PREFIX}-${src_branch//\//-}"

  # 从远端取回镜像当前状态（镜像历史只存在于远端，本地不长期保存）
  git_remote fetch -q "$REMOTE" \
    "refs/heads/${src_branch}:${mirror_ref}" 2>/dev/null || true

  if ! git show-ref --verify --quiet "$mirror_ref"; then
    die "镜像分支 ${mirror_ref} 不存在（首次建立需先做一次整段脱敏，见 deploy 规范）"
  fi

  # 上一次镜像的树必须能在本地找到对应提交，否则说明本地历史被重写过，需重新整段脱敏
  local mirror_tip_tree local_match
  mirror_tip_tree="$(git rev-parse "${mirror_ref}^{tree}")"
  local_match="$(git log --format='%H %T' "refs/heads/${src_branch}" \
    | awk -v t="$mirror_tip_tree" '$2==t {print $1; exit}')"
  [ -n "$local_match" ] || die "镜像与本地历史对不上（本地可能 rebase/重写过），需重新整段脱敏"

  local pending count=0
  pending="$(git rev-list --reverse "${local_match}..refs/heads/${src_branch}")"
  if [ -z "$pending" ]; then
    info "镜像已是最新（${mirror_ref}）"
    return 0
  fi

  local commit tree message date new_commit
  while read -r commit; do
    [ -n "$commit" ] || continue
    tree="$(git rev-parse "${commit}^{tree}")"
    message="$(git log -1 --format=%B "$commit")"
    date="$(git log -1 --format=%aI "$commit")"
    new_commit="$(GIT_AUTHOR_NAME="${MIRROR_NAME}" GIT_AUTHOR_EMAIL="${MIRROR_EMAIL}" \
      GIT_AUTHOR_DATE="${date}" \
      GIT_COMMITTER_NAME="${MIRROR_NAME}" GIT_COMMITTER_EMAIL="${MIRROR_EMAIL}" \
      GIT_COMMITTER_DATE="${date}" \
      git commit-tree "$tree" -p "$(git rev-parse "$mirror_ref")" -m "$message")"
    git update-ref "$mirror_ref" "$new_commit"
    count=$((count + 1))
  done <<<"$pending"
  info "镜像新增 ${count} 个提交（作者身份 ${MIRROR_NAME} <${MIRROR_EMAIL}>）"
}

# --------------------------------------------------------------------------- #
# 推送
# --------------------------------------------------------------------------- #
[ "${#BRANCHES[@]}" -eq 0 ] && BRANCHES=(main "$(git rev-parse --abbrev-ref HEAD)")

for branch in "${BRANCHES[@]}"; do
  # main 特殊处理：镜像的 main 是**脱敏历史自己的根提交**，与内网 main 不是同一个对象。
  # 绝不要把 refs/remotes/origin/main 推到 GitHub——那是内网原始历史（真名作者）。
  if [ "$branch" = "main" ] && [ "$RAW" -eq 0 ]; then
    if [ "$PUSH_MAIN" -eq 0 ] \
       && git_remote ls-remote --exit-code --heads "$REMOTE" refs/heads/main >/dev/null 2>&1; then
      info "跳过 main（镜像已有；要更新镜像的 main 用 --push-main）"
      continue
    fi
    base_branch="$(git rev-parse --abbrev-ref HEAD)"
    base_mirror="refs/heads/${MIRROR_REF_PREFIX}-${base_branch//\//-}"
    if ! git show-ref --verify --quiet "$base_mirror"; then
      refresh_mirror "$base_branch"
    fi
    root_commit="$(git rev-list --max-parents=0 "$base_mirror" | tail -1)"
    info "推送镜像 main（脱敏根提交 ${root_commit:0:7}）"
    push_ref "${root_commit}:refs/heads/main"
    continue
  fi

  if git show-ref --verify --quiet "refs/heads/${branch}"; then
    src_ref="refs/heads/${branch}"
  elif git show-ref --verify --quiet "refs/remotes/origin/${branch}"; then
    src_ref="refs/remotes/origin/${branch}"
  else
    die "找不到分支 ${branch}（本地与 origin 都没有）"
  fi

  if [ "$RAW" -eq 1 ]; then
    info "原样推送 ${src_ref} -> ${REMOTE}/refs/heads/${branch}（历史含真实姓名）"
    push_ref "${src_ref}:refs/heads/${branch}"
  else
    refresh_mirror "$branch"
    mirror_ref="refs/heads/${MIRROR_REF_PREFIX}-${branch//\//-}"
    info "推送脱敏镜像 ${mirror_ref} -> ${REMOTE}/refs/heads/${branch}"
    push_ref "${mirror_ref}:refs/heads/${branch}"
  fi
done

info "完成。远端分支："
git_remote ls-remote --heads "$REMOTE"
