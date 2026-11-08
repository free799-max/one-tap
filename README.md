<div align="center">

<img src="frontend/public/favicon.svg" width="80" alt="OneTap" />

<h1>OneTap</h1>

<p><strong>面向 NAS 的开源 Docker 管理平台</strong><br />
统一管理容器、镜像与 Compose 编排，一键部署常用应用和整套自动化工作流。</p>

<p>
  <a href="https://github.com/free799-max/one-tap/releases"><img src="https://img.shields.io/github/v/tag/free799-max/one-tap?style=flat-square&color=7c3aed&label=version" alt="version" /></a>
  <a href="https://hub.docker.com/r/free5104/one-tap"><img src="https://img.shields.io/docker/pulls/free5104/one-tap?style=flat-square&color=7c3aed" alt="docker pulls" /></a>
  <a href="https://github.com/free799-max/one-tap/stargazers"><img src="https://img.shields.io/github/stars/free799-max/one-tap?style=flat-square&color=7c3aed" alt="stars" /></a>
  <img src="https://img.shields.io/badge/platform-x86__64-7c3aed?style=flat-square" alt="platform" />
</p>

<p>
  <a href="#-快速部署">快速部署</a> ·
  <a href="#-功能">功能</a> ·
  <a href="#-界面预览">界面预览</a> ·
  <a href="#-本地开发">本地开发</a> ·
  <a href="#-更多文档">文档</a>
</p>

</div>

---

OneTap 把 Docker 管理、应用市场、自动化组合与服务集成放进同一个界面：应用市场一键安装常用应用；自动化组合可以一次拉起影视、漫画、书籍、音乐、刷流等整套工作流，并自动完成应用间的联动配置；反向代理、DDNS、证书与密码库也能在应用集成里统一管理。

> **支持范围**：目前仅支持 **x86**，运行环境为 Linux、飞牛 fnOS、群晖等标准 Docker 环境；ARM 设备尚未测试。

## ✨ 功能

- **服务概览**：按分类汇总应用卡片、运行状态、资源占用与访问入口，点击即可打开服务。
- **应用市场**：内置 NAS 应用模板，表单化配置端口、存储、环境变量与设备映射，部署前可预览最终 Compose。
- **自动化组合**：影视、漫画、书籍、音乐、刷流整套方案一键部署，共享存储与环境变量，自动分配端口并完成应用间账号、下载器与媒体库的装配；支持导入已有部署与按方案升级。
- **应用集成**：Lucky 反向代理与 DDNS、Hosts 优选、Vaultwarden 密码库，把已部署应用统一接入。
- **Docker 管理**：容器、Compose 编排、镜像与宿主机状态管理，日志与终端直接在页面打开。
- **基础能力**：统一服务账号、存储目录映射、服务端口自动分配、部署任务进度与实时日志。

## 🖼️ 界面预览

| 服务概览 | 应用市场 |
| :---: | :---: |
| <a href="docs/images/readme/dashboard.png"><img src="docs/images/readme/dashboard.png" width="100%" alt="服务概览" /></a> | <a href="docs/images/readme/appstore.png"><img src="docs/images/readme/appstore.png" width="100%" alt="应用市场" /></a> |
| 应用状态、资源占用与访问入口 | 应用模板一键安装 |

| 自动化组合 | 部署向导 |
| :---: | :---: |
| <a href="docs/images/readme/automation-index.png"><img src="docs/images/readme/automation-index.png" width="100%" alt="自动化组合" /></a> | <a href="docs/images/readme/automation-deploy-2.png"><img src="docs/images/readme/automation-deploy-2.png" width="100%" alt="部署向导" /></a> |
| 影视、漫画、书籍、音乐、刷流整套方案 | 共享配置、逐应用配置与 Compose 预览 |

| 网络集成 | 认证集成 |
| :---: | :---: |
| <a href="docs/images/readme/integrations-network.png"><img src="docs/images/readme/integrations-network.png" width="100%" alt="网络集成" /></a> | <a href="docs/images/readme/integrations-auth.png"><img src="docs/images/readme/integrations-auth.png" width="100%" alt="认证集成" /></a> |
| 反向代理、DDNS 与域名（敏感信息已打码） | Vaultwarden 密码库同步 |

| 容器管理 | 编排管理 |
| :---: | :---: |
| <a href="docs/images/readme/docker-containers.png"><img src="docs/images/readme/docker-containers.png" width="100%" alt="容器管理" /></a> | <a href="docs/images/readme/docker-stacks.png"><img src="docs/images/readme/docker-stacks.png" width="100%" alt="编排管理" /></a> |
| 容器状态、端口与快捷操作 | Compose 项目、服务数量与启停 |

## 🚀 快速部署

