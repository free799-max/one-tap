# OneTap

NAS 管理平台：Docker 容器/镜像/Compose Stack 管理、应用商店（内置 NAS 应用模板一键部署）、自动化编排与看板视图。开发时前后端分离，生产环境合并为一个应用容器，支持 Docker Compose 一键部署。

## 技术栈

| 层级 | 技术 |
|------|------|
| 后端 | Python 3.12 + FastAPI + SQLAlchemy 2.0（异步）|
| 数据库 | PostgreSQL 16（asyncpg 异步驱动）+ Alembic 迁移 |
| 前端 | React 19 + TypeScript + Vite + Tailwind CSS + shadcn/ui（pnpm 管理）|
| 认证 | JWT Bearer Token + bcrypt（首个注册用户自动成为管理员）|
| 部署 | Docker + Docker Compose（前端静态文件与 FastAPI 合并到同一应用容器）|

## Docker Compose 部署（推荐）

生产环境只启动两个容器：

- `app`：Nginx 对外提供前端并反代 `/api`，容器内运行 FastAPI；启动时自动执行数据库迁移。
- `postgres`：PostgreSQL 16，仅加入内部网络，不向宿主机暴露端口。

环境要求：Docker 与 Docker Compose v2.24+。

```bash
git clone <仓库地址>
cd onetap

cp .env.example .env
# 必填：POSTGRES_PASSWORD、SECRET_KEY
$EDITOR .env

docker compose up --build -d
docker compose ps
```

访问 `http://localhost:${APP_PORT}`（默认 `http://localhost:5000`），API 文档位于 `/docs`。

### 环境变量

`docker-compose.yml` 使用 `.env` 完成变量替换，并通过 `env_file` 注入 `app` 容器；运行时环境变量会覆盖 `backend/app/config.py` 中的内置默认值。通常只需填写数据库密码。

| 变量 | 必填 | 默认值 | 说明 |
|---|---|---|---|
| `APP_PORT` | 否 | `5000` | 宿主机访问端口，容器内固定为 80 |
| `POSTGRES_DB` | 否 | `onetap` | 数据库名，仅首次初始化数据卷时生效 |
| `POSTGRES_USER` | 否 | `onetap` | 数据库用户，仅首次初始化数据卷时生效 |
| `POSTGRES_PASSWORD` | **是** | 无 | 数据库密码，建议用 `openssl rand -hex 24` 生成 |
| `SECRET_KEY` | 否 | 自动生成并持久化 | JWT 签名密钥，默认保存到 `DATA_ROOT/.secret_key`，仅在需要自行指定时配置 |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | 否 | `1440` | 登录令牌有效期 |
| `REGISTRATION_OPEN` | 否 | `false` | `false` 时仅首个用户可注册并成为管理员 |
| `CORS_ORIGINS` | 否 | 空 | 跨域来源，逗号分隔；同源部署无需配置 |
| `TZ` | 否 | `Asia/Shanghai` | 应用与数据库时区 |
| `APP_NAME` | 否 | `OneTap` | 页面中的应用名称 |
| `DATA_DIR` | 否 | `./data` | Compose 工作区、图标等应用数据的宿主机目录 |
| `MANAGE_BASE_URL` | 否 | 镜像内置 `https://m.one-tap.vip,https://m.one-tab.vip` | 配套管理端 `onetap-manage` 的对外地址，逗号分隔多个地址时前一个不可用自动重试下一个；应用商店需要它，`.env` 中显式填写会覆盖镜像默认值 |
| `MANAGE_SYNC_INTERVAL_MINUTES` | 否 | `30` | 应用市场与自动化方案同步间隔 |
| `MANAGE_SYNC_ON_STARTUP` | 否 | `true` | 启动时立即同步一次 |

应用市场数据来自管理端。镜像构建时会把 `MANAGE_BASE_URL` 的默认值写进镜像（当前为 `https://m.one-tap.vip,https://m.one-tab.vip`），运行时可用 `.env` 中的同名变量覆盖。支持逗号分隔的多个地址：网络错误或 5xx 时自动换下一个地址，最近一次成功的地址优先使用（实现见 `backend/app/services/manage_client.py`）。地址必须是 `app` 容器可访问的地址，不能填写容器内的 `localhost`。

数据库数据保存在命名卷 `onetap_postgres_data`，应用数据保存在 `${DATA_DIR}`。首次启动自动迁移；访问首页注册首个账号即为管理员。

> 说明：`app` 挂载了 `/var/run/docker.sock` 以提供容器/Compose 管理功能，等同于授予容器宿主机 root 权限，请仅在受信环境使用；不需要时可删除该挂载（相关功能自动降级）。系统设置中的宿主机存储目录也需要按相同路径额外挂载进 `app`，否则容器内无法浏览或写入。

## 版本发布与镜像构建（GitLab CI + Harbor）

发布走标签：只有推送 `v主.次.修订` 标签才会触发 `.gitlab-ci.yml`，分支推送和合并请求不会构建镜像。

首次使用需要：

1. 在 Harbor 创建项目 `library`（或修改流水线变量 `HARBOR_PROJECT`）。
2. 在 Runner 宿主机上执行 `docker login harbor.cacty.top`，流水线直接复用该登录状态（不再需要 `HARBOR_USER` / `HARBOR_PASSWORD`）。
3. Runner 使用 shell executor，宿主机需安装 Docker 与 buildx，并以有权限调用 Docker 的用户运行（若改用 docker executor，需要自行恢复 `docker:dind` 服务与 `DOCKER_HOST`）。
4. `PLATFORMS` 默认 `linux/amd64`；NAS 是 ARM 时在 CI/CD 变量中改为 `linux/arm64`，并用同一平台重建基础镜像。

