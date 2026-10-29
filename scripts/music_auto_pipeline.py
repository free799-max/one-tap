#!/usr/bin/env python3
"""Music Tag Web 自动刮削 + 整理流水线。

监控 music-dl 的下载目录，发现新音频后调用 Music Tag Web 的接口完成：

1. 自动刮削（``/apimt/batch_auto_update_id3/``）
2. 按 ``艺人/专辑`` 整理到音乐库（``/apimt/tidy_folder/``）
3. 通知 Navidrome 扫描（Subsonic ``startScan``）

这些接口就是 Music Tag Web 网页端「自动刮削」「整理文件」按钮使用的接口，
免费 V1 版同样允许调用；后台刮削配置接口（``/apimt/sys/background_scrape_configs/``）
才受 V1 限制。

默认从 OneTap 数据库读取 music-tag-web / navidrome 的地址与账号密码，
也可以通过环境变量覆盖。建议用 cron 每 5 分钟触发一次，脚本自带文件锁，
上一轮没跑完时下一轮会直接退出。

环境变量:
    ONETAP_DATABASE_URL   OneTap 数据库，默认 postgresql://onetap:onetap@localhost:5432/onetap
    MUSIC_DOWNLOAD_DIR     宿主机下载目录，默认 /opt/data/data1/music-auto/downloads
    MUSIC_LIBRARY_DIR      宿主机音乐库目录（仅用于日志），默认 /opt/data/data1/music-auto/library
    MTW_DOWNLOAD_DIR       下载目录在 Music Tag Web 容器内的路径，默认 /app/media/downloads
    MTW_LIBRARY_DIR        音乐库在 Music Tag Web 容器内的路径，默认 /app/media/music
    MTW_URL / MTW_USERNAME / MTW_PASSWORD      直接指定 Music Tag Web 连接信息
    ND_URL / ND_USERNAME / ND_PASSWORD         直接指定 Navidrome 连接信息
    MTW_STATE              状态文件，默认 ~/.local/state/onetap/music-auto.json
    MTW_MIN_AGE            文件至少静置多少秒才处理，默认 120
"""

from __future__ import annotations

import argparse
import asyncio
import fcntl
import json
import logging
import os
import sys
import time
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx

logger = logging.getLogger("music-auto")

AUDIO_EXTENSIONS = {
    ".mp3",
    ".flac",
    ".m4a",
    ".aac",
    ".wav",
    ".ape",
    ".ogg",
    ".opus",
    ".wma",
    ".dsf",
    ".dff",
    ".dts",
    ".wv",
    ".aiff",
    ".aif",
    ".alac",
    ".tta",
}

# 自动刮削参数，与网页端默认值一致
SCRAPE_PARAMS: dict[str, Any] = {
    "select_mode": "simple",
    "source_list": ["netease", "qmusic"],
    "modify_list": ["artist", "album", "album_img", "year", "lyrics"],
    "is_skip_tag": True,
    "is_folder_album": False,
    "scrape_rate": 1,
}

TIDY_DIRS = [
    {"value": "artist", "value2": ""},
    {"value": "album", "value2": ""},
]

DEFAULT_DSN = "postgresql://onetap:onetap@localhost:5432/onetap"
AUDIT_POLL_INTERVAL = 2.0
SCRAPE_TIMEOUT = 600.0
TIDY_TIMEOUT = 120.0


@dataclass
class Endpoint:
    url: str
    username: str
    password: str


@dataclass
class Settings:
    mtw: Endpoint
    navidrome: Endpoint | None
    download_dir: Path
    library_dir: Path
    container_download_dir: str
    container_library_dir: str
    state_path: Path
    min_age: int


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(value: datetime) -> str:
    return value.isoformat()


def _loads_maybe_double(value: Any) -> Any:
    """OneTap 的 app_configs 可能是 JSON 字符串，也可能被转义了两次。"""
    for _ in range(2):
        if isinstance(value, str):
            value = json.loads(value)
        else:
            break
    return value


async def _load_config_from_db() -> dict[str, Any]:
    import asyncpg  # 延迟导入，--help / 纯环境变量模式不需要它

    dsn = os.environ.get("ONETAP_DATABASE_URL", DEFAULT_DSN).replace(
        "postgresql+asyncpg://", "postgresql://"
    )
    conn = await asyncpg.connect(dsn)
    try:
        row = await conn.fetchrow(
            "select app_configs from app_orchestration_instances "
            "where instance_name = 'music-auto' order by id desc limit 1"
        )
    finally:
        await conn.close()
    if not row:
        raise RuntimeError("OneTap 数据库中没有找到 music-auto 编排实例")
    configs = _loads_maybe_double(row["app_configs"])
    if not isinstance(configs, dict):
        raise RuntimeError("music-auto 的 app_configs 格式不正确")
    return configs


