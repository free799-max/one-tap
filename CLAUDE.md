# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 项目概述

OneTap — NAS 管理平台，提供 Docker 容器/镜像/Compose Stack 管理、应用商店（内置 NAS 应用模板）和看板视图。前后端分离，后端为 FastAPI + SQLAlchemy 异步服务。

## 开发命令

### 后端（在 `backend/` 目录下执行）

```bash
# 启动 PostgreSQL（如未运行）
docker run -d --name onetap-db -e POSTGRES_USER=onetap -e POSTGRES_PASSWORD=onetap -e POSTGRES_DB=onetap -p 5434:5432 postgres:16-alpine

# 启动开发服务器
cd backend
PYTHONPATH=./app .venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 5001

# 运行全部测试
PYTHONPATH=./app .venv/bin/python -m pytest

# 运行单个测试文件
PYTHONPATH=./app .venv/bin/python -m pytest tests/docker/test_docker_manager.py

# 运行单个测试函数
PYTHONPATH=./app .venv/bin/python -m pytest tests/docker/test_docker_manager.py::test_list_containers -v

# 数据库迁移
PYTHONPATH=./app .venv/bin/python -m alembic upgrade head
```

> 注：Windows 环境请将 `.venv/bin/python` 替换为 `.venv/Scripts/python.exe`。

### 前端（`frontend/` 目录下）

```bash
npm run dev          # 开发服务器（端口 5000，代理 /api -> localhost:5001）
npm run build        # TypeScript 编译 + Vite 构建
npm run lint         # ESLint 检查
```

### Docker

```bash
docker compose up --build -d   # 构建并启动 app（前端 + FastAPI）与 postgres 两个容器
docker compose logs -f app     # 查看应用、迁移与定时任务日志
```

生产环境只有 `app` 与 `postgres` 两个容器；`app` 内的 Nginx 对外监听 80，FastAPI 只监听容器内回环地址 5001，前端与 API 使用同一端口。

镜像发布走 GitLab CI（`.gitlab-ci.yml`），且**只由推送 `v主.次.修订` 标签触发**，分支推送不构建。发版统一用 `scripts/release.sh`：按提交信息递增版本、写 `CHANGELOG.md`、提交 `chore(release): vX.Y.Z` 并打标签（`--dry-run` 只预览）。用户说「小版本 / 小小版本」指只递增修订号（patch），保持当前次版本不变（v0.15.0 → v0.15.1），用 `scripts/release.sh patch`。`origin`（GitHub）接收完整源码快照但排除 `.gitlab-ci.yml`、不推标签；`gitlab` 接收完整 `main` 与标签。基础镜像 `harbor.cacty.top/library/onetap-base` 由 `docker/base.Dockerfile` 手动构建推送，流水线不构建它，只以它为基础镜像构建后端代码与前端，产物为 `onetap:<tag>`，同时推送到腾讯云 CCR `ccr.ccs.tencentyun.com/one-tap/one-tap` 与 Docker Hub `docker.io/free5104/one-tap`（正式版本另打 `latest`；Docker Hub 需在 CI/CD 变量配置 `DOCKERHUB_USERNAME` / `DOCKERHUB_PASSWORD`）。Harbor 与腾讯云 CCR 是发布必需源，`latest` 和版本标签在同一次 buildx 推送里完成；Docker Hub 从 Harbor 复制清单、单独推送且失败仅告警，避免其网络不可达时中断推送导致 `latest` 落后。`MANAGE_BASE_URL` 不设代码内置默认值：GitLab CI 构建时通过同名 `--build-arg` 注入 `https://m.one-tap.vip,https://m.one-tab.vip`（可用项目同名 CI/CD 变量覆盖），运行时 `.env` 的同名变量优先（逗号分隔多地址按顺序重试），留空即关闭同步。系统包或 `backend/requirements.txt` 变化后需要重建基础镜像。

## 架构

### 请求生命周期

```
用户请求 -> Nginx(生产) / Vite Proxy(开发) -> FastAPI -> SQLAlchemy(异步) -> PostgreSQL
                                              |
                                              +-> JWT 认证(get_current_user 依赖注入)
                                              +-> CustomAPIRoute 自动包装 StandardResponse
                                              +-> services/ 业务层 -> Docker SDK / docker compose CLI
```

### 后端分层（`backend/app/`）

| 层 | 目录 | 职责 |
|---|---|---|
| 入口 | `main.py` | FastAPI 实例、lifespan（初始化DB）、CORS、路由注册、全局异常处理器 |
| 配置 | `config.py` | pydantic-settings；Compose 通过 `env_file: .env` 把项目根 `.env` 整份注入 app 容器，config.py 不重复声明注入项；仅 `DATA_ROOT`（固定 `/data`）与 `DATABASE_URL`（由 POSTGRES_* 拼接）由 Compose 显式覆盖；授权相关配置未在 `.env` 暴露，走后端默认值；本地开发从 `backend/.env` 加载 |
| 数据库 | `database.py` | SQLAlchemy 2.0 异步引擎(asyncpg/aiosqlite)、会话工厂、`get_db()` 依赖（自动 commit/rollback） |
| API | `api/` | 路由模块（auth/app_store/dashboard/settings），编排路由在 `api/orchestration/` 子包（orchestrations/deploy_tasks），Docker 路由按领域拆分为 `api/docker/` 子包 |
| 核心 | `core/` | 基础设施：security(JWT+bcrypt)、custom_route(响应包装)、exceptions、background(后台任务)、task_manager(任务管理器基类 BaseTaskManager) |
| 业务服务 | `services/` | 按功能域拆分：`docker/`（容器/镜像/宿主机/拉取任务）、`compose/`（Compose 编排/自动发现/YAML 工具）、`orchestration/`（编排与部署任务，service 按职责拆为 deploy/restore/importer/provisioning/instances mixin）、`apps/`（应用客户端）、`app_store/`、`overview.py`（服务概览聚合与访问地址生成）、`host/`、`system/`（系统配置/存储路径解析/数据目录/端口分配） |
| 模型 | `models/` | SQLAlchemy ORM 模型；Docker 相关模型按领域拆分为 `models/docker/` 子包 |
| Schema | `schemas/` | Pydantic 请求/响应模型；Docker 与编排相关 Schema 分别拆分为 `schemas/docker/`、`schemas/orchestration/` 子包 |
| 测试 | `tests/` | pytest，按领域分目录：core/、app_store/、dashboard/、docker/、orchestration/、system/ |

### 前端分层（`frontend/src/`）

