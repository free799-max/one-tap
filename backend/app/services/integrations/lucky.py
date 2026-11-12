"""Lucky 网络集成客户端。

封装 Lucky 的登录、反向代理规则与 DDNS 任务接口。
所有操作都以 OneTap 专属的规则/任务名管理，避免影响用户已有配置。
"""

import base64
import json
import os
import time
from typing import Any

import httpx
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding

from app.core.exceptions import APIException


ONETAP_RULE_NAME = "OneTap"
ONETAP_RULE_KEY_PREFIX = "onetap-port-"
ONETAP_DDNS_NAME = "OneTap 动态域名"
ONETAP_CERT_REMARK = "OneTap 自动证书"
ACME_LETSENCRYPT_DIRECTORY = "https://acme-v02.api.letsencrypt.org/directory"
# v3 主规则必须选择 IP 过滤规则，沿用旧版子规则的黑名单模式
DEFAULT_IP_FILTER_RULE = "blacklist"

DDNS_FAMILIES = ("IPv4", "IPv6")
DEFAULT_IPV4_URLS = "\n".join(
    ("https://api.ipify.org", "https://ip.3322.net", "https://4.ipw.cn")
)
DEFAULT_IPV6_URLS = "\n".join(
    ("https://api6.ipify.org", "https://v6.ident.me", "https://6.ipw.cn")
)


def ddns_task_name(family: str) -> str:
    return f"{ONETAP_DDNS_NAME} {family}"


def _is_managed_ddns(task: dict) -> bool:
    name = str(task.get("TaskName") or "")
    return name == ONETAP_DDNS_NAME or name in {
        ddns_task_name(family) for family in DDNS_FAMILIES
    }

_CERTIFICATE_SKELETON: dict[str, Any] = {
    "Key": "",
    "MappingToPath": False,
    "MappingPath": "",
    "MappingChangeScript": "",
    "Enable": True,
    "Remark": "",
    "CertBase64": "",
    "KeyBase64": "",
    "IssuerCertificate": "",
    "AddFrom": "file",
    "ExtParams": {},
    "AllSyncClient": False,
    "SyncClientList": [],
}


def certificate_remark(domains: list[str]) -> str:
    """自动证书的备注，用于在 Lucky 里找回刚创建的证书。"""
    return f"{ONETAP_CERT_REMARK} - {domains[0]}"


def build_file_certificate_payload(
    *, remark: str, cert_pem: str, key_pem: str
) -> dict:
    """上传自有证书（自签名或已有证书）给 Lucky 的 SSL 模块。"""
    return {
        **_CERTIFICATE_SKELETON,
        "Remark": remark,
        "CertBase64": base64.b64encode(cert_pem.encode()).decode(),
        "KeyBase64": base64.b64encode(key_pem.encode()).decode(),
        "AddFrom": "file",
    }


def build_acme_certificate_payload(
    *,
    domains: list[str],
    remark: str,
    email: str = "",
    validation: str = "http",
    dns_id: str = "",
    dns_secret: str = "",
    ca_directory: str = ACME_LETSENCRYPT_DIRECTORY,
    key_type: str = "2048",
) -> dict:
    """构造 Lucky 的 ACME 申请请求。

    validation 为 "http" 时走 HTTP-01，其他值视为 Lucky 的 DNS 服务商标识。
    """
    return {
        **_CERTIFICATE_SKELETON,
        "Remark": remark,
        "AddFrom": "acme",
        "ExtParams": {
            "acmeDNSServer": validation or "http",
            "acmeDNSID": dns_id,
            "acmeDNSSecret": dns_secret,
            "acmeCADirURL": ca_directory,
            "acmeKeyType": key_type,
            "acmeKid": "",
            "acmeHMAC": "",
            "acmeProxy": "",
            "acmeProxyAddr": "",
            "acmeProxyUser": "",
            "acmeProxyPassword": "",
            "acmeDomains": list(domains),
            "acmeEmail": email,
            "acmeRecursiveNameservers": "",
            "acmeOnlyUseIPv4": False,
            "acmeIgnorePropagationChecErrors": False,
            "acmeDisableAuthoritativeNssPropagationRequirement": False,
            "acmeFindZoneUseDNS": False,
            "acmeRotatePrivateKeyPerRequest": False,
            "acmeUseGlobalPrivateKey": False,
            "acmeSequentialSolver": False,
            "acmeDNSForceIPv4": False,
            "acmeDNSOnlyTCP": False,
            "acmeCNAMESupport": False,
            "acmeDisableCompletePropagationRequirement": False,
            "acmePropagationTimeout": 180,
            "acmeWaitforcertificateTimeout": 180,
        },
    }


