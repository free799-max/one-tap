# syntax=docker/dockerfile:1
#
# 应用镜像：OneTap 基础镜像 + 后端代码 + 前端产物。
# 基础镜像由 docker/base.Dockerfile 手动构建并推送到 Harbor，
# 这里只拉取使用；系统依赖或 Python 依赖变化时重建基础镜像，不要在本文件里装。

# 基础镜像（手动构建推送，见 docker/base.Dockerfile）
ARG BASE_IMAGE=harbor.cacty.top/library/onetap-base:latest
# 前端构建镜像，取自 Harbor 的 baseimg 镜像，不访问 docker.io
ARG NODE_IMAGE=harbor.cacty.top/baseimg/node:22-alpine

# 1. 构建前端静态文件
FROM ${NODE_IMAGE} AS frontend-build

WORKDIR /build
RUN npm install --global pnpm@10
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

# 2. 运行镜像：基础镜像 + 后端代码 + 前端产物，Nginx 对外提供前端与 /api 反代
#    FastAPI 仅监听回环地址
FROM ${BASE_IMAGE} AS runtime

# 应用版本：CI 用版本标签注入，客户端安装上报与管理端展示使用。
ARG APP_VERSION=dev
ENV APP_VERSION=${APP_VERSION}

# 只复制运行时文件，避免把本地的 backend/data、测试和虚拟环境带进镜像
COPY backend/app ./app
COPY backend/alembic ./alembic
COPY backend/alembic.ini ./
COPY --from=frontend-build /build/dist /usr/share/nginx/html
COPY docker/nginx.conf /etc/nginx/conf.d/default.conf
COPY docker/entrypoint.sh /usr/local/bin/onetap-entrypoint
RUN chmod +x /usr/local/bin/onetap-entrypoint \
    && mkdir -p /data

EXPOSE 80
STOPSIGNAL SIGTERM
ENTRYPOINT ["/usr/local/bin/onetap-entrypoint"]
