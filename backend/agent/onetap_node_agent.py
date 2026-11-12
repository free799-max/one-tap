#!/usr/bin/env python3
"""OneTap node agent。

运行在加速节点（VPS）上，负责：
- 生成并保管 Reality 私钥与 short id（私钥不离开本机）
- 接收管理端下发的 sing-box 配置，校验后落盘并重启
- 提供状态、重启与历史版本回滚接口

只依赖 Python 标准库，由 systemd 托管。
"""

from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

CONFIG_DIR = Path(os.environ.get("ONETAP_NODE_DIR", "/etc/onetap-node"))
CONFIG_FILE = CONFIG_DIR / "config.json"
SINGBOX_CONFIG = CONFIG_DIR / "sing-box.json"
STATE_FILE = CONFIG_DIR / "state.json"
HISTORY_DIR = CONFIG_DIR / "history"
SINGBOX_BIN = os.environ.get("ONETAP_SINGBOX_BIN", "/usr/local/bin/sing-box")
SERVICE = os.environ.get("ONETAP_SINGBOX_SERVICE", "onetap-singbox")
PLACEHOLDER = "__REALITY_PRIVATE_KEY__"


def load_json(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def write_json(path: Path, data: dict, mode: int = 0o600) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
    os.chmod(tmp, mode)
    tmp.replace(path)


def run(cmd: list[str], timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout)


def singbox_version() -> str:
    try:
        result = run([SINGBOX_BIN, "version"])
        first = (result.stdout or "").strip().splitlines()
        return first[0].strip() if first else ""
    except (OSError, subprocess.SubprocessError):
        return ""


def service_active() -> bool:
    try:
        return run(["systemctl", "is-active", "--quiet", SERVICE]).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def read_state() -> dict:
    return load_json(STATE_FILE)


def save_state(**values) -> dict:
    state = read_state()
    state.update(values)
    state["updated_at"] = now_iso()
    write_json(STATE_FILE, state)
    return state


def generate_reality_keypair() -> tuple[str, str]:
    """调用 sing-box 生成 Reality 密钥对。"""
    result = run([SINGBOX_BIN, "generate", "reality-keypair"])
    if result.returncode != 0:
        raise RuntimeError((result.stderr or "生成 Reality 密钥失败").strip())
    private_key = public_key = ""
    for line in result.stdout.splitlines():
        key, _, value = line.partition(":")
        key = key.strip().lower()
        value = value.strip()
        if key == "privatekey":
            private_key = value
        elif key == "publickey":
            public_key = value
    if not private_key or not public_key:
        raise RuntimeError("无法解析 sing-box 生成的 Reality 密钥")
    return private_key, public_key


def generate_short_id() -> str:
    """生成 8 字节随机 short id。"""
    result = run([SINGBOX_BIN, "generate", "rand", "--hex", "8"])
    value = (result.stdout or "").strip()
    if result.returncode == 0 and re.fullmatch(r"[0-9a-f]{16}", value):
        return value
    return os.urandom(8).hex()


def inject_private_key(config: dict, private_key: str) -> dict:
    """把私钥占位符替换为节点本地私钥（不修改传入对象）。"""
    data = copy.deepcopy(config)
    for inbound in data.get("inbounds", []):
        tls = inbound.get("tls") or {}
        reality = tls.get("reality") or {}
        if reality.get("private_key") == PLACEHOLDER:
            reality["private_key"] = private_key
    return data


def validate_config(config: dict) -> tuple[bool, str]:
    """用 sing-box check 校验配置。"""
    tmp = CONFIG_DIR / "sing-box.validate.json"
    tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), "utf-8")
    try:
        result = run([SINGBOX_BIN, "check", "-c", str(tmp)])
    finally:
        tmp.unlink(missing_ok=True)
    if result.returncode != 0:
        return False, (result.stderr or result.stdout or "配置校验失败").strip()[:2000]
    return True, ""


def backup_current(revision: int) -> None:
    """保存当前配置，便于回滚。"""
    if not SINGBOX_CONFIG.exists():
        return
    HISTORY_DIR.mkdir(parents=True, exist_ok=True)
    target = HISTORY_DIR / f"r{revision}.json"
    if not target.exists():
        shutil.copyfile(SINGBOX_CONFIG, target)


def restart_service() -> tuple[bool, str]:
    """重启 sing-box 并确认服务处于运行状态。"""
    result = run(["systemctl", "restart", SERVICE], timeout=30)
    if result.returncode != 0:
        return False, (result.stderr or "systemctl restart 失败").strip()[:2000]
    for _ in range(15):
        time.sleep(0.5)
        if service_active():
            return True, ""
    return False, "sing-box 重启后未处于运行状态"