发布新版本：

```bash
scripts/release.sh --dry-run   # 预览版本号与更新说明，不改动任何文件
scripts/release.sh             # GitHub 发布前端，GitLab 发布完整项目与标签
```

版本号按提交信息自动递增（`feat` → minor，`fix` 等 → patch，`feat!` / `BREAKING CHANGE` → major，首次发布为 `v0.1.0`），也可以显式指定 `major|minor|patch`。脚本把更新说明写入 `CHANGELOG.md`，提交 `chore(release): vX.Y.Z` 后打 annotated tag。`origin`（GitHub）只接收 `frontend/` 的独立快照，不接收其他目录或标签；`gitlab` 接收完整 `main` 与标签并触发流水线。只推一个远端时用 `RELEASE_REMOTES="gitlab" scripts/release.sh`，单独补推前端用 `scripts/push-frontend.sh HEAD`。

基础镜像 `harbor.cacty.top/library/onetap-base` 由 `docker/base.Dockerfile` 手动构建并推送，流水线只拉取使用：

```bash
docker build -f docker/base.Dockerfile -t harbor.cacty.top/library/onetap-base:latest .
docker push harbor.cacty.top/library/onetap-base:latest
```

系统包或 `backend/requirements.txt` 变化后重建基础镜像。应用镜像仍由标签流水线构建：

| 阶段 | 产物 | 说明 |
|---|---|---|
| `image` | `harbor.cacty.top/library/onetap:<tag>`；正式版本另打 `v主.次`、`v主`、`latest` | 以 `onetap-base` 为起点，只构建后端代码与前端，配合 buildx registry 缓存 |

`onetap:buildcache` 是 buildx 的层缓存标签，不是可部署镜像。

构建期的 `MANAGE_BASE_URL`、`MANAGE_SYNC_INTERVAL_MINUTES` 和 `MANAGE_SYNC_ON_STARTUP` 由流水线变量在构建前替换 `backend/app/config.py` 中的默认值；运行时 `.env` 里的同名变量优先级更高。

部署时在 NAS 上拉取指定版本的镜像：

```bash
docker login harbor.cacty.top
APP_IMAGE=harbor.cacty.top/library/onetap:v1.2.3 docker compose pull app
APP_IMAGE=harbor.cacty.top/library/onetap:v1.2.3 docker compose up -d app
```

把 `v1.2.3` 换成 `latest` 即跟随最新正式版。本地 `docker compose up -d --build` 同样以 `onetap-base` 为基础镜像，需要先 `docker login harbor.cacty.top`。

## 本地开发（Linux）

### 后端（`backend/`）

```bash
# 启动 PostgreSQL（如未运行，映射宿主机 5434 端口）
docker run -d --name onetap-db -e POSTGRES_USER=onetap \
  -e POSTGRES_PASSWORD=onetap -e POSTGRES_DB=onetap \
  -p 5434:5432 postgres:16-alpine

cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 启动开发服务器（http://localhost:5001）
PYTHONPATH=./app .venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 5001

# 测试与数据库迁移
PYTHONPATH=./app .venv/bin/python -m pytest
PYTHONPATH=./app .venv/bin/python -m alembic upgrade head
```

### 前端（`frontend/`，pnpm）

```bash
cd frontend
pnpm install
pnpm run dev      # 开发服务器 http://localhost:5000，代理 /api -> localhost:5001
pnpm run build    # TypeScript 编译 + Vite 构建
pnpm run lint     # ESLint 检查
```

> 另提供 pm2 本机开发备选方案（`ecosystem.config.js` + `start-backend.sh`），正式部署请使用 docker compose。

## 目录结构

```
onetap/
├── backend/                # FastAPI 后端
│   ├── app/
│   │   ├── api/            # 路由层（auth/orchestrations/app_store/deploy_tasks/settings/docker）
│   │   ├── services/       # 业务层（docker/compose/orchestration/apps/app_store/host）
│   │   ├── models/         # SQLAlchemy ORM 模型
│   │   ├── schemas/        # Pydantic 请求/响应模型
│   │   └── main.py         # FastAPI 入口（lifespan 自动执行迁移）
│   ├── alembic/            # 数据库迁移
│   └── tests/              # pytest 测试套件
├── frontend/               # React + Vite 前端
│   ├── src/api/            # 后端请求的唯一封装入口（与后端 schema 对齐）
│   ├── src/pages/          # 页面（dashboard/apps/automation/docker/settings/auth）
│   └── public/             # 图标等静态资源
├── docker/                 # 合并容器的 Nginx 配置与启动脚本
├── Dockerfile              # 前端构建 + Python 运行时的多阶段镜像
├── docs/                   # 设计文档与 API 规范
├── docker-compose.yml      # app + postgres 一键部署
└── .env.example            # Compose 环境变量模板
```

## 更多文档

- [CLAUDE.md](CLAUDE.md)：架构分层、开发约定、异步部署任务（SSE）、应用商店维护等开发细节
- [docs/整体架构设计.md](docs/整体架构设计.md)：整体架构设计
- [docs/api/api-response-spec.md](docs/api/api-response-spec.md)：API 统一响应格式规范
