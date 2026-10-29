# syntax=docker/dockerfile:1
#
# OneTap 基础镜像：系统依赖、Docker CLI 与 Python 依赖。
# 只在本地手动构建并推送，GitLab CI 不构建它，只把 onetap-base 当作 FROM 基础镜像。
#
# 在仓库根目录执行：
#   docker build -f docker/base.Dockerfile -t harbor.cacty.top/library/onetap-base:latest .
#   docker push harbor.cacty.top/library/onetap-base:latest
#
# 系统包或 backend/requirements.txt 变化后重新执行上面两条。

# 上游镜像统一取 Harbor 的 baseimg 镜像，不访问 docker.io
ARG PY_BASE_IMAGE=harbor.cacty.top/baseimg/python:3.12-slim
ARG DOCKER_CLI_IMAGE=harbor.cacty.top/baseimg/docker:27-cli

# Docker CLI 与 Compose 插件：应用通过挂载的 docker.sock 管理宿主机容器
FROM ${DOCKER_CLI_IMAGE} AS docker-cli

FROM ${PY_BASE_IMAGE}

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# 默认使用国内镜像源；海外环境可通过 --build-arg 覆盖
ARG DEBIAN_MIRROR=https://mirrors.aliyun.com
RUN sed -i "s|http://deb.debian.org|${DEBIAN_MIRROR}|g" /etc/apt/sources.list.d/debian.sources \
    && apt-get update \
    && apt-get install -y --no-install-recommends nginx tzdata \
    && rm -rf /var/lib/apt/lists/* /etc/nginx/sites-enabled/default

WORKDIR /app
COPY --from=docker-cli /usr/local/bin/docker /usr/local/bin/docker
COPY --from=docker-cli /usr/local/libexec/docker/cli-plugins /usr/local/libexec/docker/cli-plugins

ARG PIP_INDEX_URL=https://mirrors.aliyun.com/pypi/simple/
COPY backend/requirements.txt ./
RUN pip install --index-url "${PIP_INDEX_URL}" -r requirements.txt
