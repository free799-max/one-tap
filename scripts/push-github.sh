#!/usr/bin/env bash
# 将当前提交的完整项目作为源码快照推送到 GitHub，排除 GitLab CI 配置。
# GitHub 只保存可公开的源码；完整历史与版本标签仍以 GitLab 为准。
set -Eeuo pipefail

SOURCE_REF=${1:-HEAD}
REMOTE=${GITHUB_REMOTE:-origin}
BRANCH=${GITHUB_BRANCH:-main}
export GIT_TERMINAL_PROMPT=${GIT_TERMINAL_PROMPT:-0}

# 只从 GitHub 快照中排除的文件，可按需追加
EXCLUDED=(.gitlab-ci.yml)

die() {
  echo "错误：$*" >&2
  exit 1
}

[ "$#" -le 1 ] || die "用法：scripts/push-github.sh [提交]"
ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"
git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前目录不是 Git 仓库"
git remote get-url "$REMOTE" >/dev/null 2>&1 || die "远端不存在：$REMOTE"
git rev-parse -q --verify "$SOURCE_REF^{commit}" >/dev/null || die "提交不存在：$SOURCE_REF"

TMP_INDEX=$(mktemp)
trap 'rm -f "$TMP_INDEX"' EXIT
rm -f "$TMP_INDEX"

# 用独立索引取出源码树，再移除不公开的文件
GIT_INDEX_FILE="$TMP_INDEX" git read-tree "$SOURCE_REF^{tree}"
GIT_INDEX_FILE="$TMP_INDEX" git rm --cached --ignore-unmatch "${EXCLUDED[@]}" >/dev/null
TREE=$(GIT_INDEX_FILE="$TMP_INDEX" git write-tree)

if ! REMOTE_HEAD=$(git ls-remote "$REMOTE" "refs/heads/$BRANCH" | cut -f1); then
  die "无法读取 $REMOTE/$BRANCH，请检查仓库地址和凭据"
fi

if [ -n "$REMOTE_HEAD" ]; then
  git fetch --quiet "$REMOTE" "$BRANCH" || die "无法拉取 $REMOTE/$BRANCH"
  REMOTE_HEAD=$(git rev-parse FETCH_HEAD)
  if [ "$(git rev-parse "$REMOTE_HEAD^{tree}")" = "$TREE" ]; then
    echo "源码没有变化，$REMOTE/$BRANCH 已是最新"
    exit 0
  fi
fi

SOURCE_SHA=$(git rev-parse --short "$SOURCE_REF")
if VERSION=$(git describe --tags --exact-match "$SOURCE_REF" 2>/dev/null); then
  MESSAGE="chore(release): $VERSION"
else
  MESSAGE="chore: sync OneTap source ($SOURCE_SHA)"
fi

if [ -n "$REMOTE_HEAD" ]; then
  COMMIT=$(git commit-tree "$TREE" -p "$REMOTE_HEAD" -m "$MESSAGE")
else
  COMMIT=$(git commit-tree "$TREE" -m "$MESSAGE")
fi

git push "$REMOTE" "$COMMIT:refs/heads/$BRANCH"
echo "已推送源码快照：$REMOTE/$BRANCH ($COMMIT)"
echo "已排除：${EXCLUDED[*]}"
