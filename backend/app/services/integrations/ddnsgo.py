"""DDNS-Go 客户端。

DDNS-Go 没有 JSON 配置读取接口，配置会渲染进首页 HTML 的
``reloadConf("...")`` 调用中，保存则通过 ``POST /save`` 提交完整配置。
因此这里解析首页来读取现有配置，合并 OneTap 自己的条目后再整体保存，
避免覆盖用户已有的 DDNS 配置。
"""

import html as html_lib
import json
import re
from dataclasses import dataclass, field

import httpx

from app.core.exceptions import APIException


ONETAP_DDNS_NAME = "OneTap"

IPV4_URL = (
    "https://api.ipify.org, https://ddns.oray.com/checkip, "
    "https://ip.3322.net, https://4.ipw.cn"
)
IPV6_URL = (
    "https://api64.ipify.org, https://speed.neu6.edu.cn/getIP.php, "
    "https://v6.ident.me, https://6.ipw.cn"
)

_RELOAD_RE = re.compile(r'reloadConf\("((?:[^"\\]|\\.)*)"\);', re.S)
_RELOAD_RAW_RE = re.compile(r'reloadConf\((\[.*\])\);', re.S)
_LANG_RE = re.compile(r'window\.__DDNS_GO_LANG__\s*=\s*"([^"]*)"')
_VERSION_RE = re.compile(
    r'<span class="badge badge-secondary">([^<]*)</span>'
)


class DdnsGoError(APIException):
    """DDNS-Go 调用失败。"""


@dataclass
class DdnsGoConfig:
    """从首页解析出的完整配置。"""

    global_conf: dict = field(default_factory=dict)
    dns_conf: list = field(default_factory=list)
    version: str | None = None


class DdnsGoClient:
    """DDNS-Go Web API 客户端。"""

    TIMEOUT = 20.0

    def __init__(self, base_url: str, username: str, password: str):
        url = (base_url or "").strip().rstrip("/")
        if not url:
            raise DdnsGoError("请填写 DDNS-Go 地址", 400)
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        self.base_url = url
        self.username = (username or "").strip()
        self.password = password or ""
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "DdnsGoClient":
        self._client = httpx.AsyncClient(
            timeout=self.TIMEOUT,
            follow_redirects=True,
            trust_env=False,
        )
        return self

    async def __aexit__(self, *_args) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        if self._client is None:
            raise RuntimeError("DdnsGoClient 必须在 async with 中使用")
        return self._client

    async def login(self) -> None:
        if not self.username or not self.password:
            raise DdnsGoError("请填写 DDNS-Go 用户名和密码", 400)
        try:
            response = await self.client.post(
                f"{self.base_url}/loginFunc",
                json={"Username": self.username, "Password": self.password},
            )
        except httpx.HTTPError as exc:
            raise DdnsGoError(f"无法连接 DDNS-Go：{exc}", 502) from exc

        data = _json_or_none(response)
        if response.status_code >= 400:
            raise DdnsGoError(
                f"DDNS-Go 登录失败（HTTP {response.status_code}）", 502
            )
        if not isinstance(data, dict) or data.get("Code") != 200:
            message = ""
            if isinstance(data, dict):
                message = str(data.get("Msg") or "")
            raise DdnsGoError(message or "DDNS-Go 用户名或密码错误", 401)
        token = data.get("Data")
        if isinstance(token, str) and token:
            self.client.cookies.set("token", token)

    async def test(self) -> str | None:
        await self.login()
        config = await self.fetch_config()
        return config.version

    async def fetch_config(self) -> DdnsGoConfig:
        try:
            response = await self.client.get(f"{self.base_url}/")
        except httpx.HTTPError as exc:
            raise DdnsGoError(f"无法连接 DDNS-Go：{exc}", 502) from exc
        if response.status_code >= 400:
            raise DdnsGoError(
                f"DDNS-Go 读取配置失败（HTTP {response.status_code}）", 502
            )
        return parse_config(response.text)

    async def save_config(
        self, global_conf: dict, dns_conf: list
    ) -> None:
        payload = {
            **global_conf,
            "Password": "",
            "DnsConf": dns_conf,
        }
        try:
            response = await self.client.post(
                f"{self.base_url}/save", json=payload
            )
        except httpx.HTTPError as exc:
            raise DdnsGoError(f"无法连接 DDNS-Go：{exc}", 502) from exc

        data = _json_or_none(response)
        if response.status_code >= 400:
            raise DdnsGoError(
                f"DDNS-Go 保存失败（HTTP {response.status_code}）", 502
            )
        result = data.get("result") if isinstance(data, dict) else None
        if result != "ok":
            raise DdnsGoError(
                str(result or "DDNS-Go 保存配置失败"), 400
            )


def _json_or_none(response: httpx.Response) -> dict | None:
    try:
        data = response.json()
    except ValueError:
        return None
    return data if isinstance(data, dict) else None


def _input_value(page: str, field_id: str) -> str:
    match = re.search(
        r'<input[^>]*\bid="' + re.escape(field_id) + r'"[^>]*>', page
    )
    if not match:
        return ""
    tag = match.group(0)
    value = re.search(r'\bvalue="([^"]*)"', tag)
    return html_lib.unescape(value.group(1)) if value else ""