async def load_settings(args: argparse.Namespace) -> Settings:
    configs: dict[str, Any] = {}
    need_db = not (os.environ.get("MTW_PASSWORD") and os.environ.get("MTW_URL"))
    if need_db:
        configs = await _load_config_from_db()

    def endpoint(name: str, prefix: str) -> Endpoint | None:
        raw = configs.get(name) or {}
        url = os.environ.get(f"{prefix}_URL") or raw.get("url")
        username = os.environ.get(f"{prefix}_USERNAME") or raw.get("username")
        password = os.environ.get(f"{prefix}_PASSWORD") or raw.get("password")
        if not (url and username and password):
            return None
        return Endpoint(str(url).rstrip("/"), str(username), str(password))

    mtw = endpoint("music-tag-web", "MTW")
    if mtw is None:
        raise RuntimeError(
            "缺少 Music Tag Web 连接信息：请设置 MTW_URL/MTW_USERNAME/MTW_PASSWORD，"
            "或确认 OneTap 中 music-auto 已完成自动装配"
        )
    navidrome = endpoint("navidrome", "ND")

    if args.state:
        state_path = Path(args.state).expanduser()
    else:
        state_path = Path(
            os.environ.get("MTW_STATE", "~/.local/state/onetap/music-auto.json")
        ).expanduser()

    return Settings(
        mtw=mtw,
        navidrome=navidrome,
        download_dir=Path(
            os.environ.get(
                "MUSIC_DOWNLOAD_DIR", "/opt/data/data1/music-auto/downloads"
            )
        ),
        library_dir=Path(
            os.environ.get(
                "MUSIC_LIBRARY_DIR", "/opt/data/data1/music-auto/library"
            )
        ),
        container_download_dir=os.environ.get(
            "MTW_DOWNLOAD_DIR", "/app/media/downloads"
        ).rstrip("/"),
        container_library_dir=os.environ.get(
            "MTW_LIBRARY_DIR", "/app/media/music"
        ).rstrip("/"),
        state_path=state_path,
        min_age=args.min_age,
    )


class StateStore:
    """记录每个文件是否已经处理过，避免重复刮削。"""

    def __init__(self, path: Path) -> None:
        self.path = path
        self.data: dict[str, Any] = {"version": 1, "files": {}}
        self._load()

    def _load(self) -> None:
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            logger.warning("状态文件损坏，忽略: %s", self.path)
            return
        if isinstance(raw, dict) and isinstance(raw.get("files"), dict):
            self.data = raw

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(".tmp")
        tmp.write_text(
            json.dumps(self.data, ensure_ascii=False, indent=1), encoding="utf-8"
        )
        tmp.replace(self.path)

    @property
    def files(self) -> dict[str, Any]:
        return self.data["files"]

    def get(self, key: str) -> dict[str, Any] | None:
        entry = self.files.get(key)
        return entry if isinstance(entry, dict) else None

    def should_process(self, key: str, size: int, mtime: float, force: bool) -> bool:
        entry = self.get(key)
        if force or entry is None:
            return True
        if entry.get("size") != size or entry.get("mtime") != mtime:
            return True  # 同名文件被重新下载/覆盖
        if entry.get("status") == "done":
            return False
        next_retry = entry.get("next_retry_at")
        if entry.get("status") == "failed" and next_retry:
            try:
                if _now() < datetime.fromisoformat(next_retry):
                    return False
            except ValueError:
                pass
        return True

    def mark_done(self, key: str, size: int, mtime: float) -> None:
        self.files[key] = {
            "size": size,
            "mtime": mtime,
            "status": "done",
            "updated_at": _iso(_now()),
        }

    def mark_failed(self, key: str, size: int, mtime: float, error: str) -> None:
        entry = self.get(key) or {}
        tries = int(entry.get("tries", 0)) + 1
        delay = min(3600, 300 * (2 ** min(tries - 1, 4)))
        self.files[key] = {
            "size": size,
            "mtime": mtime,
            "status": "failed",
            "tries": tries,
            "last_error": error[:500],
            "updated_at": _iso(_now()),
            "next_retry_at": _iso(_now() + timedelta(seconds=delay)),
        }

    def prune(self, download_dir: Path) -> None:
        """清掉已经不在下载目录、且完成超过 7 天的记录。"""
        cutoff = _now() - timedelta(days=7)
        for key, entry in list(self.files.items()):
            if Path(key).exists():
                continue
            updated = entry.get("updated_at")
            try:
                if not updated or datetime.fromisoformat(updated) < cutoff:
                    del self.files[key]
            except ValueError:
                del self.files[key]


