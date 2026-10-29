#!/usr/bin/env bash
# 将指定提交中的 frontend/ 发布为独立快照，不带其他目录或完整项目历史。
set -Eeuo pipefail

SOURCE_REF=${1:-HEAD}
REMOTE=${FRONTEND_REMOTE:-origin}
BRANCH=${FRONTEND_BRANCH:-main}
export GIT_TERMINAL_PROMPT=${GIT_TERMINAL_PROMPT:-0}

die() {
  echo "错误：$*" >&2
  exit 1
}

[ "$#" -le 1 ] || die "用法：scripts/push-frontend.sh [提交]"
ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前目录不是 Git 仓库"
git remote get-url "$REMOTE" >/dev/null 2>&1 || die "远端不存在：$REMOTE"

TREE=$(git rev-parse "$SOURCE_REF:frontend" 2>/dev/null) || die "$SOURCE_REF 中不存在 frontend/ 目录"

if ! REMOTE_HEAD=$(git ls-remote "$REMOTE" "refs/heads/$BRANCH" | cut -f1); then
  die "无法读取 $REMOTE/$BRANCH，请检查仓库地址和凭据"
fi

if [ -n "$REMOTE_HEAD" ]; then
  git fetch --quiet "$REMOTE" "$BRANCH" || die "无法拉取 $REMOTE/$BRANCH"
  REMOTE_HEAD=$(git rev-parse FETCH_HEAD)
  if [ "$(git rev-parse "$REMOTE_HEAD^{tree}")" = "$TREE" ]; then
    echo "前端没有变化，$REMOTE/$BRANCH 已是最新"
    exit 0
  fi
fi

MESSAGE=${FRONTEND_COMMIT_MESSAGE:-}
if [ -z "$MESSAGE" ]; then
  if VERSION=$(git describe --tags --exact-match "$SOURCE_REF" 2>/dev/null); then
    MESSAGE="chore(release): $VERSION"
  else
    MESSAGE="chore: sync OneTap frontend"
  fi
fi

if [ -n "$REMOTE_HEAD" ]; then
  COMMIT=$(git commit-tree "$TREE" -p "$REMOTE_HEAD" -m "$MESSAGE")
else
  COMMIT=$(git commit-tree "$TREE" -m "$MESSAGE")
fi

git push "$REMOTE" "$COMMIT:refs/heads/$BRANCH"
echo "已推送前端快照：$REMOTE/$BRANCH ($COMMIT)"
