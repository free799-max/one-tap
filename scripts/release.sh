#!/usr/bin/env bash
# 发布新版本：按提交记录递增语义化版本，写 CHANGELOG、打 v* 标签并推送。
# GitLab 接收完整项目与标签；GitHub 只接收 frontend/ 独立快照。
# 推送标签会触发 GitLab CI（.gitlab-ci.yml 只响应 tag），构建并推送 Harbor 镜像。
set -euo pipefail

ROOT_DIR=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")/.." && pwd)
cd "$ROOT_DIR"

BRANCH=${RELEASE_BRANCH:-main}
REMOTES=${RELEASE_REMOTES:-"origin gitlab"}
# 非交互环境（如 CI、AI 助手）下直接失败，避免卡在密码输入；需要手动输入密码时设为 1
export GIT_TERMINAL_PROMPT=${GIT_TERMINAL_PROMPT:-0}

BUMP=""
DRY_RUN=0
PUSH=1

usage() {
  cat <<'EOF'
用法: scripts/release.sh [major|minor|patch] [--dry-run] [--no-push]

  major|minor|patch  指定版本递增方式；省略时按提交信息自动判断
  --dry-run          只打印发布计划，不写文件、不提交、不打标签、不推送
  --no-push          本地生成提交与标签，跳过推送

环境变量:
  RELEASE_BRANCH     发布分支，默认 main
  RELEASE_REMOTES    推送的远端列表，默认 "origin gitlab"
  GIT_TERMINAL_PROMPT 设为 1 可交互输入密码（默认 0，直接失败）
EOF
}

die() {
  echo "错误：$*" >&2
  exit 1
}

for arg in "$@"; do
  case "$arg" in
    major|minor|patch) BUMP="$arg" ;;
    --dry-run) DRY_RUN=1 ;;
    --no-push) PUSH=0 ;;
    -h|--help) usage; exit 0 ;;
    *) echo "未知参数：$arg" >&2; usage >&2; exit 2 ;;
  esac
done

git rev-parse --is-inside-work-tree >/dev/null 2>&1 || die "当前目录不是 Git 仓库"

CURRENT_BRANCH=$(git rev-parse --abbrev-ref HEAD)
[ "$CURRENT_BRANCH" = "$BRANCH" ] || die "请在 $BRANCH 分支发布，当前为 $CURRENT_BRANCH"
[ -z "$(git status --porcelain --untracked-files=no)" ] || die "工作区有未提交的改动，请先提交"
git config user.name >/dev/null 2>&1 && git config user.email >/dev/null 2>&1 \
  || die "未配置 git user.name / user.email"

# 只从完整项目远端拉取标签；GitHub 前端仓库不作为版本来源
for remote in $REMOTES; do
  if [ "$remote" != "origin" ] && git remote get-url "$remote" >/dev/null 2>&1; then
    git fetch --tags --quiet "$remote" >/dev/null 2>&1 || true
  fi
done

LAST_TAG=$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)
if [ -n "$LAST_TAG" ] && ! printf '%s' "$LAST_TAG" | grep -Eq '^v[0-9]+\.[0-9]+\.[0-9]+(-[0-9A-Za-z.-]+)?$'; then
  die "最近标签 $LAST_TAG 不是 v主.次.修订 格式，请手动处理"
fi

if [ -n "$LAST_TAG" ]; then
  RANGE="$LAST_TAG..HEAD"
  CORE=${LAST_TAG#v}
  CORE=${CORE%%-*}
  IFS=. read -r MAJOR MINOR PATCH <<< "$CORE"
else
  RANGE="HEAD"
  MAJOR=0
  MINOR=0
  PATCH=0
fi

TMP_DIR=$(mktemp -d)
trap 'rm -rf "$TMP_DIR"' EXIT

SUBJECTS="$TMP_DIR/subjects"
git log "$RANGE" --no-merges --pretty=tformat:'%s' > "$SUBJECTS" || true
[ -s "$SUBJECTS" ] || die "距上一个版本没有新提交，无法发布"

BREAKING=0
if grep -Eq '^[a-zA-Z]+(\([^)]*\))?!:' "$SUBJECTS"; then
  BREAKING=1
fi
if git log "$RANGE" --no-merges --pretty=tformat:'%B' | grep -Eq 'BREAKING[ -]CHANGE'; then
  BREAKING=1
fi

if [ -z "$BUMP" ]; then
  if [ -z "$LAST_TAG" ]; then
    BUMP=minor
  elif [ "$BREAKING" -eq 1 ]; then
    BUMP=major
  elif grep -Eq '^feat(\([^)]*\))?!?:' "$SUBJECTS"; then
    BUMP=minor
  else
    BUMP=patch
  fi
fi

case "$BUMP" in
  major) NEW_TAG="v$((MAJOR + 1)).0.0" ;;
  minor) NEW_TAG="v${MAJOR}.$((MINOR + 1)).0" ;;
  patch) NEW_TAG="v${MAJOR}.${MINOR}.$((PATCH + 1))" ;;
