#!/usr/bin/env bash
# OneTap 加速节点接入脚本。
#
# 由管理端生成命令后，在 VPS 上执行：
#   curl -fsSL <管理端地址>/api/acceleration/agent/install.sh | sudo bash -s -- \
#     --token <令牌> --base <管理端地址> --api-port 8787 --vless-port 8443 --sni www.microsoft.com
#
# 脚本会安装 sing-box、生成本地 Reality 密钥并启动 onetap-node-agent。
# Reality 私钥只写在 /etc/onetap-node/config.json，权限 0600，不会上报。

set -euo pipefail

TOKEN=""
BASE=""
API_PORT="8787"
VLESS_PORT="8443"
SNI="www.microsoft.com"
SINGBOX_VERSION="1.14.4"

while [[ $# -gt 0 ]]; do
  case "$1" in
    --token) TOKEN="$2"; shift 2 ;;
    --base) BASE="$2"; shift 2 ;;
    --api-port) API_PORT="$2"; shift 2 ;;
    --vless-port) VLESS_PORT="$2"; shift 2 ;;
    --sni) SNI="$2"; shift 2 ;;
    --singbox-version) SINGBOX_VERSION="$2"; shift 2 ;;
    *) echo "未知参数：$1" >&2; exit 2 ;;
  esac
done

if [[ -z "$TOKEN" ]]; then
  echo "缺少 --token" >&2
  exit 2
fi
if [[ "$(id -u)" -ne 0 ]]; then
  echo "请使用 root 或 sudo 执行" >&2
  exit 2
fi

NODE_DIR="/etc/onetap-node"
AGENT_DIR="/opt/onetap-node"
SINGBOX_BIN="/usr/local/bin/sing-box"

arch="$(uname -m)"
case "$arch" in
  x86_64|amd64) SB_ARCH="amd64" ;;
  aarch64|arm64) SB_ARCH="arm64" ;;
  *) echo "不支持的架构：$arch" >&2; exit 2 ;;
esac

install_singbox() {
  if command -v sing-box >/dev/null 2>&1; then
    cp -f "$(command -v sing-box)" "$SINGBOX_BIN"
    return
  fi
  local archive="sing-box-${SINGBOX_VERSION}-linux-${SB_ARCH}.tar.gz"
  local tmp
  tmp="$(mktemp -d)"
  local mirrors=(
    "https://gh-proxy.com/https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}"
    "https://ghfast.top/https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}"
    "https://github.com/SagerNet/sing-box/releases/download/v${SINGBOX_VERSION}"
  )
  for mirror in "${mirrors[@]}"; do
    echo "下载 sing-box：${mirror}/${archive}"
    if curl -fsSL --connect-timeout 15 --max-time 180 -o "$tmp/$archive" "$mirror/$archive"; then
      tar -xzf "$tmp/$archive" -C "$tmp"
      local bin
      bin="$(find "$tmp" -type f -name sing-box | head -1)"
      if [[ -n "$bin" ]]; then
        install -m 0755 "$bin" "$SINGBOX_BIN"
        rm -rf "$tmp"
        return
      fi
    fi
  done
  rm -rf "$tmp"
  echo "sing-box 下载失败，请手动安装后重试" >&2
  exit 1
}

install_agent() {
  mkdir -p "$AGENT_DIR"
  if [[ -n "$BASE" ]]; then
    curl -fsSL --connect-timeout 15 --max-time 60 \
      -o "$AGENT_DIR/onetap_node_agent.py" \
      "${BASE%/}/api/acceleration/agent/onetap_node_agent.py"
  fi
  if [[ ! -s "$AGENT_DIR/onetap_node_agent.py" ]]; then
    echo "缺少 agent 源码，请通过 --base 指定可访问的管理端地址" >&2
    exit 1
  fi
  chmod 0755 "$AGENT_DIR/onetap_node_agent.py"
}