def _is_managed_rule(rule: dict) -> bool:
    """OneTap 管理的规则：新版本按端口命名，旧版本是单条 OneTap 规则。"""
    key = str(rule.get("RuleKey") or "")
    name = str(rule.get("RuleName") or "")
    return key.startswith("onetap-") or name.startswith(ONETAP_RULE_NAME)


def rule_key(port: int) -> str:
    return f"{ONETAP_RULE_KEY_PREFIX}{int(port)}"


def rule_name(port: int) -> str:
    return f"{ONETAP_RULE_NAME} {int(port)}"


# Lucky 登录成功后要求把 token 放在该请求头里
ADMIN_TOKEN_HEADER = "Lucky-Admin-Token"
# v3 登录挑战与加密登录通过随机会话头绑定
LOGIN_SESSION_HEADER = "Lucky-Login-Session"
# 与 Lucky 前端一致：RSA 加密前按 120 个 UTF-8 字节切分，密文用 "." 连接
LOGIN_RSA_CHUNK_BYTES = 120


def _utf8_chunks(text: str, limit: int = LOGIN_RSA_CHUNK_BYTES) -> list[bytes]:
    """按字符切分，避免截断多字节字符。"""
    chunks: list[bytes] = []
    current = ""
    size = 0
    for char in text:
        width = len(char.encode("utf-8"))
        if current and size + width > limit:
            chunks.append(current.encode("utf-8"))
            current, size = "", 0
        current += char
        size += width
    if current:
        chunks.append(current.encode("utf-8"))
    return chunks


def _rsa_encrypt_login(public_key_pem: str, plaintext: str) -> str:
    """Lucky v3 的登录加密：RSA PKCS#1 v1.5，长文本分块后用 "." 连接。"""
    key = serialization.load_pem_public_key(public_key_pem.encode("utf-8"))
    parts = [
        base64.b64encode(key.encrypt(chunk, padding.PKCS1v15())).decode("ascii")
        for chunk in _utf8_chunks(plaintext)
    ]
    return ".".join(parts)


_last_signature_stamp = ""


def _signature_param() -> str:
    """Lucky 前端会给每个请求带上 `_` 校验参数，缺失时登录会被判为密码错误。

    前端的算法：毫秒时间戳去掉最后一位，再拼上各位数字之和对 8 取余。
    同一百毫秒内连续请求会撞到同一个值，这里顺延一位避免重复。
    """
    global _last_signature_stamp
    stamp = str(int(time.time() * 1000))[:-1]
    if stamp == _last_signature_stamp:
        stamp = str(int(stamp) + 1)
    _last_signature_stamp = stamp
    checksum = sum(int(digit) for digit in stamp) % 8
    return f"{stamp}{checksum}"


# Lucky 返回的英文错误信息，转成可直接展示的中文说明
_LUCKY_MESSAGES = {
    "IncorrectAccountOrPassword": "Lucky 账号或密码错误",
    "ExceededMaximumLoginAttempts": (
        "Lucky 登录失败次数过多，已暂停登录，请重启 Lucky 后重试"
    ),
    "Error parsing login request.": "Lucky 登录请求格式错误",
}


class LuckyError(APIException):
    """Lucky 调用失败。"""