class MusicTagWeb:
    def __init__(self, endpoint: Endpoint) -> None:
        self.endpoint = endpoint
        self.client = httpx.Client(
            base_url=endpoint.url, timeout=60.0, follow_redirects=True
        )
        self.token: str | None = None

    def close(self) -> None:
        self.client.close()

    @property
    def headers(self) -> dict[str, str]:
        if not self.token:
            raise RuntimeError("尚未登录 Music Tag Web")
        return {"Authorization": f"JWT {self.token}"}

    def login(self) -> None:
        resp = self.client.post(
            "/apimt/token/",
            json={
                "username": self.endpoint.username,
                "password": self.endpoint.password,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("result") or not payload.get("token"):
            raise RuntimeError(f"Music Tag Web 登录失败: {payload.get('message')}")
        self.token = str(payload["token"])

    def latest_audit_id(self) -> int:
        resp = self.client.get(
            "/apimt/audit_log/",
            headers=self.headers,
            params={"page": 1, "page_size": 1},
        )
        resp.raise_for_status()
        items = (resp.json().get("data") or {}).get("items") or []
        return int(items[0]["id"]) if items else 0

    def _wait_audit(self, action: str, after_id: int, timeout: float) -> dict[str, Any]:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            resp = self.client.get(
                "/apimt/audit_log/",
                headers=self.headers,
                params={"page": 1, "page_size": 20},
            )
            resp.raise_for_status()
            items = (resp.json().get("data") or {}).get("items") or []
            for item in items:
                if int(item.get("id", 0)) <= after_id:
                    continue
                if item.get("action") != action:
                    continue
                extra = str(item.get("extra") or "")
                # 0 个文件时后台任务不会写 complete_at，按终态处理
                if item.get("complete_at") or "共0个文件" in extra:
                    return item
            time.sleep(AUDIT_POLL_INTERVAL)
        raise TimeoutError(f"等待「{action}」完成超时")

    def scrape(self, container_dir: str, filename: str) -> dict[str, Any]:
        after_id = self.latest_audit_id()
        params = dict(SCRAPE_PARAMS)
        body = {
            "file_full_path": container_dir,
            "select_data": [
                {
                    "checked": True,
                    "icon": "icon-script-file",
                    "name": filename,
                    "title": filename,
                }
            ],
            **params,
            "music_info": params,
        }
        resp = self.client.post(
            "/apimt/batch_auto_update_id3/", headers=self.headers, json=body
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("result"):
            raise RuntimeError(f"创建刮削任务失败: {payload.get('message')}")
        return self._wait_audit("自动刮削", after_id, SCRAPE_TIMEOUT)

    def tidy(
        self, container_dir: str, filename: str, container_library_dir: str
    ) -> dict[str, Any]:
        after_id = self.latest_audit_id()
        body = {
            "file_full_path": container_dir,
            "select_data": [
                {
                    "checked": True,
                    "icon": "icon-script-file",
                    "name": filename,
                    "title": filename,
                }
            ],
            "root_path": container_library_dir,
            "dir_list": TIDY_DIRS,
        }
        resp = self.client.post(
            "/apimt/tidy_folder/", headers=self.headers, json=body
        )
        resp.raise_for_status()
        payload = resp.json()
        if not payload.get("result"):
            raise RuntimeError(f"创建整理任务失败: {payload.get('message')}")
        return self._wait_audit("整理文件夹", after_id, TIDY_TIMEOUT)


class Navidrome:
    def __init__(self, endpoint: Endpoint) -> None:
        self.endpoint = endpoint
        self.client = httpx.Client(
            base_url=endpoint.url, timeout=30.0, follow_redirects=True
        )

    def close(self) -> None:
        self.client.close()

    def start_scan(self) -> None:
        resp = self.client.post(
            "/auth/login",
            json={
                "username": self.endpoint.username,
                "password": self.endpoint.password,
            },
        )
        resp.raise_for_status()
        payload = resp.json()
        params = {
            "u": self.endpoint.username,
            "t": payload.get("subsonicToken", ""),
            "s": payload.get("subsonicSalt", ""),
            "v": "1.16.1",
            "c": "onetap-music-auto",
            "f": "json",
        }
        resp = self.client.get("/rest/startScan", params=params)
        resp.raise_for_status()
        result = resp.json().get("subsonic-response", {})
        if result.get("status") != "ok":
            raise RuntimeError(f"Navidrome 扫描请求失败: {result}")


def discover_files(download_dir: Path, min_age: int) -> list[dict[str, Any]]:
    now = time.time()
    found: list[dict[str, Any]] = []
    if not download_dir.is_dir():
        logger.warning("下载目录不存在: %s", download_dir)
        return found
    for path in download_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.name.startswith("."):
            continue
        if path.suffix.lower() not in AUDIO_EXTENSIONS:
            continue
        try:
            stat = path.stat()
        except OSError:
            continue
        if now - stat.st_mtime < min_age:
            logger.debug("文件还在写入，跳过: %s", path)
            continue
        found.append(
            {
                "host_path": path,
                "size": stat.st_size,
                "mtime": stat.st_mtime,
            }
        )
    return found


def cleanup_empty_dirs(root: Path) -> None:
    """删除下载目录里整理后留下的空子目录。"""
    if not root.is_dir():
        return
    for path in sorted(root.rglob("*"), key=lambda p: len(p.parts), reverse=True):
        if not path.is_dir():
            continue
        try:
            path.rmdir()
        except OSError:
            pass


def audit_succeeded(item: dict[str, Any]) -> tuple[bool, str]:
    extra = str(item.get("extra") or "")
    if item.get("status") == "success" and "失败:0" in extra:
        return True, extra
    if "刮削完成" in extra and "失败:" not in extra:
        return True, extra
    return False, extra or "刮削未成功"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dry-run", action="store_true", help="只打印计划，不调用接口")
    parser.add_argument("--force", action="store_true", help="忽略状态文件重新处理")
    parser.add_argument(
        "--min-age",
        type=int,
        default=int(os.environ.get("MTW_MIN_AGE", "120")),
        help="文件静置秒数",
    )
    parser.add_argument("--max-files", type=int, default=20, help="单轮最多处理文件数")
    parser.add_argument("--state", help="自定义状态文件路径")
    parser.add_argument("--verbose", action="store_true")
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    settings = asyncio.run(load_settings(args))
    state_path = settings.state_path
    state_path.parent.mkdir(parents=True, exist_ok=True)
    lock_path = state_path.with_suffix(".lock")

    lock_file = lock_path.open("w")
    try:
        fcntl.flock(lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        logger.info("上一轮还在运行，退出")
        return 0

    store = StateStore(state_path)
    store.prune(settings.download_dir)

    files = discover_files(settings.download_dir, settings.min_age)
    pending = [
        item
        for item in files
        if store.should_process(
            str(item["host_path"]), item["size"], item["mtime"], args.force
        )
    ][: args.max_files]

    if not pending:
        logger.info("没有需要处理的文件")
        return 0

    logger.info(
        "发现 %d 个待处理文件（下载目录 %s，曲库 %s）",
        len(pending),
        settings.download_dir,
        settings.library_dir,
    )
    if args.dry_run:
        for item in pending:
            logger.info("[dry-run] %s", item["host_path"])
        return 0

    mtw = MusicTagWeb(settings.mtw)
    navidrome = Navidrome(settings.navidrome) if settings.navidrome else None
    tidied = 0
    try:
        mtw.login()
        for item in pending:
            host_path: Path = item["host_path"]
            try:
                relative = host_path.relative_to(settings.download_dir)
            except ValueError:
                continue
            container_dir = str(
                Path(settings.container_download_dir) / relative.parent
            )
            if container_dir == ".":
                container_dir = settings.container_download_dir
            filename = host_path.name

            logger.info("刮削: %s", host_path)
            try:
                audit = mtw.scrape(container_dir, filename)
                ok, detail = audit_succeeded(audit)
                if not ok:
                    raise RuntimeError(detail)

                logger.info("整理: %s", host_path)
                tidy_audit = mtw.tidy(
                    container_dir, filename, settings.container_library_dir
                )
                if "完成" not in str(tidy_audit.get("extra") or ""):
                    raise RuntimeError(
                        f"整理未完成: {tidy_audit.get('extra') or ''}"
                    )
                if host_path.exists():
                    raise RuntimeError("整理后源文件仍存在")

                store.mark_done(str(host_path), item["size"], item["mtime"])
                tidied += 1
            except Exception as exc:  # noqa: BLE001 - 单个文件失败不影响后续
                logger.warning("处理失败 %s: %s", host_path, exc)
                store.mark_failed(
                    str(host_path), item["size"], item["mtime"], str(exc)
                )
            finally:
                store.save()

        cleanup_empty_dirs(settings.download_dir)

        if tidied and navidrome is not None:
            try:
                navidrome.start_scan()
                logger.info("已触发 Navidrome 扫描")
            except Exception as exc:  # noqa: BLE001
                logger.warning("触发 Navidrome 扫描失败: %s", exc)
    finally:
        mtw.close()
        if navidrome is not None:
            navidrome.close()
        store.save()

    logger.info("本轮完成：成功 %d 个", tidied)
    return 0


if __name__ == "__main__":
    sys.exit(main())
