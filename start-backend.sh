#!/bin/bash
# 本机开发备选方案（配合 pm2，见 ecosystem.config.js）：正式部署请使用 docker-compose（见 README.md）。
# 依赖 backend/.venv 虚拟环境，首次使用请先执行：cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
set -e

BACKEND_DIR="$(cd "$(dirname "$0")/backend" && pwd)"
export PYTHONPATH="$BACKEND_DIR"

cd "$BACKEND_DIR"
"$BACKEND_DIR/.venv/bin/python" -m alembic upgrade head
# 保留 lifespan：数据库 reconciliation、目录同步与音乐自动整理调度器
# 都依赖启动/关闭钩子，关闭后定时任务不会运行。
# data/ 下有 Mihomo 等容器持续写入的运行时文件，必须排除，避免热重载被反复触发
exec "$BACKEND_DIR/.venv/bin/python" -m uvicorn app.main:app --reload \
  --reload-exclude 'data/*' --reload-exclude '.venv/*' \
  --host 0.0.0.0 --port 5001