| 层 | 目录 | 职责 |
|---|---|---|
| 入口 | `App.tsx` | QueryClientProvider + AuthProvider + ToastProvider + BrowserRouter + ProtectedRoute |
| 布局 | `layouts/` | AppLayout(Sidebar+TopBar+Outlet)、Sidebar(导航)、TopBar |
| 页面 | `pages/` | 按功能域组织子目录：`auth/`、`dashboard/`（服务概览应用墙）、`apps/`、`automation/`(自动化编排)、`docker/`(含 containers/images/stacks/host/shared)、`settings/` |
| 数据 | `hooks/` | React Query hooks，只做缓存/状态/toast/SSE；**不定义接口类型、不直接拼 URL**，请求一律调用 `api/` 层函数，类型从 `api/` re-export 以兼容既有 import |
| API 封装 | `api/` | **所有后端请求的唯一入口**：按领域分文件（auth/apps/dashboard/orchestrations/settings/directories/compose/docker），接口类型定义也在此，与后端 schemas 对齐只改这层。`types.ts` 导出共享 `ApiError` |
| HTTP 实例 | `lib/api.ts` | Axios 实例：请求拦截器附加 Bearer token；响应拦截器校验 `success` 标识后解包 `data`；401 时仅当请求 token 与本地一致才清除并跳转 /login（防竞态） |
| 工具 | `lib/utils.ts` | `cn()`(clsx+twMerge)、`formatBytes()`、`formatDate()` — Docker 页面统一从此导入 |
| UI | `components/ui/` | shadcn/ui 组件（@base-ui/react 原语），不手动修改 |
| 认证 | `contexts/AuthContext.tsx` | 全局认证状态，初始化时调 `/auth/me` 验证 token |

### Docker 后端目录拆分

重构后，Docker 相关业务按领域拆分到子包/子目录，避免单文件过大：

```
api/docker/
├── __init__.py          # 总路由，prefix=/api/docker，include 各子路由
├── containers.py        # /api/docker/containers/*
├── images.py            # /api/docker/images/*
├── registries.py        # /api/docker/registries/*
├── compose.py           # /api/docker/compose/*
└── host.py              # /api/docker/host/*

models/docker/
├── __init__.py
├── container.py         # DockerContainer、DockerMirrorConfig
└── compose.py           # DockerComposeProject、DockerComposeVersion、DockerComposeStack、COMPOSE_PROJECT_LABEL

schemas/docker/
├── __init__.py
├── container.py
├── image.py
├── registry.py
├── compose.py
└── host.py

services/
├── docker/              # Docker 容器/镜像/宿主机/拉取任务
│   ├── __init__.py
│   ├── container_service.py
│   ├── image_service.py
│   ├── host_service.py
│   ├── pull_task_service.py
│   └── docker_common.py
├── compose/             # Docker Compose 编排与自动发现
│   ├── __init__.py
│   ├── compose_service.py    # 项目生命周期/命令执行/日志状态
│   ├── compose_discovery.py
│   ├── yaml_utils.py         # 项目名/YAML 校验、标签注入、端口改写（纯函数）
│   ├── log_utils.py          # 日志输出处理（strip_ansi/detect_stage）
│   └── permissions.py        # bind 挂载提取、PUID/PGID 解析
├── orchestration/       # 编排与部署任务
│   ├── __init__.py           # 导出 OrchestrationService、orchestration_service
│   ├── service.py            # 主类（组合各 mixin）+ 模板查询/认证检测
│   ├── deploy.py             # DeployMixin：部署/重新部署
│   ├── importer.py           # ImportMixin：扫描导入候选/导入现有容器
│   ├── provisioning.py       # ProvisionMixin：部署后配置下发
│   ├── wiring.py             # WiringMixin：部署后跨应用自动装配（如 qb/jellyfin 配进 MoviePilot）
│   ├── inspection.py         # InspectionMixin：应用检测/修复调度、插件管理
│   ├── instances.py          # InstanceMixin：实例组 CRUD
│   ├── credentials.py / shared_config.py / deploy_task_service.py
└── system/              # 系统级共享服务
    ├── __init__.py           # re-export 全部公开名
    ├── config_service.py     # SystemConfigService + 单例
    ├── storage.py            # StoragePathResolver（含数据目录 host_path ↔ mount_path 前缀互转）
    ├── storage_directory_service.py  # 数据目录 CRUD（storage_directories 表，含容器内可见性校验）
    └── port_allocator.py     # 端口分配/预留
```

`api/orchestration/__init__.py` 统一导出 `orchestrations_router` 与 `deploy_tasks_router`，`main.py` 从该包一处导入。

## 数据模型关系

```
User 1--* AppInstance 1--1 DockerComposeProject
DockerComposeProject 1--* DockerComposeVersion
DockerComposeProject 1--1 DockerComposeStack
```

- `DockerComposeProject` 是独立实体，不依赖插件实例；其当前激活版本通过 `DockerComposeVersion.is_current` 标识。
- Compose 运行时文件写入 `COMPOSE_WORKSPACE_DIR`（默认 `./data/compose`），并自动注入 `onetap.compose.project` 标签用于归属识别。

## 存储目录约定

- **存储目录**统一由 `storage_directories` 表管理（CRUD 在 `/api/settings/storage-directories`），每条维护 `host_path`（宿主机路径，compose bind source）↔ `mount_path`（容器内/表单视角路径）对应关系；保存时校验 `mount_path` 在应用容器内可见，其宿主机路径需按相同路径挂载进 `app` 容器（见 docker-compose.yml 注释示例）。
- **应用数据目录**（`is_app_data=True`，全局至多一条）是特殊的存储目录，存应用自身持久化小数据。部署表单把应用挂载的相对默认值物化为 `<应用数据目录>/<实例名>/<应用名>/<子目录>`（如 `comic/comic-auto/data`），路径在表单里可直接编辑；实例改名时同步替换旧的实例名前缀（`frontend/src/lib/schema-utils.ts` 的 `applyInstanceDirPrefix`）。编排的共享目录默认落在 `<实例名>/media`，与各应用目录同级。其余为普通**数据目录**（多条），供大数据量应用使用。
- 部署表单的目录字段强制落在应用数据目录或某个启用数据目录下；部署时 volumes 的绝对 `host_path` 会经 `StoragePathResolver.is_managed_path` 校验。

## API 响应格式规范

所有接口（除 204 No Content 外）统一返回 `StandardResponse` 格式：

```json
// 成功
{"success": true, "data": ..., "message": "ok"}

// 错误
{"success": false, "data": null, "message": "错误描述"}
```

- 后端路由层**直接返回数据**，`CustomAPIRoute` 自动包装，无需手动构造 `StandardResponse`
- 业务异常统一使用 `APIException(message, status_code)`，**禁止使用 `HTTPException`**
- 204 No Content（删除操作）保持空响应体，不包装
- 前端 Axios 拦截器自动解包 `data` 字段（要求响应带 `success` 标识），`src/api/` 层函数统一返回解包后的业务数据
- 错误消息通过 `error.displayMessage` 传递给页面层 toast

完整规范见 `docs/api/api-response-spec.md`。

## 异步部署任务机制

部署类操作（应用部署、编排部署/导入、Compose 创建/编辑/启停）均为**异步任务**，不是同步请求：