class LuckyClient:
    """Lucky HTTP API 客户端。"""

    TIMEOUT = 15.0

    def __init__(self, base_url: str, username: str, password: str):
        url = (base_url or "").strip().rstrip("/")
        if not url:
            raise LuckyError("请填写 Lucky 地址", 400)
        if not url.startswith(("http://", "https://")):
            url = f"http://{url}"
        self.base_url = url
        self.username = (username or "").strip()
        self.password = password or ""
        self.token: str | None = None
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "LuckyClient":
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
            raise RuntimeError("LuckyClient 必须在 async with 中使用")
        return self._client

    async def _request(
        self,
        method: str,
        path: str,
        *,
        json: Any = None,
        params: dict | None = None,
        auth: bool = True,
        headers: dict | None = None,
        _retry: bool = True,
    ) -> dict:
        headers = dict(headers or {})
        if auth:
            if not self.token:
                await self.login()
            headers[ADMIN_TOKEN_HEADER] = self.token or ""
        query = {**(params or {}), "_": _signature_param()}
        try:
            response = await self.client.request(
                method,
                f"{self.base_url}{path}",
                json=json,
                params=query,
                headers=headers,
            )
        except httpx.HTTPError as exc:
            raise LuckyError(f"无法连接 Lucky：{exc}", 502) from exc

        if response.status_code >= 400:
            raise LuckyError(f"Lucky 请求失败（HTTP {response.status_code}）", 502)
        try:
            data = response.json()
        except ValueError as exc:
            raise LuckyError("Lucky 返回内容不是合法 JSON", 502) from exc
        if not isinstance(data, dict):
            raise LuckyError("Lucky 返回内容格式异常", 502)
        ret = data.get("ret")
        if ret not in (0, "0", None):
            message = str(data.get("msg") or "Lucky 操作失败")
            message = _LUCKY_MESSAGES.get(message, message)
            if ret in (-1, "-1"):
                # token 过期时重新登录一次，避免把「登录失效」当成账号密码错误
                if auth and _retry:
                    self.token = None
                    return await self._request(
                        method,
                        path,
                        json=json,
                        params=params,
                        auth=auth,
                        headers=headers,
                        _retry=False,
                    )
                raise LuckyError("Lucky 登录状态失效，请检查账号密码", 401)
            raise LuckyError(message, 400)
        return data

    async def _login_challenge(self, session: str) -> dict | None:
        """v3 的登录挑战；v2 没有该接口，返回 None 走旧版登录。"""
        try:
            response = await self.client.get(
                f"{self.base_url}/api/login/challenge",
                params={"_": _signature_param()},
                headers={LOGIN_SESSION_HEADER: session},
            )
            data = response.json()
        except (httpx.HTTPError, ValueError):
            return None
        if response.status_code >= 400 or not isinstance(data, dict):
            return None
        if data.get("ret") not in (0, "0", None):
            return None
        if not all(data.get(key) for key in ("challengeId", "nonce", "publicKey")):
            return None
        return data

    async def login(self) -> str:
        if not self.username or not self.password:
            raise LuckyError("请填写 Lucky 账号和密码", 400)
        session = os.urandom(32).hex()
        challenge = await self._login_challenge(session)
        if challenge is not None:
            # v3：账号密码先 RSA 加密，明文不再出现在请求体里
            plaintext = json.dumps(
                {
                    "account": self.username,
                    "password": self.password,
                    "twoFA": "",
                    "challengeId": challenge["challengeId"],
                    "nonce": challenge["nonce"],
                },
                ensure_ascii=False,
                separators=(",", ":"),
            )
            data = await self._request(
                "POST",
                "/api/login",
                json={
                    "challengeId": challenge["challengeId"],
                    "cipherText": _rsa_encrypt_login(
                        challenge["publicKey"], plaintext
                    ),
                },
                auth=False,
                headers={LOGIN_SESSION_HEADER: session},
            )
        else:
            data = await self._request(
                "POST",
                "/api/login",
                json={"Account": self.username, "Password": self.password},
                auth=False,
            )
        if data.get("need2FA"):
            raise LuckyError("Lucky 已开启两步验证，暂不支持自动登录", 400)
        token = data.get("token")
        if not token:
            raise LuckyError("Lucky 登录成功但未返回 token", 502)
        self.token = str(token)
        return self.token

    async def version(self) -> str | None:
        try:
            response = await self.client.get(f"{self.base_url}/version")
            if response.status_code < 400:
                data = response.json()
                if isinstance(data, dict):
                    return str(data.get("version") or "") or None
        except (httpx.HTTPError, ValueError):
            pass
        return None

    async def test(self) -> str | None:
        await self.login()
        return await self.version()

    async def list_proxy_rules(self) -> list[dict]:
        data = await self._request("GET", "/api/webservice/rules")
        rules = data.get("ruleList") or data.get("list") or data.get("data") or []
        return [item for item in rules if isinstance(item, dict)]

    async def list_managed_rules(self) -> list[dict]:
        """OneTap 管理的规则；兼容旧版单条「OneTap」规则。"""
        return [
            rule for rule in await self.list_proxy_rules() if _is_managed_rule(rule)
        ]

    async def save_proxy_rules(self, payloads: list[dict]) -> None:
        """按规则名维护规则，跨端口移动子规则时先释放旧规则标识。

        Lucky 新版本在创建时自行生成 RuleKey，所以只能用 RuleName 对应。
        """
        existing = {
            str(rule.get("RuleName") or ""): rule
            for rule in await self.list_managed_rules()
        }
        desired_payloads = {
            str(payload.get("RuleName") or ""): payload for payload in payloads
        }
        desired = set(desired_payloads)
        desired_owners = {
            str(sub_rule.get("Key") or ""): name
            for name, payload in desired_payloads.items()
            for sub_rule in payload.get("ProxyList") or []
            if sub_rule.get("Key")
        }
        for name, rule in existing.items():
            if name in desired:
                continue
            key = str(rule.get("RuleKey") or "")
            if key:
                await self._request("DELETE", f"/api/webservice/rule/{key}")

        for name, current in existing.items():
            if name not in desired:
                continue
            key = str(current.get("RuleKey") or "")
            if not key:
                continue
            current_keys = {
                str(sub_rule.get("Key") or "")
                for sub_rule in current.get("ProxyList") or []
                if sub_rule.get("Key")
            }
            if not any(desired_owners.get(item) != name for item in current_keys):
                continue
            payload = desired_payloads[name]
            retained = [
                sub_rule
                for sub_rule in payload.get("ProxyList") or []
                if str(sub_rule.get("Key") or "") in current_keys
            ]
            body = {**payload, "RuleKey": key, "ProxyList": retained}
            await self._request("PUT", f"/api/webservice/rule/{key}", json=body)

        for payload in payloads:
            name = str(payload.get("RuleName") or "")
            current = existing.get(name)
            key = str(current.get("RuleKey") or "") if current else ""
            if key:
                body = {**payload, "RuleKey": key}
                await self._request(
                    "PUT", f"/api/webservice/rule/{key}", json=body
                )
            else:
                await self._request(
                    "POST", "/api/webservice/rules", json=payload
                )

    async def delete_proxy_rules(self) -> None:
        for rule in await self.list_managed_rules():
            key = str(rule.get("RuleKey") or "")
            if key:
                await self._request("DELETE", f"/api/webservice/rule/{key}")

    async def list_certificates(self) -> list[dict]:
        """Lucky SSL 模块里的证书；反代规则按域名自动匹配，不能单独绑定。"""
        data = await self._request("GET", "/api/ssl")
        items = data.get("list") or data.get("data") or []
        return [item for item in items if isinstance(item, dict)]

    async def create_certificate(self, payload: dict) -> None:
        """新增证书。Lucky 自行生成 Key，需要再从列表里找回。"""
        await self._request("POST", "/api/ssl", json=payload)

    async def delete_certificate(self, key: str) -> None:
        await self._request("DELETE", "/api/ssl", params={"key": key})

    async def get_ddns_config(self) -> dict:
        data = await self._request("GET", "/api/ddns/configure")
        config = data.get("ddnsconfigure") or data.get("data") or {}
        return config if isinstance(config, dict) else {}

    async def ensure_ddns_enabled(self) -> None:
        config = await self.get_ddns_config()
        if config.get("Enable"):
            return
        payload = {
            "Enable": True,
            "HttpClientSecureVerify": bool(config.get("HttpClientSecureVerify", False)),
            "FirstCheckDelay": int(config.get("FirstCheckDelay", 1) or 1),
            "Intervals": int(config.get("Intervals", 300) or 300),
        }
        await self._request("PUT", "/api/ddns/configure", json=payload)

    async def list_ddns_tasks(self) -> list[dict]:
        data = await self._request("GET", "/api/ddnstasklist")
        tasks = data.get("data") or []
        return [item for item in tasks if isinstance(item, dict)]

    async def find_ddns_task(self, family: str) -> dict | None:
        name = ddns_task_name(family)
        for task in await self.list_ddns_tasks():
            if task.get("TaskName") == name:
                return task
        return None

    async def save_ddns_task(self, family: str, payload: dict) -> None:
        """按协议族维护 OneTap 的 DDNS 任务；禁用且不存在时不再新建。"""
        task = await self.find_ddns_task(family)
        key = str(task.get("TaskKey") or "") if task else ""
        if key:
            await self._request(
                "PUT",
                "/api/ddns",
                json={**payload, "TaskKey": key},
                params={"key": key},
            )
        elif payload.get("Enable"):
            await self._request("POST", "/api/ddns", json=payload)

    async def delete_ddns_tasks(self) -> None:
        for task in await self.list_ddns_tasks():
            if not _is_managed_ddns(task):
                continue
            key = str(task.get("TaskKey") or "")
            if key:
                await self._request("DELETE", "/api/ddns", params={"key": key})

    async def delete_legacy_ddns_task(self) -> None:
        """删除旧版本的单条「OneTap 动态域名」任务。"""
        for task in await self.list_ddns_tasks():
            if task.get("TaskName") != ONETAP_DDNS_NAME:
                continue
            key = str(task.get("TaskKey") or "")
            if key:
                await self._request("DELETE", "/api/ddns", params={"key": key})

    async def list_interfaces(self) -> dict:
        """Lucky 所在主机的网卡列表，供 DDNS 直接取 IP。"""
        data = await self._request("GET", "/api/netinterfaces")
        payload = data.get("data")
        return payload if isinstance(payload, dict) else {}

    async def get_base_config(self) -> dict:
        """读取基础配置；返回的 AdminPassword 是掩码字符串。"""
        data = await self._request("GET", "/api/baseconfigure")
        config = data.get("baseconfigure")
        if not isinstance(config, dict):
            raise LuckyError("Lucky 返回的基础配置格式异常", 502)
        return config

    async def update_admin(
        self,
        *,
        account: str,
        password: str,
        old_password: str,
        listen_port: int | None = None,
        disable_tls: bool = False,
    ) -> None:
        """修改管理员账号密码，可选调整管理端口并关闭 HTTPS 监听。

        Lucky 修改密码时必须提供原密码；改完账号密码后旧 token 即刻失效。
        """
        config = await self.get_base_config()
        config["AdminAccount"] = account
        config["AdminPassword"] = password
        config["OldPassword"] = old_password
        if listen_port is not None:
            config["AdminWebListenPort"] = int(listen_port)
        if disable_tls:
            config["AdminWebListenTLS"] = False
        await self._request("PUT", "/api/baseconfigure", json=config)