write_config() {
  mkdir -p "$NODE_DIR"
  chmod 0700 "$NODE_DIR"
  "$SINGBOX_BIN" generate reality-keypair > "$NODE_DIR/.keypair"
  private_key="$(sed -n 's/^PrivateKey:[[:space:]]*//p' "$NODE_DIR/.keypair")"
  public_key="$(sed -n 's/^PublicKey:[[:space:]]*//p' "$NODE_DIR/.keypair")"
  rm -f "$NODE_DIR/.keypair"
  if [[ -z "$private_key" || -z "$public_key" ]]; then
    echo "生成 Reality 密钥失败" >&2
    exit 1
  fi
  short_id="$("$SINGBOX_BIN" generate rand --hex 8)"

  if [[ -f "$NODE_DIR/config.json" ]]; then
    private_key="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("private_key") or "")' "$NODE_DIR/config.json")"
    public_key="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("public_key") or "")' "$NODE_DIR/config.json")"
    short_id="$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1])).get("short_id") or "")' "$NODE_DIR/config.json")"
  fi

  python3 - "$NODE_DIR/config.json" "$TOKEN" "$API_PORT" "$VLESS_PORT" "$SNI" "$private_key" "$public_key" "$short_id" <<'PY'
import json
import sys

path, token, api_port, vless_port, sni, private_key, public_key, short_id = sys.argv[1:]
data = {
    "token": token,
    "api_port": int(api_port),
    "vless_port": int(vless_port),
    "sni": sni,
    "private_key": private_key,
    "public_key": public_key,
    "short_id": short_id,
}
with open(path, "w", encoding="utf-8") as handle:
    json.dump(data, handle, ensure_ascii=False, indent=2)
PY
  chmod 0600 "$NODE_DIR/config.json"
}

write_bootstrap_config() {
  if [[ -f "$NODE_DIR/sing-box.json" ]]; then
    return
  fi
  bootstrap_uuid="$(python3 -c 'import uuid;print(uuid.uuid4())')"
  python3 - "$NODE_DIR/sing-box.json" "$NODE_DIR/config.json" "$bootstrap_uuid" <<'PY'
import json
import sys

target, node_path, user_uuid = sys.argv[1:]
node = json.load(open(node_path, encoding="utf-8"))
config = {
    "log": {"level": "warn", "timestamp": True},
    "inbounds": [{
        "type": "vless",
        "tag": "vless-in",
        "listen": "0.0.0.0",
        "listen_port": node["vless_port"],
        "users": [{"uuid": user_uuid, "flow": "xtls-rprx-vision"}],
        "tls": {
            "enabled": True,
            "server_name": node["sni"],
            "reality": {
                "enabled": True,
                "handshake": {"server": node["sni"], "server_port": 443},
                "private_key": node["private_key"],
                "short_id": [node["short_id"]],
            },
        },
    }],
    "outbounds": [{"type": "direct", "tag": "direct"}],
}
with open(target, "w", encoding="utf-8") as handle:
    json.dump(config, handle, ensure_ascii=False, indent=2)
PY
  chmod 0600 "$NODE_DIR/sing-box.json"
}

write_units() {
  cat > /etc/systemd/system/onetap-singbox.service <<EOF
[Unit]
Description=OneTap sing-box
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=$SINGBOX_BIN run -c $NODE_DIR/sing-box.json
Restart=always
RestartSec=3
LimitNOFILE=1048576

[Install]
WantedBy=multi-user.target
EOF

  cat > /etc/systemd/system/onetap-node-agent.service <<EOF
[Unit]
Description=OneTap Node Agent
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
Environment=ONETAP_NODE_DIR=$NODE_DIR
ExecStart=/usr/bin/python3 $AGENT_DIR/onetap_node_agent.py
Restart=always
RestartSec=3

[Install]
WantedBy=multi-user.target
EOF

  systemctl daemon-reload
}

open_firewall() {
  if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q "Status: active"; then
    ufw allow "$API_PORT/tcp" >/dev/null
    ufw allow "$VLESS_PORT/tcp" >/dev/null
  fi
}

main() {
  install_singbox
  write_config
  write_bootstrap_config
  install_agent
  write_units
  open_firewall
  systemctl enable --now onetap-singbox >/dev/null 2>&1 || systemctl restart onetap-singbox
  systemctl enable --now onetap-node-agent >/dev/null 2>&1 || systemctl restart onetap-node-agent
  sleep 2
  echo "sing-box：$(systemctl is-active onetap-singbox)"
  echo "agent：$(systemctl is-active onetap-node-agent)"
  echo "管理端口：$API_PORT，VLESS 端口：$VLESS_PORT，SNI：$SNI"
  echo "$("$SINGBOX_BIN" version | head -1)"
}

main