- 后端：POST 接口立即返回 `task_id`（`DeployTaskCreateResponse`），实际部署由 `services/orchestration/deploy_task_service.py` 在后台执行；进度通过 SSE `GET /api/deploy-tasks/{task_id}/events` 推送（事件内嵌 `_task_status` 标记完成/失败）。
- 前端：`hooks/useDeployTasks.ts` 管理任务状态与 SSE 连接，`components/DeployProgressDialog.tsx` 通过 `DeployTaskContext` 全局监听并展示进度；发起部署的组件调用 `startTask(taskId)` 接入。
- 镜像拉取有独立的任务机制（`pull_task_service` + `useAllPullProgress`），不复用部署任务通道。
- 组合部署在创建实例组前预检并发槽位与 Compose 项目名：槽位不足或项目名被占用时直接返回 409，避免留下没有应用的空实例组。删除实例组时，即使部署失败导致项目未回写到实例的 `project_id`，也会按项目名兜底一并清理。
- `group` 只是应用角色的展示分组（如播放器、阅读器），同一 group 内的应用可以同时选中部署；只有显式声明 `conflict_with` 的应用才互斥。

## 删除实例组与数据备份/恢复

- `DELETE /api/orchestrations/instances/{id}` 改为后台任务：立即返回 `{task_id}`，进度通过部署任务 SSE 推送（阶段 `preparing → stopping_services → backing_up_data → cleaning_records → completed`），前端复用全局任务进度弹窗。删除逻辑本身先用 `stop`/移除容器，再把各应用自身的数据目录移入 `<应用数据目录>/_backups/<时间戳>-<实例组名>/`，最后删除数据库记录；实现见 `services/orchestration/backup.py`。
- 删除前会检查组内是否有进行中的部署任务；有则返回 409，避免删除正在部署的实例。
- 只备份应用自身的数据目录：新布局按应用配置中的绝对挂载推导 `<应用数据目录>/<实例名>` 整体移动，旧布局按 Compose 项目名目录移动；共享存储（媒体/下载等）与应用数据目录之外的路径不动。若未配置应用数据目录则跳过备份。
- 备份目录内写入 `backup.json`（实例组、编排、删除时间、各应用 `config`、`app_configs` 凭据、`shared_config`、目录映射），用于恢复。
- 恢复：`GET /api/orchestrations/backups?orchestration=<name>` 列出备份，`POST /api/orchestrations/backups/restore`（`{backup, instance_name?}`）把数据目录移回并按备份配置重新部署；新布局按实例名目录移回，换实例名恢复时同步改写配置里的路径前缀。凭据沿用备份（系统统一密码变化时用 `previous_password` 登录旧密码再改密）；实现见 `services/orchestration/restore.py`，前端入口为自动化页右上角「恢复」按钮（`pages/automation/AutomationRestoreDialog.tsx`）。恢复成功后备份目录被消费。恢复后的账号自动配置与集成装配由 `orchestration_provision` 进度任务持续展示，完成后自动刷新实例详情。
- 重新部署（修复）不走备份：它有意保留数据卷，并继承上次部署的密钥类 env。修复时可只勾选需要重部署的应用（包括取消必选应用），未勾选的应用保持运行；后端 `_validate_composition(require_required=False)` 只校验所选应用。
- 普通 Compose 项目删除同样复用这套备份：`DELETE /api/docker/compose/{id}` 调用 `compose_manager.delete_project(..., backup_data=True)`，先 `down` 并移除容器，再把实例配置或当前 Compose 文件声明的数据目录移入 `_backups`，最后删除项目记录。重新部署时原路径为空，会全新初始化；手动 Compose 项目没有实例记录时从 Compose 文件的 bind mount 推导目录。`backup_data` 默认 False，内部替换项目（如 AdGuard 重新部署）不会搬走数据。

## 服务概览

- 首页采用 sun-panel 式应用墙：顶部展示宿主机 CPU/内存/磁盘/运行时间与当前时间，下方按应用分类（`apps.category`，缺失时回落到编排分类）自动分组；分组顺序取管理端下发的分类字典（`GET /api/public/categories`），前端展示名称同样来自该字典。
- 只展示真实存在的部署：平台应用按 Compose 项目关联的容器校验，导入应用按 `container_id`/`container_name` 校验；失败、已清理或没有容器的历史记录会被过滤，运行状态以 Docker 实际状态为准。
- 接口 `GET /api/dashboard/overview`（`api/dashboard.py` + `services/overview.py`）只返回展示所需字段，不下发实例配置中的密码等敏感信息；`GET /api/dashboard/apps/{id}` 返回单个应用的容器基本信息（镜像、容器 ID/名称、项目、端口映射、启动时间）和实时监控指标（CPU、内存、网络、磁盘 I/O、进程数）。
- 访问地址无需落库：优先使用实例配置中显式填写的 `url`，否则按当前请求主机名 + 实例端口自动生成（兼容 `ports` 端口数组、单端口字段及导入容器的 `host_port`）；编排实例组的 `app_configs.url` 仅作兜底。
- 宿主机实时指标来自 `GET /api/docker/host/info`（`services/docker/host_service.py`）：`resources.cpu_usage_percent`、`resources.memory_used`、`uptime_seconds`、`load_average` 由 `/proc` 采样；前端 `useDockerHostInfo` 每 10 秒刷新。
- 前端 `pages/dashboard/DashboardPage.tsx` 负责状态栏、应用墙、分类统计与搜索；卡片右上角「详情」按钮打开 `AppDetailDialog`，展示容器基本信息与监控指标，打开期间每 5 秒刷新；`hooks/useDashboard.ts` 每 60 秒刷新概览，部署任务完成后会主动失效 `["dashboard", "overview"]` 缓存。
- 测试：`backend/tests/dashboard/`。

## 应用市场数据同步