def _sub_rule(
    instance_id: int,
    *,
    domains: list[str],
    target_url: str,
    remark: str,
) -> dict:
    return {
        "Key": f"onetap-{instance_id}",
        "Enable": True,
        "Remark": remark,
        "WebServiceType": "reverseproxy",
        "Domains": domains,
        "Locations": [target_url],
        "LocationInsecureSkipVerify": True,
        # Lucky 的「万事大吉」：自动补全反代所需请求头
        "EasyLucky": True,
        "EnableAccessLog": True,
        "LogLevel": 4,
        "LogOutputToConsole": False,
        "AccessLogMaxNum": 1000,
        "WebListShowLastLogMaxCount": 30,
        "ForwardedByClientIP": False,
        "TrustedCIDRsStrList": [],
        "RemoteIPHeaders": [],
        "AddRemoteIPToHeader": False,
        "AddRemoteIPHeaderKey": "",
        "EnableBasicAuth": False,
        "BasicAuthUser": "",
        "BasicAuthPasswd": "",
        "SafeIPMode": "blacklist",
        "SafeUserAgentMode": "blacklist",
        "UserAgentfilter": [],
        "CustomRobotTxt": False,
        "RobotTxt": "",
    }


def _default_sub_rule(
    service_type: str = "reverseproxy",
    locations: list[str] | None = None,
) -> dict:
    return {
        "Key": "default",
        "WebServiceType": service_type,
        "Locations": list(locations or []),
        "LocationInsecureSkipVerify": True,
        "EasyLucky": True,
        "EnableAccessLog": True,
        "LogLevel": 4,
        "LogOutputToConsole": False,
        "AccessLogMaxNum": 1000,
        "WebListShowLastLogMaxCount": 10,
        "ForwardedByClientIP": False,
        "TrustedCIDRsStrList": [],
        "RemoteIPHeaders": [],
        "AddRemoteIPToHeader": False,
        "AddRemoteIPHeaderKey": "",
        "EnableBasicAuth": False,
        "BasicAuthUser": "",
        "BasicAuthPasswd": "",
        "SafeIPMode": "blacklist",
        "SafeUserAgentMode": "blacklist",
        "UserAgentfilter": [],
        "CustomRobotTxt": False,
        "RobotTxt": "",
    }