OneTap 由应用容器与 PostgreSQL 16 数据库组成，一条 Docker Compose 即可运行。不需要 `.env`，也不用提前创建 `data`、`appdata`、`postgres` 等目录。

下面以飞牛 fnOS 为例。进入「Docker → 创建项目」：

- **项目名称**：`one-tap`
- **路径**：选择一个存储空间下的目录，例如 `存储空间 1/我的文件/docker/one-tap`
- **来源**：选择「创建 docker-compose.yml」，粘贴下面的内容
- 勾选「创建项目后立即启动」，点击「确认」

```yaml
name: onetap

services:
  postgres:
    container_name: onetap-postgres
    image: postgres:16-alpine
    restart: unless-stopped
    environment:
      POSTGRES_DB: onetap
      POSTGRES_USER: onetap
      POSTGRES_PASSWORD: onetap9527
      TZ: Asia/Shanghai
    volumes:
      - ./postgres:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U onetap -d onetap"]
      interval: 5s
      timeout: 5s
      retries: 20
      start_period: 10s

  app:
    container_name: onetap-app
    image: ccr.ccs.tencentyun.com/one-tap/one-tap:latest
    restart: unless-stopped
    init: true
    stop_grace_period: 30s
    depends_on:
      postgres:
        condition: service_healthy
    ports:
      - "6782:80"
    environment:
      APP_NAME: OneTap
      TZ: Asia/Shanghai
      DATA_ROOT: /data
      DATABASE_URL: postgresql+asyncpg://onetap:onetap9527@postgres:5432/onetap
    volumes:
      - ./data:/data
      - ./appdata:/appdata
      - /var/run/docker.sock:/var/run/docker.sock
```

启动完成后访问 `http://<主机地址>:6782`。端口 `6782` 可以按需修改，冒号右侧的 `80` 是容器内端口，不要改动。**首个注册的账号自动成为管理员**，注册成功后注册入口会自动关闭。

镜像也可以换成 Docker Hub 的 `free5104/one-tap:latest`，两者同步发布；需要固定版本时把 `latest` 换成对应版本标签。

### 挂载说明

| 挂载 | 作用 |
|---|---|
| `./data:/data` | OneTap 自身数据：Compose 工作区、应用图标、登录签名密钥 |
| `./postgres:/var/lib/postgresql/data` | 数据库：账号、系统设置、应用实例与部署记录 |
| `./appdata:/appdata` | 应用数据目录：各应用配置、附件与缓存 |
| `/vol2/1000/OneTap:/mnt/hdd-1` | 业务数据目录（可选）：视频、音乐、下载等大文件 |

> **安全提示**：必须挂载 `/var/run/docker.sock`，否则 OneTap 无法部署应用和管理 Docker。这等同于把宿主机 root 权限交给容器，**请勿直接暴露到公网**，远程访问建议使用 VPN。

### 升级

```bash
docker compose pull
docker compose up -d
```

数据库迁移会在启动时自动执行。升级前建议备份 `data`、`appdata` 与 `postgres` 三个目录，**不要删除 `postgres`**。需要固定版本时，先修改 `docker-compose.yml` 中的镜像标签再执行上述命令。

## 🛠️ 本地开发

| 层级 | 技术 |
|---|---|
| 后端 | Python 3.12 · FastAPI · SQLAlchemy 2.0（异步） |
| 前端 | React 19 · TypeScript · Vite · Tailwind CSS · shadcn/ui |
| 数据库 | PostgreSQL 16 · Alembic 迁移 |
| 部署 | Docker Compose，前端静态文件与 FastAPI 合并到同一应用容器 |

### 后端

```bash
# 启动 PostgreSQL（映射宿主机 5434 端口）
docker run -d --name onetap-db -e POSTGRES_USER=onetap \
  -e POSTGRES_PASSWORD=onetap -e POSTGRES_DB=onetap \
  -p 5434:5432 postgres:16-alpine

cd backend
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt

# 开发服务器：http://localhost:5001
PYTHONPATH=./app .venv/bin/python -m uvicorn app.main:app --reload --host 0.0.0.0 --port 5001

# 测试与数据库迁移
PYTHONPATH=./app .venv/bin/python -m pytest
PYTHONPATH=./app .venv/bin/python -m alembic upgrade head
```

### 前端

```bash
cd frontend
pnpm install
pnpm run dev      # http://localhost:5000，代理 /api -> localhost:5001
pnpm run build    # TypeScript 编译 + Vite 构建
pnpm run lint     # ESLint 检查
```

## 📚 更多文档

- [整体架构设计](docs/整体架构设计.md)
- [API 统一响应格式规范](docs/api/api-response-spec.md)
- [CLAUDE.md](CLAUDE.md)：架构分层、开发约定、异步部署任务、应用商店维护等开发细节
- [问题反馈](https://github.com/free799-max/one-tap/issues)