- 应用定义由配套管理端（onetap-manage）维护，本仓库**不再提供应用创建/编辑/删除/图标上传**。原 `POST/PUT/DELETE /api/apps`、`POST /api/apps/preview-template`、`POST/GET /api/apps/{name}/icon` 接口与前端 `/appstore/create`、`/appstore/:name/edit` 页面均已移除。
- 管理端地址代码内默认留空，镜像构建时由 GitLab CI 注入 `https://m.one-tap.vip,https://m.one-tab.vip` 作为默认值，运行时通过 `.env` 的同名变量覆盖（同步间隔与启动同步默认 30 分钟 / 开启，同样可用 `MANAGE_SYNC_INTERVAL_MINUTES` / `MANAGE_SYNC_ON_STARTUP` 覆盖）：`MANAGE_BASE_URL` 支持逗号分隔的多个地址，请求失败按顺序重试下一个并记住最近成功地址（见 `services/manage_client.py`）。后端启动时同步一次；之后按间隔检查，仅在「自动更新」开启、当前处于设置里配置的夜间时段（默认 02:00-06:00）且当晚尚未执行时，才同步 `GET {MANAGE_BASE_URL}/api/public/apps` 并自动升级，upsert 到本地 `apps` 表作为只读缓存；自动升级只处理设置里明确勾选的服务（`auto_update_targets`，`group:<id>` / `app:<id>`），未勾选不执行，升级结果写入 `orchestration_update_events`，设置页「更新配置」弹窗分「更新配置 / 更新记录」两页（实现见 `services/catalog_sync.py` 的 `sync_apps` 与 `main.py` 的 `_sync_loop`，夜间窗口判断见 `services/orchestration/auto_update.py` 的 `in_update_window`）。当前生产环境管理端地址为 `http://192.168.1.17:5101`。
- 只同步带 `compose_template` 且通过模板 / Schema 校验的条目，校验失败仅告警跳过；管理端下架（远端不再返回）的应用会删除本地缓存，但只删非内置且没有部署实例的记录，远端返回空列表时不做清理；`is_builtin` 等本地字段保持不变。
- 分类为自由字符串，展示名称与排序由管理端 `backend/app/core/categories.py` 统一维护，经 `GET /api/public/categories` 下发；本仓库启动/定时同步到进程内缓存，前端通过 `GET /api/apps/categories` 获取（`frontend/src/lib/categories.ts` 仅保留查表辅助，不再内置映射），未配置 `MANAGE_BASE_URL` 时回退显示原始分类值。
- 应用定义与应用目录 JSON 已整体迁移到管理端仓库：数据文件在 `onetap-manage/backend/app/data/apps/`，由管理端启动种子与 `scripts/import_apps.py` 导入；本仓库不再保留应用导入脚本。

## 网络优化（AdGuard / Hosts 优选）

- 应用集成页「网络优化」Tab（内部仍称 Hosts 优选），配置保存在 `system_config.hosts_optimizer_*` 字段；支持手动运行与按间隔自动运行。
- 后端服务：`services/system/hosts_optimizer_service.py`。通过 DNSPod DoH（`doh.pub` / `1.12.12.12`）获取候选 IP，配置了系统代理时再补充 Cloudflare / Google DoH；对候选 IP 做 TLS + HTTP 探测后，写入 AdGuard Home 的 DNS 重写（`/control/rewrite/*`）。
- 接口：`GET/PUT /api/settings/hosts-optimizer/config`、`GET .../status`、`POST .../test`、`POST .../run`（同步执行，上限 180 秒）。
- 调度器在 `main.py` lifespan 中启动，每 30 秒检查一次配置；`enabled && mode == "auto"` 且到达 `finished_at + interval` 时运行。配置保存后调用 `wake()` 立即重新判断。
- 探测失败（某域名没有可用 IP）时保留 AdGuard 中已有的重写，避免临时网络抖动导致回退到被污染的默认解析。
- **只对把 DNS 指向 AdGuard 的客户端生效**；本功能不写宿主机 `/etc/hosts`。Docker 容器需在 compose 中显式配置 `dns: [<AdGuard-IP>]`。
- 管理端 AdGuard 应用默认映射 `3000 -> 3000`（初始化）与 `8080 -> 80`（管理界面）。「网络优化」的 AdGuard 区域统一提供「服务地址 / 登录账号 / 登录密码 + 测试 + 保存配置」表单：托管实例的服务地址只读（由后端通过容器 IP 维护），账号密码可编辑；保存时若账号密码有变化，后端会写回实例的 `AdGuardHome.yaml` 并重启容器。手动接入外部 AdGuard 时填写 `http://<NAS-IP>:8080`。
- 「影视自动化」方案（管理端 `movie`/`movie-auto` 与内置 `media-stack`，1.1.0 起）把 AdGuard Home 列为可选组件（分组「网络优化」），与 MoviePilot、qBittorrent 等一起部署；已有实例可在「升级」时勾选安装。
- 方案中 AdGuard 固定使用稳定版 `adguard/adguardhome:v0.107.79`（与 1.23 上实际运行的版本一致），不使用 `latest`。
- AdGuard 应用定义标记 `x-fixed-ports`（`config_schema` 顶层），`app_service._validate_and_prepare` 会跳过顺序端口分配，保留 53、3000、8080 等端口；否则 DNS 会被改到高位端口导致无法作为局域网 DNS 使用。固定端口在部署前会检测占用并纳入预留表，避免同批部署的其他应用分配到同一端口；前端端口建议（`lib/schema-utils.ts`）同样跳过 `x-fixed-ports` 应用。
- 测试：`backend/tests/system/test_hosts_optimizer.py`。

## 应用级 DNS 加速

- 入口：应用集成 →「网络优化」→「应用加速」。列表只展示已开启加速的应用，通过「添加应用」弹窗按需选择（与「应用集成 → 反向代理与域名」的添加方式一致）；弹窗按自动化方案分组，支持全选一组。开启动作逐个提交，避免并发重启应用。只对添加的应用生效，不修改全局代理或宿主机 DNS。
- 服务：`services/system/hosts_acceleration_service.py`。
  - 首次开启自动部署专用 AdGuard Home（Compose 项目 `onetap-adguard`，容器 `onetap-adguardhome`）。应用市场目录中存在 `adguardhome` 时，前端复用应用市场的安装弹窗（可确认镜像、端口、存储、环境变量与 Compose 预览），提交后由 `app_service.deploy` 执行：复用应用模板、端口检测、数据目录挂载、`AppInstance` 记录与部署任务；目录中没有该应用时才回退到内置 Compose。镜像固定 `adguard/adguardhome:v0.107.79`。
  - 只映射初始化端口（容器 3000）和管理端口（容器 80），**不映射宿主机 53**。安装弹窗通过 `GET /api/settings/port-suggestions?preferred=...&exclude_container=onetap-adguardhome` 预填宿主端口：优先复用旧的可用端口，被其他服务占用时自动从 `service_port_start` 选空闲端口；`exclude_container` 让重建时忽略旧 AdGuard 自己占用的端口。容器内 DNS 固定监听 53，Docker 的 `dns:` 选项不支持自定义端口；需要给局域网设备用时，再在安装弹窗里添加「宿主端口 → 容器 53（TCP/UDP）」。
  - AdGuard 与加速应用加入独立 Docker 网络 `onetap-accel`（自动挑选未占用的 `172.31.x.0/24` 子网），AdGuard 使用固定容器 IP；重建时优先沿用 `hosts_optimizer_adguard_dns_ip`，避免已开启加速的应用指向旧地址。
  - OneTap 后端容器会自动接入 `onetap-accel`，并用 AdGuard 的**容器 IP**（`http://<dns-ip>:80`）同步 DNS 重写；宿主机 IP 变化不影响同步，不需要重启容器。`overview` 额外返回按当前宿主机 IP 计算的 `web_url`，只用于浏览器「打开管理」。
  - 初始化/重建 AdGuard 时会把上游 DNS 统一切到国内可达的 DoH（`https://doh.pub/dns-query`、`https://1.12.12.12/dns-query`，bootstrap `223.5.5.5`、`119.29.29.29`），并清空缓存。默认的 Quad9 DoH 在国内不可达，会让每次非重写查询等约 4 秒再落到明文备用 DNS。注意：国内 DoH 的 RFC 8484 wire 格式仍可能返回污染结果（实测 `www.google.com` 被解析到 Facebook IP），只能保证「探测 + 证书校验通过的加速域名」不被污染；需要全局干净解析只能走代理。
  - 直接改写应用自己的 Compose 文件（单一文件源），不再生成覆盖文件：开启时把 `dns:` 与 `networks:` 写进主文件并生成一个新的 Compose 版本（`comment` 为「OneTap 网络加速：注入 DNS」），关闭时按记录还原并生成「移除 DNS」版本。被改动前的原始值记录在顶层扩展字段 `x-onetap-accel` 中，重复开启沿用它，避免把已注入的 DNS 当成用户原值；旧版 `docker-compose.onetap-dns.yml` 会在下次开启/关闭时自动删除并把 `config_files` 收敛为单一文件。`dns:` 依次为 AdGuard 静态 IP、服务原有 DNS 和兜底 DNS（`223.5.5.5`、`119.29.29.29`）：Docker 只在 AdGuard 超时不可达时才使用兜底地址，避免单点故障导致应用完全无法解析域名。
  - 加速偏好按稳定的实例名保存在 `system_config.hosts_optimizer_accel_instances`。应用部署任务完成后会自动恢复该偏好，因此自动化方案的「修复/重新部署」、重新安装都会重新写入 DNS 覆盖；自动化方案里的每个子应用同样在弹窗中按需添加，`overview.apps` 会带上 `orchestration_group_id` / `orchestration_group_name` / `orchestration_name` 供前端分组。
  - 开启/关闭都会执行一次 `docker compose up -d` 重启该应用；服务使用 `network_mode: host/service:/container:` 时会被跳过并给出提示。