esac

git rev-parse -q --verify "refs/tags/$NEW_TAG" >/dev/null && die "标签 $NEW_TAG 已存在"

# 按 conventional commit 前缀归类提交，生成发布说明
group() {
  local file=$1
  [ -s "$file" ] || return 0
  sed 's/^/- /' "$file"
  echo
}

NOTES="$TMP_DIR/notes"
: > "$NOTES"
CONVENTIONAL_RE='^([a-zA-Z]+)(\([^)]*\))?(!)?: (.*)$'
while IFS= read -r subject; do
  [ -n "$subject" ] || continue
  if [[ $subject =~ $CONVENTIONAL_RE ]]; then
    type=${BASH_REMATCH[1],,}
    msg=${BASH_REMATCH[4]}
  else
    type=other
    msg=$subject
  fi
  case "$type" in
    feat|fix|perf|refactor|docs|test|style) ;;
    build|ci) type=ci ;;
    *) type=other ;;
  esac
  printf '%s\n' "$msg" >> "$TMP_DIR/$type"
done < "$SUBJECTS"

for pair in "feat:新功能" "fix:修复" "perf:性能" "refactor:重构" "docs:文档" "ci:构建与 CI" "test:测试" "style:样式" "other:其他"; do
  type=${pair%%:*}
  title=${pair#*:}
  file="$TMP_DIR/$type"
  [ -f "$file" ] || continue
  [ -s "$file" ] || continue
  printf '### %s\n\n' "$title" >> "$NOTES"
  group "$file" >> "$NOTES"
done

SECTION="$TMP_DIR/section"
{
  printf '## [%s] - %s\n\n' "$NEW_TAG" "$(date +%F)"
  cat "$NOTES"
} > "$SECTION"

echo "上一个版本：${LAST_TAG:-（无，首次发布）}"
echo "提交数量：$(wc -l < "$SUBJECTS")"
echo "递增方式：$BUMP"
echo "新版本号：$NEW_TAG"
echo
cat "$SECTION"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "（--dry-run）未写文件、未提交、未打标签、未推送"
  exit 0
fi

if [ -f CHANGELOG.md ]; then
  {
    head -n 1 CHANGELOG.md
    echo
    cat "$SECTION"
    tail -n +2 CHANGELOG.md
  } > "$TMP_DIR/CHANGELOG.md"
else
  {
    printf '# 更新日志\n\n'
    cat "$SECTION"
  } > "$TMP_DIR/CHANGELOG.md"
fi
mv "$TMP_DIR/CHANGELOG.md" CHANGELOG.md

git add CHANGELOG.md
git commit -m "chore(release): $NEW_TAG" >/dev/null

{
  printf 'OneTap %s\n\n' "$NEW_TAG"
  cat "$NOTES"
} > "$TMP_DIR/tag-message"
git tag -a "$NEW_TAG" -F "$TMP_DIR/tag-message"

echo "已提交并打标签 $NEW_TAG"

manual_push_command() {
  if [ "$1" = "origin" ]; then
    echo "  scripts/push-frontend.sh HEAD"
  else
    echo "  git push $1 HEAD:$BRANCH && git push $1 $NEW_TAG"
  fi
}

push_remote() {
  if [ "$1" = "origin" ]; then
    scripts/push-frontend.sh HEAD
  else
    git push "$1" "HEAD:$BRANCH" && git push "$1" "$NEW_TAG"
  fi
}

if [ "$PUSH" -eq 0 ]; then
  echo "已按 --no-push 跳过推送；需要时执行："
  for remote in $REMOTES; do
    manual_push_command "$remote"
  done
  exit 0
fi

PUSH_FAILED=0
for remote in $REMOTES; do
  if ! git remote get-url "$remote" >/dev/null 2>&1; then
    echo "跳过不存在的远端：$remote" >&2
    continue
  fi
  if push_remote "$remote"; then
    if [ "$remote" = "origin" ]; then
      echo "已推送：$remote（仅 frontend/，不推标签）"
    else
      echo "已推送：$remote（完整项目与标签）"
    fi
  else
    PUSH_FAILED=1
    echo "推送 $remote 失败。修复凭据后执行：" >&2
    manual_push_command "$remote" >&2
  fi
done

[ "$PUSH_FAILED" -eq 0 ] || exit 1

echo
echo "版本：$NEW_TAG"
echo "镜像：harbor.cacty.top/library/onetap:$NEW_TAG（稳定版另打 latest）"
echo "      ccr.ccs.tencentyun.com/one-tap/one-tap:$NEW_TAG（稳定版另打 latest）"
echo "流水线：https://git.cacty.top/nas/one-tap/-/pipelines"