def build_proxy_rule(
    *,
    listen_port: int,
    enable_tls: bool,
    network: str = "tcp",
    links: dict[int, dict],
) -> dict:
    """按 OneTap 本地记录的域名映射生成完整反向代理规则。"""
    proxy_list = [
        _sub_rule(
            int(instance_id),
            domains=list(link.get("domains") or []),
            target_url=str(link.get("target_url") or ""),
            remark=str(link.get("remark") or ""),
        )
        for instance_id, link in links.items()
        if link.get("domains") and link.get("target_url")
    ]
    return {
        "RuleName": rule_name(listen_port),
        "RuleKey": rule_key(listen_port),
        "Enable": True,
        "Network": network if network in ("tcp", "tcp4", "tcp6") else "tcp",
        "ListenIP": "",
        "ListenPort": listen_port,
        "EnableTLS": enable_tls,
        "IPFilterRule": DEFAULT_IP_FILTER_RULE,
        "TLSMinVersion": 2,
        "MaxHeaderKBytes": 32,
        "DefaultProxy": _default_sub_rule(),
        "ProxyList": proxy_list,
    }


def redirect_rule_name(port: int) -> str:
    return f"{rule_name(port)} HTTP"


def redirect_rule_key(port: int) -> str:
    return f"{ONETAP_RULE_KEY_PREFIX}http-{int(port)}"