- 接口：`GET /api/settings/hosts-optimizer/acceleration`、`POST .../acceleration/apps/{instance_id}`、`POST .../acceleration/adguard/deploy`、`DELETE .../acceleration/adguard`、`POST .../managed-rules/sync`。
- `POST .../acceleration/adguard/deploy` 立即返回部署任务 ID（`adguard_deploy` 跟踪任务，不占并发槽位），部署与初始化在后台执行；应用市场子任务的进度按 10%→80% 折算，AdGuard 初始化占 85%，前端接入统一的「任务进度」弹窗，部署完成后刷新网络优化页。
- 删除托管 AdGuard 会先关闭所有已开启加速的应用并恢复原网络配置，再把数据目录移入 `<应用数据目录>/_backups`，然后删除 Compose 项目与网络，最后清空 `hosts_optimizer_adguard_*` 中的地址/端口配置；账号密码保留，重新部署时从空数据全新初始化。
- 旧版自建 Compose 的 `<应用数据目录>/adguard/{work,conf}` 在首次迁移时复制到应用市场目录后删除，避免用户删除 AdGuard 后重新部署又从旧目录复制回过期密码和配置。
- 重新部署会复用已有的 `work/conf` 数据（DNS 重写、过滤规则不丢）。启动容器前先把系统配置里的 AdGuard 账号密码哈希写回 `AdGuardHome.yaml`（按安装弹窗的 conf 挂载路径确定，内置兜底用 `<应用数据目录>/adguard/conf`），避免旧数据密码与系统配置不一致导致 401/403。后端容器没有挂载宿主机目录时，通过 Docker 临时辅助容器（复用本地已有镜像）读写文件，不需要额外挂载；配置写回后已用 AdGuard `--check-config` 验证。系统配置里的密码为空时会生成新密码并写回数据。运行期间在「网络优化」里修改托管实例的账号密码并保存时，`PUT /api/settings/hosts-optimizer/config` 会按容器挂载反查配置文件、写入新凭据并重启容器（`hosts_acceleration_service.apply_managed_credentials`）。
- 加速域名规则由配套管理端统一维护：OneTap 从 `GET {MANAGE_BASE_URL}/api/public/dns-rules` 拉取，缓存到 `system_config.hosts_optimizer_managed_rules`；每次优选前自动同步一次，管理端暂不可用时使用缓存。规则不再下发指定解析 IP，统一由客户端探测最快 IP（`tier`/`answers` 仅为旧版兼容字段）。
- 管理端仓库 `nas-deck-manage` 提供「DNS 加速」页面与 `/api/dns-rules` CRUD，公共接口 `/api/public/dns-rules` 只返回启用中的免费规则；这些规则同时会编译进 Mihomo 订阅的分流规则。独立的 HAProxy DNS 加速节点已下线，两台 VPS 只作为 Mihomo（VLESS Reality）出口节点。
- 节点侧 sing-box 使用同一份规则生成白名单路由（`sniff` 后按域名/IP 匹配，`final: block`），只转发管理端规则命中的目标；规则增删会自动重发所有已发布节点。
- 自动部署需要先在「存储配置」中设置应用数据目录。走应用市场部署时数据位于 `<应用数据目录>/onetap-adguard/adguardhome/{work,conf}`；旧版自建 Compose 的 `<应用数据目录>/adguard/{work,conf}` 会在首次重新部署时自动复制过去。
- 测试：`backend/tests/system/test_hosts_acceleration.py`。

## 应用商店图标规则

- 内置/本地应用图标统一放在 `frontend/public/icons/apps/<app-name>.svg`，Vite 构建时会自动复制到 `dist/icons/apps/`。
- 前端统一使用 `frontend/src/pages/apps/AppIcon.tsx` 渲染图标：
  - 当 `app.icon` 为 `http/https` 外链或以 `/` 开头的本地路径时直接使用；
  - 其他情况（空值或管理端的图标标识）一律回退到 `/icons/apps/${app.name}.svg`。
- 管理端下发的图标会覆盖本地 `apps.icon`，更换图标无需修改数据库，按应用名放置/替换 svg 文件即可。

## 应用客户端与认证检测

应用在自动化编排（`orchestrations`）中的认证检测，由各应用专属客户端提供，不要写成独立的认证检测模块。

### 后端

- 应用客户端统一放在 `backend/app/services/apps/`，每个应用一个子包：

  ```text
  services/apps/
  ├── base.py              # AppClient 抽象基类、AuthVerifyResult、InspectReport 等
  ├── registry.py          # app_name -> client_class 注册表
  └── <app>/
      ├── __init__.py
      ├── client.py        # 继承 AppClient（组合各 mixin），实现 verify_auth，并 register_client
      ├── inspection.py    # 可选：检测项/自动修复（inspect / apply_fix）
      └── integration.py   # 可选：业务 API 集成（如 MoviePilot 系统设置/插件）
  ```