def _input_checked(page: str, field_id: str) -> bool:
    match = re.search(
        r'<input[^>]*\bid="' + re.escape(field_id) + r'"[^>]*>', page
    )
    return bool(match and re.search(r'\bchecked\b', match.group(0)))


def _textarea_value(page: str, field_id: str) -> str:
    match = re.search(
        r'<textarea[^>]*\bid="' + re.escape(field_id) + r'"[^>]*>(.*?)</textarea>',
        page,
        re.S,
    )
    return html_lib.unescape(match.group(1)) if match else ""


def parse_config(page: str) -> DdnsGoConfig:
    """解析 DDNS-Go 首页，提取全局配置与 DNS 配置列表。"""
    if 'id="formDnsConf"' not in page:
        raise DdnsGoError(
            "DDNS-Go 登录失败或返回了登录页，请检查账号密码", 401
        )

    text = ""
    match = _RELOAD_RE.search(page)
    if match:
        try:
            text = json.loads(f'"{match.group(1)}"')
        except json.JSONDecodeError:
            text = ""
    if not text:
        raw_match = _RELOAD_RAW_RE.search(page)
        if not raw_match:
            raise DdnsGoError("DDNS-Go 页面结构不兼容，未找到配置数据", 502)
        text = raw_match.group(1)
    try:
        dns_conf = json.loads(text)
    except json.JSONDecodeError as exc:
        raise DdnsGoError("DDNS-Go 配置数据解析失败", 502) from exc
    if not isinstance(dns_conf, list):
        raise DdnsGoError("DDNS-Go 配置数据格式异常", 502)

    lang_match = _LANG_RE.search(page)
    version_match = _VERSION_RE.search(page)
    global_conf = {
        "Username": _input_value(page, "Username"),
        "Password": "",
        "Lang": lang_match.group(1) if lang_match else "",
        "NotAllowWanAccess": _input_checked(page, "NotAllowWanAccess"),
        "WebhookURL": _input_value(page, "WebhookURL"),
        "WebhookRequestBody": _textarea_value(page, "WebhookRequestBody"),
        "WebhookHeaders": _textarea_value(page, "WebhookHeaders"),
    }
    return DdnsGoConfig(
        global_conf=global_conf,
        dns_conf=dns_conf,
        version=(version_match.group(1).strip() if version_match else None),
    )


def build_entry(
    *,
    enabled: bool,
    provider: str,
    access_id: str,
    secret: str,
    ipv4: dict | None = None,
    ipv6: dict | None = None,
) -> dict:
    """构造 OneTap 专属的 DDNS-Go 配置项。

    DDNS-Go 的 DNS 服务商与凭据是条目级共用的；IPv4 / IPv6 只各自
    控制是否启用、取 IP 方式和域名。
    """
    ipv4 = ipv4 or {}
    ipv6 = ipv6 or {}
    v4_enabled = enabled and bool(ipv4.get("enabled"))
    v6_enabled = enabled and bool(ipv6.get("enabled"))
    v4_get_type = ipv4.get("get_type") or "url"
    v6_get_type = ipv6.get("get_type") or "url"
    v4_url = (ipv4.get("url") or "").strip() or IPV4_URL
    v6_url = (ipv6.get("url") or "").strip() or IPV6_URL
    v4_domains = "\n".join(ipv4.get("domains") or []) if v4_enabled else ""
    v6_domains = "\n".join(ipv6.get("domains") or []) if v6_enabled else ""
    return {
        "Name": ONETAP_DDNS_NAME,
        "DnsName": provider,
        "DnsID": access_id,
        "DnsSecret": secret,
        "DnsExtParam": "",
        "TTL": "",
        "HttpInterface": "",
        "Ipv4Enable": v4_enabled,
        "Ipv4GetType": v4_get_type,
        "Ipv4Url": v4_url,
        "Ipv4NetInterface": ipv4.get("net_interface") or "",
        "Ipv4Cmd": "",
        "Ipv4Domains": v4_domains,
        "Ipv6Enable": v6_enabled,
        "Ipv6GetType": v6_get_type,
        "Ipv6Url": v6_url,
        "Ipv6NetInterface": ipv6.get("net_interface") or "",
        "Ipv6Cmd": "",
        "Ipv6Reg": ipv6.get("ip_reg") or "",
        "Ipv6Domains": v6_domains,
    }


def find_entry(entries: list) -> dict | None:
    for item in entries:
        if isinstance(item, dict) and item.get("Name") == ONETAP_DDNS_NAME:
            return item
    return None


def merge_entry(entries: list, entry: dict, *, create: bool = True) -> list:
    """替换同名条目；不存在时按需追加，其余条目原样保留。"""
    merged = list(entries)
    for index, item in enumerate(merged):
        if isinstance(item, dict) and item.get("Name") == ONETAP_DDNS_NAME:
            merged[index] = {**item, **entry}
            return merged
    if create:
        merged.append(entry)
    return merged


def remove_entry(entries: list) -> list:
    return [
        item
        for item in entries
        if not (isinstance(item, dict) and item.get("Name") == ONETAP_DDNS_NAME)
    ]
