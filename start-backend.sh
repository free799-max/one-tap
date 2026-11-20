#!/bin/bash
# 本机开发备选方案（配合 pm2，见 ecosystem.config.js）：正式部署请使用 docker-compose（见 README.md）。
# 依赖 backend/.venv 虚拟环境，首次使用请先执行：cd backend && python -m venv .venv && .venv/bin/pip install -r requirements.txt
set -e

BACKEND_DIR="$(cd "$(dirname "$0")/backend" && pwd)"
export PYTHONPATH="$BACKEND_DIR"

cd "$BACKEND_DIR"

# 开机时 PostgreSQL 容器还在崩溃恢复，此时连库会报 “not yet accepting connections”，
# alembic 直接失败会让 pm2 反复重启后端（前端代理表现为 502）。先等库能执行查询，
# 最多 180 秒；超时仍继续，保留原来的失败路径。
if ! "$BACKEND_DIR/.venv/bin/python" - <<'PY'
import asyncio
import sys
import time

import asyncpg
from sqlalchemy.engine import make_url

from app.config import settings

url = make_url(settings.DATABASE_URL)
deadline = time.monotonic() + 180


async def probe() -> bool:
    try:
        conn = await asyncpg.connect(
            user=url.username or "postgres",
            password=url.password or "",
            host=url.host or "localhost",
            port=url.port or 5432,
            database=url.database or "postgres",
            timeout=5,
        )
    except Exception:
        return False
    try:
        await conn.execute("select 1")
        return True
    except Exception:
        return False
    finally:
        await conn.close()


while time.monotonic() < deadline:
    if asyncio.run(probe()):
        sys.exit(0)
    time.sleep(3)
sys.exit(1)
PY
then
  echo "数据库在 180 秒内未就绪，仍然尝试执行迁移"
fi

"$BACKEND_DIR/.venv/bin/python" -m alembic upgrade head
# 保留 lifespan：数据库 reconciliation、目录同步与音乐自动整理调度器
# 都依赖启动/关闭钩子，关闭后定时任务不会运行。
# 只监听 app/ 与 alembic/：data/ 下有 Mihomo 等容器持续写入，tests/ 会被 pytest
# 改写。监听整个目录时，跑测试会触发 reload，多次 reload 叠加在启动阶段
# （alembic 迁移）会让 reloader 卡死，后端停止监听、所有请求超时。
# 部署进度页会占用 SSE 长连接，优雅关闭若一直等它断开，reload 会永远卡在
# 旧进程上；限制关闭等待时间，超时由 uvicorn 强制收尾。
exec "$BACKEND_DIR/.venv/bin/python" -m uvicorn app.main:app --reload \
  --reload-dir app --reload-dir alembic \
  --host 0.0.0.0 --port 5001 \
  --timeout-graceful-shutdown 5