- `verify_auth(config: dict)` 接收编排实例中保存的认证配置（`url`、`auth_type`、`username`、`password`、`api_key` 等），返回 `AuthVerifyResult(valid, message)`。
- 认证方式枚举统一为 `none` / `basic` / `api_key`（历史数据里的 `apikey` 客户端均兼容）。编排实例的 `app_configs` 里账号密码与 API Key 可以同时存在，`auth_type` 只标记当前生效的一套；切换认证方式或保存配置都不会清除另一套凭据，账号自动配置也只在 `auth_type` 为空或 `none` 时补成 `basic`，不覆盖用户显式选择的 `api_key`。
- 统一检测入口为 `POST /api/orchestrations/auth/verify`，由 `orchestration_service.verify_app_auth()` 根据 `app_name` 调度对应客户端。
- 新增应用时，只需新增客户端子包并注册，无需修改 API 路由或 Service 调度逻辑。
- 当前已注册客户端：`moviepilot`、`qbittorrent`、`jellyfin`（`emby` 复用）、`fnos-media`、`comic-auto`、`komga`、`kavita`、`music-dl`、`music-tag-web`、`navidrome`、`reader`、`ermao-library`。
- `fnos-media`（飞牛影视）是 fnOS 自带的外部服务：没有官方 API 文档，客户端使用 Web 端私有接口（`/v/api/v1`，请求需 `authx` 签名；新版登录在 `/api/v2/user/loginByPassword`，旧版回退 `/v/api/v1/login`）。它不会被平台部署，`provision` 只验证用户填写的账号密码可登录（不改密码），验证通过后标记 `provisioned`，供后续装配使用；外部服务补配置时优先使用用户保存的密码，不用系统统一密码覆盖。

### 应用检测/优化与自动装配

- `AppClient.inspect(ctx)` / `apply_fix(ctx, item_ids)` 提供每应用检测项与一键修复（qBittorrent 优化项、Jellyfin 库/Webhook、MoviePilot 装配完整性）；调度入口为 `GET/POST /api/orchestrations/instances/{id}/apps/{app}/inspect[/fix]`。
- 部署/修复后 `WiringMixin._wire_group` 按编排模板 `app_composition` 条目里的 `integrations` 声明自动装配跨应用集成（当前：把 qBittorrent、Jellyfin/Emby、飞牛影视写入 MoviePilot 的 `Downloaders`/`MediaServers` 系统设置），结果落库 `group.app_configs[app]['integrations']`；手动重跑：`POST /api/orchestrations/instances/{id}/wire`。
- MoviePilot 目标可挂多个媒体服务器：`from` 列表声明的候选只装组内实际存在的那个；此外组内已配置的 `MEDIA_SERVER_APPLICATIONS`（jellyfin/emby/fnos-media）会一并装配，避免本地播放器与飞牛影视这类外部服务二选一。飞牛影视写入 `MediaServers` 时 `type` 使用 MoviePilot 的 `trimemedia`，`config` 为 `host/username/password/access_code/play_host`（`access_code` 对应飞牛启用设备访问码时的可选配置）。
- 手动重跑装配会先执行 `ProvisionMixin._reprovision_pending`：对 `app_configs` 里尚未 `provisioned` 的应用补做账号自动配置（复用统一密码和部署配置里的 `API_TOKEN`；外部服务改用用户保存的账号密码），覆盖后端重启导致后台配置协程中断的场景，然后再装配。手动重跑同样返回进度任务 ID（`POST .../wire` 的 `task_id`），前端会把它接入全局进度弹窗。
- 自动配置的等待策略（`ProvisionMixin`）：部署凭据在开始配置前先写入 `app_configs`（已有密码保持不变，仅在成功后更新为目标密码；失败时保留旧密码，保证「重新装配」能用当前有效密码登录后改密）；配置期间每 15 秒刷新一次进度并附带容器日志尾行（`PROVISION_HEARTBEAT_SECONDS`），避免 MoviePilot 首次启动下载浏览器内核等长耗时场景被误判超时；首次失败后若容器仍在运行，按 `PROVISION_RETRY_DELAYS`（60/180/300 秒）自动重试。
- 需要容器内执行命令的应用（如 Music Tag Web 免费版改密）通过 `AppClient.provision(command_runner=...)` 使用调度方注入的回调，客户端本身不依赖 Docker SDK；回调实现为 `ProvisionMixin._make_command_runner`（`container.exec_run`）。
- 客户端首次启动超时：MoviePilot 首次启动会下载 200MB+ 浏览器内核，`PROVISION_TIMEOUT` 为 3600 秒；Jellyfin 首次初始化数据库较慢，为 1800 秒。服务暂时返回 5xx（如 Jellyfin `503`）时继续轮询而不是抛出；Jellyfin 的 `/Startup/User` 在服务未初始化完成时可能返回 404，此时不能视为向导已完成，必须重试。
- 部署/修复/恢复后的自动配置与装配通过独立的进度跟踪任务（`orchestration_provision`）推送：`_schedule_provisioning` 创建任务并把 ID 追加到部署响应的 `task_ids`，前端进度弹窗依次展示「等待部署 → 配置账号 → 自动装配 → 完成」。跟踪任务不占用并发部署槽位（`create_task(..., enforce_limit=False)`），完成时刷新实例详情，因此恢复后无需再手动点「重新装配」。
- MoviePilot 插件管理：`GET/POST /api/orchestrations/instances/{id}/apps/moviepilot/plugins[/install]`。
- 前端：`AppConfigPanel` 分 Tab（概览/检测优化/自动整理/自动同步/集成），应用头部提供「编排日志」按钮（复用 `StackLogsDialog` 查看该应用 Compose 项目日志），检测面板 `AppInspectPanel`、MoviePilot 集成面板 `MoviePilotIntegrationPanel`、Music Tag Web 自动整理面板 `MusicAutoOrganizePanel`、Reader 自动同步面板 `ReaderAutoSyncPanel` 均在 `pages/automation/`。

### MCP 聚合