def apply_config(config: dict, revision: int) -> tuple[bool, str]:
    """校验并应用配置，失败时恢复上一份配置。"""
    node = load_json(CONFIG_FILE)
    private_key = node.get("private_key", "")
    if not private_key:
        return False, "节点缺少 Reality 私钥"
    injected = inject_private_key(config, private_key)
    ok, error = validate_config(injected)
    if not ok:
        return False, f"配置校验失败：{error}"

    previous_revision = int(read_state().get("revision") or 0)
    backup_current(previous_revision)
    backup = SINGBOX_CONFIG.read_text("utf-8") if SINGBOX_CONFIG.exists() else ""
    write_json(SINGBOX_CONFIG, injected)

    ok, error = restart_service()
    if not ok:
        if backup:
            SINGBOX_CONFIG.write_text(backup, "utf-8")
            restart_service()
        return False, error

    save_state(
        revision=revision,
        applied_at=now_iso(),
        last_error=None,
        config_sha=hashlib.sha256(
            json.dumps(injected, sort_keys=True).encode("utf-8")
        ).hexdigest(),
    )
    return True, ""


def status_payload() -> dict:
    node = load_json(CONFIG_FILE)
    state = read_state()
    return {
        "ok": True,
        "reality_public_key": node.get("public_key", ""),
        "short_id": node.get("short_id", ""),
        "singbox_version": singbox_version(),
        "singbox_active": service_active(),
        "revision": int(state.get("revision") or 0),
        "applied_at": state.get("applied_at"),
        "last_error": state.get("last_error"),
        "vless_port": int(node.get("vless_port") or 0),
        "sni": node.get("sni", ""),
    }


class Handler(BaseHTTPRequestHandler):
    server_version = "onetap-node-agent/1.0"

    def log_message(self, fmt: str, *args) -> None:  # noqa: A003
        sys.stderr.write("[agent] " + fmt % args + "\n")

    def _authorized(self) -> bool:
        node = load_json(CONFIG_FILE)
        expected = node.get("token", "")
        header = self.headers.get("Authorization", "")
        provided = header.removeprefix("Bearer ").strip()
        return bool(expected) and hmac.compare_digest(provided, expected)

    def _send(self, code: int, payload: dict) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json(self) -> dict:
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > 2 * 1024 * 1024:
            return {}
        try:
            return json.loads(self.rfile.read(length))
        except ValueError:
            return {}

    def do_GET(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        if self.path.split("?")[0] == "/status":
            self._send(200, status_payload())
            return
        self._send(404, {"ok": False, "error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if not self._authorized():
            self._send(401, {"ok": False, "error": "unauthorized"})
            return
        path = self.path.split("?")[0]
        body = self._read_json()

        if path == "/apply":
            config = body.get("config")
            revision = int(body.get("revision") or 0)
            if not isinstance(config, dict) or revision <= 0:
                self._send(400, {"ok": False, "error": "缺少 config 或 revision"})
                return
            ok, error = apply_config(config, revision)
            if not ok:
                save_state(last_error=error)
                self._send(500, {"ok": False, "error": error})
                return
            self._send(200, {"ok": True, "revision": revision})
            return

        if path == "/rollback":
            revision = int(body.get("revision") or 0)
            target = HISTORY_DIR / f"r{revision}.json"
            if revision <= 0 or not target.exists():
                self._send(404, {"ok": False, "error": "配置版本不存在"})
                return
            try:
                config = json.loads(target.read_text("utf-8"))
            except ValueError:
                self._send(500, {"ok": False, "error": "历史配置已损坏"})
                return
            ok, error = apply_config(config, revision)
            if not ok:
                self._send(500, {"ok": False, "error": error})
                return
            self._send(200, {"ok": True, "revision": revision})
            return

        if path == "/restart":
            ok, error = restart_service()
            self._send(200 if ok else 500, {"ok": ok, "error": error})
            return

        self._send(404, {"ok": False, "error": "not found"})


def main() -> None:
    if not CONFIG_FILE.exists():
        raise SystemExit(f"缺少节点配置：{CONFIG_FILE}")
    node = load_json(CONFIG_FILE)
    port = int(node.get("api_port") or 8787)
    server = ThreadingHTTPServer(("0.0.0.0", port), Handler)
    sys.stderr.write(f"onetap node agent listening on 0.0.0.0:{port}\n")
    server.serve_forever()


if __name__ == "__main__":
    main()