def redirect_target(https_port: int) -> str:
    if int(https_port) == 443:
        return "https://{host}{path}{args}"
    return f"https://{{host}}:{int(https_port)}{{path}}{{args}}"


def build_redirect_rule(*, https_port: int, network: str = "tcp") -> dict:
    """HTTP → HTTPS 跳转规则。

    Lucky 的做法是单独一条不启用 TLS 的 Web 服务规则，默认子规则类型为
    重定向；443 端口对应监听 80，其他端口监听同一端口（Lucky 支持同一
    端口同时存在 TLS / 非 TLS 两条规则）。
    """
    listen_port = 80 if int(https_port) == 443 else int(https_port)
    return {
        "RuleName": redirect_rule_name(https_port),
        "RuleKey": redirect_rule_key(https_port),
        "Enable": True,
        "Network": network if network in ("tcp", "tcp4", "tcp6") else "tcp",
        "ListenIP": "",
        "ListenPort": listen_port,
        "EnableTLS": False,
        "IPFilterRule": DEFAULT_IP_FILTER_RULE,
        "TLSMinVersion": 2,
        "MaxHeaderKBytes": 32,
        # 跳转目标写成变量，Lucky 会按请求域名和端口替换
        "DefaultProxy": _default_sub_rule(
            "redirect", [redirect_target(https_port)]
        ),
        "ProxyList": [],
    }


def _port_network(links: dict[int, dict]) -> str:
    """同一端口的所有应用共用一个监听器，有双栈需求就监听双栈。"""
    networks = {str(link.get("network") or "tcp") for link in links.values()}
    if "tcp" in networks or len(networks) > 1:
        return "tcp"
    return networks.pop() if networks else "tcp"


def build_proxy_rules(
    *,
    links: dict[int, dict],
    default_port: int,
    default_tls: bool = False,
) -> list[dict]:
    """按监听端口分组，一个端口生成一条 Lucky 规则。

    HTTPS 和监听协议是规则级的，一个端口上的应用共享；取并集：
    任一应用启用 HTTPS 就启用，监听协议按 ``_port_network`` 归并。
    启用 HTTPS 且需要强制跳转时，再补一条不启用 TLS 的重定向规则。
    """
    groups: dict[int, dict[int, dict]] = {}
    for instance_id, link in links.items():
        try:
            port = int(link.get("listen_port") or default_port)
        except (TypeError, ValueError):
            port = default_port
        if not 1 <= port <= 65535:
            port = default_port
        groups.setdefault(port, {})[instance_id] = link
    rules = []
    for port, group in sorted(groups.items()):
        enable_tls = any(
            bool(link.get("enable_tls", default_tls)) for link in group.values()
        )
        network = _port_network(group)
        rules.append(
            build_proxy_rule(
                listen_port=port,
                enable_tls=enable_tls,
                network=network,
                links=group,
            )
        )
        if not enable_tls:
            continue
        if not any(bool(link.get("ssl_forced", True)) for link in group.values()):
            continue
        redirect_port = 80 if port == 443 else port
        # 80 端口已经被另一条 HTTPS 规则占用时不重复创建
        if redirect_port != port and redirect_port in groups:
            continue
        rules.append(build_redirect_rule(https_port=port, network=network))
    return rules