- 基于 `fastmcp==2.14.7` 在 FastAPI 进程内提供唯一 Streamable HTTP 端点 `/mcp`，实现位于 `services/mcp/`。协议使用独立 ASGI 路由，不能经过 `StandardResponse` 包装；主 lifespan 必须显式启停 MCP session manager。
- 原生上游目前支持 MoviePilot：自动从实例和 `app_configs` 解析地址/API Key，连接 `{url}/api/v1/mcp`，按 `moviepilot_<instance_id>_<原生工具名>` 暴露。携带凭据的上游只允许使用实例中持久化的 URL，不能根据请求 Host 生成目标；禁止自动跟随重定向，客户端 Authorization/Cookie 不透传给上游。远程发现并发执行，带总超时、短缓存和失败退避；本地工具优先调用，不枚举远程上游；实例删除后回收挂载及旧权限配置。
- `IntegrationConfig(kind='mcp')` 保存全局开关、每实例工具白名单、本地工具开关和 OneTap API `api_operation_ids`。默认全局关闭、上游只读、API 操作白名单为空；空白名单表示不开放任何工具，订阅等写工具需管理员在「系统设置 → MCP 聚合」中显式开启。`tools/list` 与 `tools/call` 都执行同一套服务端校验。
- 本地工具包括媒体目标查询、音乐整理状态/触发、Reader 同步状态/触发，以及 `onetap_api_catalog` / `onetap_api_request`。前者查询运行时完整 OpenAPI 目录和按需裁剪的 Schema；后者只按 operationId 调用已授权操作，不接受任意 URL、method 或请求头。调用使用短时内存 JWT 经 ASGI 执行原路由及权限依赖，响应递归脱敏并限制大小；认证、秘密查看、MCP 控制面、SSE、文件、WebSocket 和容器 exec 仅展示、不可调用。
- 管理端 `/api/settings/mcp/catalog` 统一展示全部 OneTap API、本地工具和远程工具，含参数详情、授权与实际生效状态；前端默认展示可搜索、筛选和分页的完整接口目录。完整展示不代表默认授权。
- 外部客户端使用独立的 `otmcp_*` 管理员 Token（Authorization Bearer），不能使用登录 JWT 或 URL query token。数据库只保存 SHA-256 哈希，支持过期和撤销；调用审计在 `mcp_tool_call_logs`，API 调用记录为 `api:<operationId>`，不保存参数和结果正文。
- 测试位于 `backend/tests/mcp/`，覆盖真实 FastMCP 上游聚合、白名单直接调用拦截、请求头隔离、OpenAPI 全量目录、真实 API 调用和 ASGI 协议。FastMCP/Pydantic/Uvicorn 依赖变化后仍需按 `docker/base.Dockerfile` 重建基础镜像。

### 漫画自动化（Comic Auto + Komga/Kavita）

- 编排模板为 `comics-stack`（`category=漫画`），组合为 `comic-auto`（必选）+ `komga` / `kavita`（可选，`group=阅读器`）。分组名由管理端填写、原样展示，不再做英文映射。
- 应用数据由配套管理端（onetap-manage）下发并缓存到本地 `apps` 表；图标在 `frontend/public/icons/apps/<name>.svg`。
- 应用与编排模板数据均由配套管理端（onetap-manage）下发：后端通过 `.env` 的 `MANAGE_BASE_URL` 获取地址，定时从 `GET {MANAGE_BASE_URL}/api/public/apps` 与 `GET {MANAGE_BASE_URL}/api/public/automations` 拉取并 upsert 到本地 `apps` / `app_orchestrations` 表作为只读缓存（实现见 `services/catalog_sync.py`）。
- 编排模板按管理端 `/api/public/automations` 返回数组的位置持久化 `sort_order`，仅调整顺序也会同步；列表 API 按 `sort_order, id` 返回。分类统一使用管理端填写的中文值，同版本分类变化也会同步。远端缺失的模板保留记录但标记 `is_listed=false`，从目录隐藏且重新下发时恢复，实例详情仍可按名称读取。侧边栏与自动化分类卡片按组合分类首次出现的顺序动态生成，展示元数据集中在 `frontend/src/lib/automation-categories.ts`，不再由前端固定分类列表决定入口。
- 共享存储：`comics` 宿主机目录统一挂载到各容器 `/mnt/media`；Comic Auto 下载目录 `/mnt/media/down` 与 Komga / Kavita 媒体库目录一致，缓存目录 `/mnt/media/cache`，新章节下载后可直接被媒体库扫描入库。
- 账号自动配置：Comic Auto 通过 `/api/v1/app/init/setup/` 创建管理员；Komga 通过 `/api/v1/claim` claim 服务器并创建 API Key；Kavita 通过 `/api/Account/register` 注册首个管理员，登录接口返回 API Key。
- 自动装配：`comic-auto` 条目的 `integrations` 声明 Komga/Kavita 为 `mediaserver`，部署后由 `ComicAutoClient.setup_mediaserver` 写入媒体服务器、下载目录与缓存目录；结果落在 `group.app_configs['comic-auto']['integrations']`。Comic Auto 使用账号密码换取 JWT 完成装配，因此客户端标记 `requires_api_key = False`。

### 音乐自动化（music-dl + Music Tag Web + Navidrome）

- 编排模板为 `music-auto`（`category=音乐`），组合为 `music-dl`（下载）+ `music-tag-web`（整理）+ `navidrome`（可选播放器）。共享卷约定：`downloads` 下载、`library` 曲库、`playlists` 歌单。
- 账号自动配置：music-dl 首次启动会在容器日志输出一次性 `Web setup token`，`MusicDLClient` 解析后调用 `POST /music/setup` 创建管理员；已初始化时直接用账号密码登录。music-dl 没有改密接口，配置卷复用时若统一密码与旧密码不一致，会明确失败并提示手动重置或重新部署数据卷。
- 账号自动配置：Music Tag Web 内置默认管理员 `admin/admin`，`MusicTagWebClient` 先尝试统一密码，失败后回退默认账号；免费版（V1）的用户管理接口返回 403，改密通过容器内 `python manage.py shell` 执行 Django `set_password`（由 `ProvisionMixin._make_command_runner` 注入的命令回调完成），随后用新密码复验。
- 账号自动配置：Navidrome 首次启动没有管理员，`NavidromeClient` 通过 `POST /auth/createAdmin` 创建；已有管理员时登录后用 `PUT /api/user/{id}` 同步统一密码。
- 目录自动配置：从实例组 `shared_config.volumes` 的容器挂载路径推导 music-dl 下载目录（优先 `/app/media/downloads`，其次 `/mnt/music/downloads`），登录后通过 `POST /music/settings` 写入 `downloadDir`，结果落库 `group.app_configs['music-dl']['download_dir']`，前端概览页展示。Music Tag Web 与 Navidrome 的曲库目录由共享卷和 `ND_MUSICFOLDER` 提供，无需额外写入应用设置。
- 认证检测：`MusicDLClient` / `MusicTagWebClient` / `NavidromeClient` 均使用账号密码登录校验，不支持 API Key。
- 自动刮削/整理（集成在 OneTap 内）：免费版 V1 不开放后台刮削配置接口（`/apimt/sys/background_scrape_configs/` 返回 403），但网页端单次操作使用的 `/apimt/batch_auto_update_id3/` 与 `/apimt/tidy_folder/` 可用。实现在 `services/orchestration/music_auto.py`，通过 `MusicTagWebSession` 调用这两个接口，整理后可选触发 Navidrome `startScan`；不修改应用镜像。
  - 配置保存在 `group.app_configs['music-tag-web']['auto_organize']`（目录、间隔、音乐源、写入字段、目录层级等），由 `main.py` lifespan 启动的后台调度器按间隔执行，也可从自动化页手动触发。
  - 目录默认从应用实例的真实挂载推导（优先 `/app/media/downloads` 与 `/app/media/music`），在 `AppConfigPanel` 的「自动整理」Tab 中编辑。
  - 文件处理状态与失败退避存于 `music_auto_organize_files` 表；接口为 `GET/PUT /api/orchestrations/instances/{id}/apps/music-tag-web/auto-organize` 与 `POST .../auto-organize/run`。
  - 定时触发使用 `force=False`：新发现的文件按 Music Tag Web 返回的 mtime（用容器 `TZ` 解释）判断是否已稳定，已放够 `min_age_seconds` 的文件当轮处理，仍在写入的文件留到下一轮；手动触发 `force=True` 会立即处理。
  - 旧的 `scripts/music_auto_pipeline.py` 与 cron 方案已由上述服务取代，避免重复处理同一批文件。

