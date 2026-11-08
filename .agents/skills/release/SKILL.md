---
name: release
description: 为 OneTap 发布新版本：按提交记录自动递增语义化版本、生成 CHANGELOG、打 v* 标签并推送，触发 GitLab CI 构建推送 Harbor 镜像。用于「发布版本」「打 tag」「升级版本号」「出镜像」「release」等场景。
argument-hint: "[major|minor|patch] [--dry-run]"
---

# OneTap 版本发布

一句话：`scripts/release.sh --dry-run` 看版本号和发布说明 → 确认后 `scripts/release.sh` 完成提交、打标签、推送。只有推送 v* 标签才会触发 GitLab CI，分支推送不构建。

## 版本规则

| 提交内容 | 递增 | 示例 |
|---|---|---|
| `feat!:`、正文含 `BREAKING CHANGE` | major | v1.2.3 → v2.0.0 |
| `feat:` | minor | v1.2.3 → v1.3.0 |
| `fix:` / `perf:` / 其他 | patch | v1.2.3 → v1.2.4 |
| 仓库还没有任何 v* 标签 | 首次发布 | → v0.1.0 |

标签是唯一版本来源（`v主.次.修订`），CHANGELOG 由脚本从提交信息生成，不要手改版本号。

## 流程

1. 看现状，确认要发布的改动：

```bash
git status --short
git tag --sort=-v:refname | head -3
LAST=$(git describe --tags --abbrev=0 2>/dev/null || true)
if [ -n "$LAST" ]; then git log --oneline "$LAST..HEAD"; else git log --oneline -20; fi
```

2. 预览发布计划（不写文件、不提交、不打标签）：

```bash
scripts/release.sh --dry-run
```

把生成的版本号和发布说明给用户看。需要强制递增级别时加 `major` / `minor` / `patch`：

```bash
scripts/release.sh minor --dry-run
```

3. 正式发布（自动写 CHANGELOG、提交 `chore(release): vX.Y.Z`、打 annotated tag，并推送项目）：

```bash
scripts/release.sh
```

脚本默认发布到两个远端：

- `origin`（GitHub，`https://github.com/free799-max/one-tap`）：推送完整源码快照，但排除 `.gitlab-ci.yml`，也不推版本标签。
- `gitlab`：推送完整 `main` 与版本标签，标签触发 CI。

只推其中一个时用 `RELEASE_REMOTES="gitlab" scripts/release.sh` 或 `RELEASE_REMOTES="origin" scripts/release.sh`。

4. 验证：

```bash
git tag -n1 --sort=-v:refname | head -3
git ls-remote --tags gitlab | tail -5          # 标签已到 GitLab
git ls-remote gitlab refs/heads/main
git ls-remote origin refs/heads/main
```

流水线在 `https://git.cacty.top/nas/one-tap/-/pipelines`，只有 tag 流水线：`app-image` 以
`harbor.cacty.top/library/onetap-base` 为基础镜像（由 `docker/base.Dockerfile` 手动构建，流水线不构建），
推送 `harbor.cacty.top/library/onetap:<tag>`，正式版本同时打 `latest`；腾讯云
`ccr.ccs.tencentyun.com/one-tap/one-tap` 与 Docker Hub `docker.io/free5104/one-tap`
同样只推 `<tag>` 与 `latest`。Docker Hub 推送需要 GitLab CI/CD 变量
`DOCKERHUB_USERNAME` / `DOCKERHUB_PASSWORD`（可用 Access Token），或 Runner 宿主机已有
Docker Hub 登录状态。
部署侧按标签升级：

```bash
APP_IMAGE=harbor.cacty.top/library/onetap:v1.2.3 docker compose pull app
APP_IMAGE=harbor.cacty.top/library/onetap:v1.2.3 docker compose up -d app
```

## 失败处理

- 推送失败（凭据缺失、网络问题）：提交和标签已经生成在本地，**不要重跑脚本**（会再进一个版本）。修好凭据后手动补推：

```bash
git push gitlab HEAD:main && git push gitlab vX.Y.Z
scripts/push-github.sh HEAD
```

- 需要交互输入 HTTPS 密码时：`GIT_TERMINAL_PROMPT=1 scripts/release.sh`。
- 标签打错且**尚未推送**：`git tag -d vX.Y.Z && git reset --hard HEAD~1` 后重新发布。
- 标签已推送：不要删除或重打、不要 force push；发一个更高的修订版修正。

## 红线

- 工作区必须干净；脚本会拒绝脏工作区和非 `main` 分支。
- 版本号只增不减，不修改已推送的标签和历史。
- 不改 `CHANGELOG.md` 里已有版本的条目。
- 不读、不打印 `.env` 与任何密钥。