def build_ddns_task(
    *,
    record_type: str,
    enabled: bool,
    provider: str,
    access_id: str,
    secret: str,
    domains: list[str],
    interval: int,
    get_type: str = "url",
    url: str = "",
    net_interface: str = "",
    ip_reg: str = "",
) -> dict:
    """构造 Lucky DDNS 任务请求。

    Lucky 的任务级 DNS 配置是共用的，因此 IPv4 / IPv6 各建一条任务，
    每条只放本协议族的域名和取 IP 方式。
    """
    family = record_type if record_type in DDNS_FAMILIES else "IPv4"
    is_v4 = family == "IPv4"
    query_type = get_type if get_type in ("url", "netInterface") else "url"
    default_url = DEFAULT_IPV4_URLS if is_v4 else DEFAULT_IPV6_URLS
    query_url = (url or "").strip() or default_url
    record_type_value = "A" if is_v4 else "AAAA"
    address_key = "ipv4Address" if is_v4 else "ipv6Address"
    placeholder = "{ipv4Addr}" if is_v4 else "{ipv6Addr}"
    records = [
        {
            "Disable": False,
            "SyncRecordData": {
                "type": record_type_value,
                "remark": "",
                "fullDomainName": domain,
                "ttl": 0,
                address_key: placeholder,
            },
        }
        for domain in domains
    ]
    v4 = {
        "V4QueryIPEnable": is_v4,
        "V4QueryIPType": query_type if is_v4 else "url",
        "V4QueryUrl": query_url if is_v4 else DEFAULT_IPV4_URLS,
        "V4NetInterface": net_interface if is_v4 else "",
        "V4NetInterfaceIPReg": ip_reg if is_v4 else "",
        "V4GetIPScript": "",
    }
    v6 = {
        "V6QueryIPEnable": not is_v4,
        "V6QueryIPType": query_type if not is_v4 else "url",
        "V6QueryUrl": query_url if not is_v4 else DEFAULT_IPV6_URLS,
        "V6NetInterface": net_interface if not is_v4 else "",
        "V6NetInterfaceIPReg": ip_reg if not is_v4 else "",
        "V6GetIPScript": "",
        "V6DUID": "",
    }
    return {
        "TaskName": ddns_task_name(family),
        "TaskKey": "",
        "TaskType": family,
        "Enable": enabled,
        "DiaglogShowMode": "simple",
        "HttpClientTimeout": 15,
        "InsecureSkipVerify": False,
        "FirstCheckDelay": 16,
        "Intervals": max(interval, 30),
        "DebugMode": False,
        "DNS": {
            "Name": provider,
            "ID": access_id,
            "Secret": secret,
            "ForceInterval": 3600,
            "ResolverDoaminCheck": True,
            "DNSServerList": [],
            "CallAPINetwork": "",
            "Callback": {
                "URL": "",
                "Method": "get",
                "Headers": [],
                "RequestBody": "",
                "Server": "other",
                "DisableCallbackSuccessContentCheck": False,
                "CallbackSuccessContent": [],
            },
            "HttpClientProxyType": "",
            "HttpClientProxyAddr": "",
            "HttpClientProxyUser": "",
            "HttpClientProxyPassword": "",
        },
        "GlobalWebhook": False,
        "WebhookEnable": False,
        "WebhookURL": "",
        "WebhookMethod": "get",
        "WebhookHeaders": [],
        "WebhookRequestBody": "",
        "WebhookDisableCallbackSuccessContentCheck": False,
        "WebhookSuccessContent": [],
        "WebhookProxy": "",
        "WebhookProxyAddr": "",
        "WebhookProxyUser": "",
        "WebhookProxyPassword": "",
        "RetryCount": 0,
        "RetryInterval": 500,
        "TTL": "",
        **v4,
        **v6,
        "Records": records,
    }