### 书籍自动化（Reader + 二毛读书）

- 编排模板为 `book-auto`（`category=书籍`），组合为 `reader`（书源/书架）+ `ermao-library`（书库）。
- 自动同步（集成在 OneTap 内）：原先由 `onetap-manage/apps/reader/3.2.14` 镜像内置的 Book Bridge 移植到后端，不再需要构建/推送改造镜像。实现在 `services/orchestration/reader_auto.py`：
  - `ReaderSession` 调用 Reader 的 `/reader3/getShelfBookWithCacheInfo`、`getChapterList`、`getBookContent`、`exportBook`，补齐缺失章节后导出 EPUB；
  - `ErmaoClient` 负责二毛的初始化、登录、书库列表/创建、`/api/libraries/{id}/scan` 与导入任务轮询；
  - `fix_epub`（`services/apps/reader/epub.py`）转义裸 `&`、剔除非法 XML 控制字符，并按 `mimetype` 首条且 STORED 重新打包。
- 账号自动配置：`ReaderAppClient` 在部署时通过 `reader_env` 注入 `READER_APP_SECURE=true` 开启登录认证，首次调用 `/reader3/login`（`isLogin=false`）注册管理员，已有账号直接登录。Reader 每次登录都会轮换 `accessToken`，因此只落库用户名/密码，不保存令牌；自动同步每次用账号密码重新登录。统一密码变化时会先尝试用旧密码登录并调用 `/reader3/resetPassword`，未配置 `READER_APP_SECUREKEY` 时该管理接口不可用，会提示手动重置密码或重新部署数据卷。
- 账号自动配置：`ErmaoLibraryClient` 通过 `/api/auth/setup` 创建首个管理员（邮箱密码），已初始化时登录并用 `/api/auth/account/password` 同步统一密码；普通用户名映射为 `<用户名>@example.com`，实际生效的邮箱写入 `app_configs`。
- 自动同步始终以各应用认证设置中的地址和凭据为准（`reader` 的用户名/密码、`ermao-library` 的邮箱/密码），不在面板重复填写，历史 `auto_sync` 中的旧认证信息不会覆盖最新配置。
- 同步策略与目录保存在 `group.app_configs['reader']['auto_sync']`，由 `main.py` lifespan 启动的后台调度器按间隔执行，也可从自动化页手动触发。
  - 书库目录与二毛书库目录默认从运行中容器的真实挂载推导（Docker 不可用时回退到实例配置），在 `AppConfigPanel` 的「自动同步」Tab 中可一键填入或手动修改；应用地址和账号在各自的认证设置中维护。
  - 首次运行遇到二毛尚未初始化时，可用认证设置中的邮箱/密码自动创建管理员和书库（`auto_setup`）；已有账号时直接登录，失败会提示检查认证设置。
  - 书籍导出状态与失败退避存于 `reader_auto_sync_books` 表；接口为 `GET/PUT /api/orchestrations/instances/{id}/apps/reader/auto-sync` 与 `POST .../auto-sync/run`。
  - 后端需要能写到书库的宿主机目录（当前 PM2 部署直接可写；Docker 部署需把该目录按相同路径挂载进 `app` 容器）。
  - 旧的 `apps/reader/3.2.14` 镜像与 `BRIDGE_*` 环境变量不再参与运行，Reader 使用官方 `hectorqin/reader:latest` 即可。

### 前端

- 通用检测 Hook 为 `useVerifyAppAuth`，接口类型定义在 `frontend/src/api/orchestrations.ts`（hooks 层 re-export）。
- `AutomationImportDialog` 和 `AppConfigPanel` 已内置检测按钮，新增应用无需改动 UI。
- 认证配置是否可检测，统一通过 `frontend/src/pages/automation/auth-config-utils.ts` 中的 `isAuthConfigReady()` 判断。

## 关键约定

- **注释语言**：中文（docstring + `#` 行内注释）
- **Python 环境**：开发环境使用 `backend/.venv/bin/python`（Linux）或 `backend/.venv/Scripts/python.exe`（Windows）；运行命令需设置 `PYTHONPATH=./app`
- **数据库**：PostgreSQL + asyncpg 异步驱动，默认 `DATABASE_URL=postgresql+asyncpg://onetap:onetap@localhost:5434/onetap`（见 `.env`）；测试使用内存 SQLite（`sqlite+aiosqlite:///:memory:`），不影响开发库。
  - **改表结构 / 新增表**：写 Alembic migration，然后执行 `alembic upgrade head`。
  - **修改数据**（如应用商店默认配置、内置应用模板、实例配置）：直接改数据库或写初始化/修复脚本，**不要生成 Alembic migration**。
- **认证**：JWT Bearer token（HS256），**第一个**注册用户自动成为 `admin`，后续注册用户为普通 `user`；token 有效期 24 小时
- **CORS**：开发环境 `allow_origins=["*"]`
- **路径别名**：前端 `@/` 映射到 `src/`
- **Vite 代理**：开发时 `/api` 代理到 `http://localhost:5001`
- **前端请求约定**：组件/hooks **禁止直接 import `@/lib/api` 发请求**，必须在 `src/api/` 对应领域文件中新增/复用封装函数；新增接口时类型与后端 Pydantic schema 保持对齐（含字段可选性）
- **React Query**：hooks 中 mutation 的 `onSuccess` 自动调用 `invalidateQueries` 刷新缓存；容器/Compose 项目列表 10 秒轮询，Stack 状态 30 秒轮询
- **Docker 管理**：可选功能，后端通过 `docker_manager.available` 检测可用性，不可用时安全降级
- **Compose 管理**：通过 `docker compose` CLI 操作；`compose_service` 维护项目级异步锁防止并发操作冲突
- **Toast 通知**：前端使用自定义 `ToastProvider`（`components/ui/toast.tsx`），通过 `useToast()` 调用
- **页面拆分**：复杂页面按功能拆分为子组件，放在同目录下；Docker 跨页面共享组件在 `docker/shared/`
- **业务下沉**：新增业务逻辑优先放到 `services/` 对应领域子包；跨领域共享的系统级能力（系统配置、存储路径、端口分配）放 `services/system/`，`core/` 只保留与业务无关的基础设施
