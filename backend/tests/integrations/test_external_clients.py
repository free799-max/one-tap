"""Lucky / Vaultwarden 客户端与外部接口格式的联调测试。

使用本地 HTTP 服务模拟两个外部系统，验证登录、加密写入与规则结构。
"""

import json
import base64
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

import pytest
from cryptography import x509
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base
from app.models.app_store import App
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    AppOrchestrationInstance,
)
from app.schemas.integration import (
    LuckyConnectionUpdate,
    NetworkCertificateRequest,
    NetworkDdnsUpdate,
    NetworkDdnsUpdateFamily,
    NetworkProxyRequest,
    NetworkUpdate,
    VaultwardenUpdate,
)
from app.services.integrations import integration_service
from app.services.integrations.lucky import (
    LuckyClient,
    build_ddns_task,
    build_proxy_rule,
    build_proxy_rules,
)
from app.services.integrations.vaultwarden import (
    VaultwardenClient,
    VaultwardenCrypto,
    _aes_encrypt,
    _b64encode,
    _hkdf_expand,
)


@pytest.fixture(autouse=True)
def _skip_docker_check(monkeypatch):
    """单元测试不依赖宿主机 Docker，跳过容器存在性校验。"""

    async def _no_docker():
        return None

    monkeypatch.setattr(
        "app.services.integrations.service._docker_app_keys", _no_docker
    )


class _FakeServer:
    def __init__(self):
        self.requests: list[tuple[str, str, dict | None]] = []
        self.lucky_auth: list[tuple[str, str | None]] = []
        self.ciphers: dict[str, dict] = {}
        self.lucky_rules: list[dict] = []
        self.lucky_certs: list[dict] = [
            {
                "Key": "cert-1",
                "Remark": "example",
                "CertsInfo": {
                    "Domains": ["media.example.com"],
                    "NotAfterTime": "2027-01-01",
                },
            }
        ]
        self.ddns_tasks: list[dict] = []
        self.ddns_enabled = False
        # 置为 True 时模拟 Lucky v3：先取登录挑战，再提交 RSA 加密的登录请求
        self.encrypted_login = False
        self.login_payload: dict | None = None
        self._rsa_key = None
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def _login_public_key(self) -> str:
        if self._rsa_key is None:
            self._rsa_key = rsa.generate_private_key(
                public_exponent=65537, key_size=2048
            )
        return (
            self._rsa_key.public_key()
            .public_bytes(
                serialization.Encoding.PEM,
                serialization.PublicFormat.SubjectPublicKeyInfo,
            )
            .decode()
        )

    def duplicate_sub_rule_key(self, body: dict, ignore_rule_key: str = "") -> str:
        existing = {
            str(sub_rule.get("Key") or "")
            for rule in self.lucky_rules
            if rule.get("RuleKey") != ignore_rule_key
            for sub_rule in rule.get("ProxyList") or []
            if sub_rule.get("Key")
        }
        return next(
            (
                str(sub_rule.get("Key") or "")
                for sub_rule in body.get("ProxyList") or []
                if str(sub_rule.get("Key") or "") in existing
            ),
            "",
        )

    @property
    def base_url(self) -> str:
        host, port = self._server.server_address
        return f"http://{host}:{port}"

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self._server.shutdown()
        self._server.server_close()

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _read_body(self):
                length = int(self.headers.get("content-length", 0) or 0)
                if not length:
                    return b""
                return self.rfile.read(length)

            def _read_json(self):
                raw = self._read_body()
                if not raw:
                    return None
                return json.loads(raw)

            def _send(self, payload, status=200):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _record_auth(self):
                path = urlparse(self.path).path
                if path.startswith("/api/"):
                    outer.lucky_auth.append(
                        (path, self.headers.get("Lucky-Admin-Token"))
                    )

            def _query(self):
                return parse_qs(urlparse(self.path).query)

            def do_GET(self):
                outer.requests.append(("GET", self.path, None))
                self._record_auth()
                path = urlparse(self.path).path
                if path == "/api/config":
                    return self._send({"version": "2026.1.0"})
                if path == "/version":
                    return self._send({"ret": 0, "version": "2.19.0"})
                if path == "/api/login/challenge":
                    if not outer.encrypted_login:
                        return self._send({"ret": 1, "msg": "not found"}, 404)
                    return self._send(
                        {
                            "ret": 0,
                            "challengeId": "challenge-1",
                            "nonce": "nonce-1",
                            "publicKey": outer._login_public_key(),
                            "binding": "session",
                        }
                    )
                if path == "/api/webservice/rules":
                    return self._send({"ret": 0, "ruleList": outer.lucky_rules})
                if path == "/api/ssl":
                    return self._send({"ret": 0, "list": outer.lucky_certs})
                if path == "/api/ddns/configure":
                    return self._send(
                        {"ret": 0, "ddnsconfigure": {"Enable": outer.ddns_enabled}}
                    )
                if path == "/api/ddnstasklist":
                    return self._send({"ret": 0, "data": outer.ddns_tasks})
                if path == "/api/netinterfaces":
                    return self._send(
                        {
                            "ret": 0,
                            "data": {
                                "IPv4NewInterfaces": [
                                    {
                                        "NetInterfaceName": "ens2",
                                        "HardwareAddr": "aa:bb:cc:dd:ee:ff",
                                        "AddressList": ["192.168.1.2"],
                                    }
                                ],
                                "IPv6NewInterfaces": [
                                    {
                                        "NetInterfaceName": "ens2",
                                        "HardwareAddr": "aa:bb:cc:dd:ee:ff",
                                        "AddressList": ["fd00::2"],
                                    }
                                ],
                            },
                        }
                    )
                return self._send({"ret": 1, "msg": "not found"}, 404)

            def do_POST(self):
                raw = self._read_body()
                self._record_auth()
                path = urlparse(self.path).path
                if path == "/identity/connect/token":
                    body = parse_qs(raw.decode())
                else:
                    body = json.loads(raw) if raw else None
                outer.requests.append(("POST", self.path, body))
                if path == "/identity/accounts/prelogin":
                    return self._send({"Kdf": 0, "KdfIterations": 100000})
                if path == "/identity/connect/token":
                    assert body["password"][0]
                    master_key = VaultwardenCrypto.derive_master_key(
                        "master", "admin@example.com", 0, 100000
                    )
                    enc_key = _hkdf_expand(master_key, b"enc")
                    mac_key = _hkdf_expand(master_key, b"mac")
                    user_key = bytes(range(64))
                    import hashlib
                    import hmac

                    iv = b"0123456789abcdef"
                    ciphertext = _aes_encrypt(enc_key, iv, user_key)
                    mac = hmac.new(mac_key, iv + ciphertext, hashlib.sha256).digest()
                    key = "2.{}|{}|{}".format(
                        _b64encode(iv), _b64encode(ciphertext), _b64encode(mac)
                    )
                    return self._send({"access_token": "token", "Key": key})
                if path == "/api/ciphers":
                    cipher_id = f"cipher-{len(outer.ciphers) + 1}"
                    outer.ciphers[cipher_id] = body
                    return self._send({"Id": cipher_id})
                if path == "/api/login":
                    if outer.encrypted_login:
                        if body.get("challengeId") != "challenge-1":
                            return self._send({"ret": 2, "msg": "invalid challenge"})
                        plaintext = b"".join(
                            outer._rsa_key.decrypt(
                                base64.b64decode(part), padding.PKCS1v15()
                            )
                            for part in body["cipherText"].split(".")
                        )
                        outer.login_payload = json.loads(plaintext)
                        return self._send({"ret": 0, "token": "lucky-token"})
                    # Lucky 新版要求带 "_" 校验参数，缺失时会误报账号密码错误
                    if "_" not in self._query():
                        return self._send(
                            {"ret": 2, "msg": "IncorrectAccountOrPassword"}
                        )
                    return self._send({"ret": 0, "token": "lucky-token"})
                if path == "/api/webservice/rules":
                    duplicate = outer.duplicate_sub_rule_key(body)
                    if duplicate:
                        return self._send(
                            {"ret": 1, "msg": f"规则标识重复 [{duplicate}]"}
                        )
                    key = f"srv-key-{len(outer.lucky_rules) + 1}"
                    outer.lucky_rules.append({**body, "RuleKey": key})
                    return self._send({"ret": 0})
                if path == "/api/ddns":
                    key = f"task-{len(outer.ddns_tasks) + 1}"
                    outer.ddns_tasks.append({**body, "TaskKey": key})
                    return self._send({"ret": 0})
                if path == "/api/ssl":
                    return self._add_certificate(body)
                return self._send({"ret": 1, "msg": "not found"}, 404)

            def _add_certificate(self, body):
                key = f"ssl-key-{len(outer.lucky_certs) + 1}"
                row = {
                    "Key": key,
                    "Remark": body.get("Remark", ""),
                    "AddFrom": body.get("AddFrom", ""),
                    "ExtParams": body.get("ExtParams") or {},
                    "ACMEErrMsg": "",
                    "ACMEing": body.get("AddFrom") == "acme",
                    "CertsInfo": None,
                }
                if body.get("AddFrom") == "file":
                    certificate = x509.load_pem_x509_certificate(
                        base64.b64decode(body["CertBase64"])
                    )
                    sans = certificate.extensions.get_extension_for_class(
                        x509.SubjectAlternativeName
                    ).value
                    row["CertsInfo"] = {
                        "Domains": [entry.value for entry in sans],
                        "NotAfterTime": "2030-01-01",
                    }
                    row["ACMEing"] = False
                outer.lucky_certs.append(row)
                return self._send({"ret": 0})

            def do_PUT(self):
                body = self._read_json()
                self._record_auth()
                path = urlparse(self.path).path
                outer.requests.append(("PUT", self.path, body))
                if path.startswith("/api/ciphers/"):
                    outer.ciphers[path.rsplit("/", 1)[-1]] = body
                    return self._send({})
                if path.startswith("/api/webservice/rule/"):
                    key = path.rsplit("/", 1)[-1]
                    duplicate = outer.duplicate_sub_rule_key(body, key)
                    if duplicate:
                        return self._send(
                            {"ret": 1, "msg": f"规则标识重复 [{duplicate}]"}
                        )
                    outer.lucky_rules = [
                        {**body, "RuleKey": key}
                        if rule.get("RuleKey") == key
                        else rule
                        for rule in outer.lucky_rules
                    ]
                    return self._send({"ret": 0})
                if path == "/api/ddns/configure":
                    outer.ddns_enabled = bool(body.get("Enable"))
                    return self._send({"ret": 0})
                if path == "/api/ddns":
                    key = (self._query().get("key") or [""])[0]
                    outer.ddns_tasks = [
                        {**body, "TaskKey": key}
                        if task.get("TaskKey") == key
                        else task
                        for task in outer.ddns_tasks
                    ]
                    return self._send({"ret": 0})
                return self._send({"ret": 1, "msg": "not found"}, 404)

            def do_DELETE(self):
                outer.requests.append(("DELETE", self.path, None))
                self._record_auth()
                path = urlparse(self.path).path
                if path.startswith("/api/webservice/rule/"):
                    key = path.rsplit("/", 1)[-1]
                    outer.lucky_rules = [
                        rule
                        for rule in outer.lucky_rules
                        if rule.get("RuleKey") != key
                    ]
                    return self._send({"ret": 0})
                if path == "/api/ddns":
                    key = (self._query().get("key") or [""])[0]
                    outer.ddns_tasks = [
                        task
                        for task in outer.ddns_tasks
                        if task.get("TaskKey") != key
                    ]
                    return self._send({"ret": 0})
                if path == "/api/ssl":
                    key = (self._query().get("key") or [""])[0]
                    outer.lucky_certs = [
                        item
                        for item in outer.lucky_certs
                        if item.get("Key") != key
                    ]
                    return self._send({"ret": 0})
                return self._send({"ret": 1, "msg": "not found"}, 404)

        return Handler


@pytest.fixture
def fake_server():
    server = _FakeServer().start()
    yield server
    server.stop()


@pytest.mark.asyncio
async def test_vaultwarden_login_and_create_cipher(fake_server):
    async with VaultwardenClient(fake_server.base_url) as client:
        session = await client.login("admin@example.com", "master")
        assert session.version == "2026.1.0"
        payload = client.build_login_cipher(
            name="OneTap · Jellyfin",
            username="admin",
            password="secret",
            url="http://192.168.1.2:8096",
        )
        cipher_id = await client.create_cipher(payload)
        assert cipher_id == "cipher-1"

    stored = fake_server.ciphers["cipher-1"]
    enc_key, mac_key = VaultwardenCrypto.split_user_key(bytes(range(64)))
    assert (
        VaultwardenCrypto._decrypt_enc_string(
            stored["login"]["password"], enc_key, mac_key
        ).decode()
        == "secret"
    )


@pytest.mark.asyncio
async def test_lucky_save_proxy_rule(fake_server):
    payload = build_proxy_rule(
        listen_port=443,
        enable_tls=True,
        links={
            7: {
                "domains": ["media.example.com", "media.lan"],
                "target_url": "http://192.168.1.2:8096",
                "remark": "Jellyfin",
            }
        },
    )
    async with LuckyClient(fake_server.base_url, "666", "666") as client:
        assert await client.test() == "2.19.0"
        await client.save_proxy_rules([payload])

        assert fake_server.lucky_rules[0]["RuleKey"] == "srv-key-1"
        assert ("/api/webservice/rules", "lucky-token") in fake_server.lucky_auth

        payload["ProxyList"][0]["Locations"] = ["http://192.168.1.2:9096"]
        await client.save_proxy_rules([payload])
        assert len(fake_server.lucky_rules) == 1
        assert fake_server.lucky_rules[0]["RuleKey"] == "srv-key-1"
        assert ("/api/webservice/rule/srv-key-1", "lucky-token") in fake_server.lucky_auth

    assert fake_server.lucky_rules[0]["RuleName"] == "OneTap 443"
    assert fake_server.lucky_rules[0]["IPFilterRule"] == "blacklist"
    assert fake_server.lucky_rules[0]["ProxyList"][0]["Domains"] == [
        "media.example.com",
        "media.lan",
    ]
    assert fake_server.lucky_rules[0]["ProxyList"][0]["Locations"] == [
        "http://192.168.1.2:9096"
    ]


@pytest.mark.asyncio
async def test_lucky_move_sub_rule_to_another_port(fake_server):
    links = {
        7: {
            "domains": ["media.example.com"],
            "target_url": "http://192.168.1.2:8096",
            "remark": "Jellyfin",
        },
        -1: {
            "domains": ["custom.lan"],
            "target_url": "http://192.168.1.30:9000",
            "remark": "自定义应用",
        },
    }
    initial = build_proxy_rule(listen_port=443, enable_tls=True, links=links)
    moved = build_proxy_rule(
        listen_port=80, enable_tls=False, links={-1: links[-1]}
    )
    remaining = build_proxy_rule(
        listen_port=443, enable_tls=True, links={7: links[7]}
    )

    async with LuckyClient(fake_server.base_url, "666", "666") as client:
        await client.save_proxy_rules([initial])
        await client.save_proxy_rules([moved, remaining])

    rules = {rule["ListenPort"]: rule for rule in fake_server.lucky_rules}
    assert [item["Key"] for item in rules[80]["ProxyList"]] == ["onetap--1"]
    assert [item["Key"] for item in rules[443]["ProxyList"]] == ["onetap-7"]


@pytest.mark.asyncio
async def test_lucky_v3_encrypted_login(fake_server):
    fake_server.encrypted_login = True
    # 超过 120 字节，覆盖 Lucky v3 的分块 RSA 加密
    password = "长密码" * 40 + "tail"
    async with LuckyClient(fake_server.base_url, "666", password) as client:
        assert await client.login() == "lucky-token"
        assert await client.list_proxy_rules() == []

    payload = fake_server.login_payload
    assert payload is not None
    assert payload["account"] == "666"
    assert payload["password"] == password
    assert payload["challengeId"] == "challenge-1"
    assert payload["nonce"] == "nonce-1"


def test_lucky_build_proxy_rules_groups_by_port():
    rules = build_proxy_rules(
        default_port=443,
        default_tls=True,
        links={
            1: {
                "domains": ["a.example.com", "a.lan"],
                "target_url": "http://192.168.1.2:3000",
                "listen_port": 443,
                "enable_tls": True,
            },
            2: {
                "domains": ["b.example.com"],
                "target_url": "http://192.168.1.2:3001",
                "listen_port": 8443,
                "network": "tcp6",
            },
            3: {
                "domains": ["c.example.com"],
                "target_url": "http://192.168.1.2:3002",
            },
        },
    )

    by_port = {(rule["ListenPort"], rule["EnableTLS"]): rule for rule in rules}
    assert set(by_port) == {(443, True), (8443, True), (80, False), (8443, False)}
    assert by_port[(8443, True)]["RuleKey"] == "onetap-port-8443"
    assert by_port[(8443, True)]["Network"] == "tcp6"
    assert by_port[(443, True)]["ProxyList"][0]["Domains"] == [
        "a.example.com",
        "a.lan",
    ]
    assert [sub["Domains"][0] for sub in by_port[(443, True)]["ProxyList"]] == [
        "a.example.com",
        "c.example.com",
    ]

    redirect_443 = by_port[(80, False)]
    assert redirect_443["RuleName"] == "OneTap 443 HTTP"
    assert redirect_443["DefaultProxy"]["WebServiceType"] == "redirect"
    assert redirect_443["DefaultProxy"]["Locations"] == [
        "https://{host}{path}{args}"
    ]

    redirect_8443 = by_port[(8443, False)]
    assert redirect_8443["DefaultProxy"]["Locations"] == [
        "https://{host}:8443{path}{args}"
    ]
    assert redirect_8443["Network"] == "tcp6"


def test_lucky_build_proxy_rules_skips_redirect_when_disabled():
    rules = build_proxy_rules(
        default_port=443,
        links={
            1: {
                "domains": ["a.example.com"],
                "target_url": "http://192.168.1.2:3000",
                "enable_tls": True,
                "ssl_forced": False,
            }
        },
    )

    assert len(rules) == 1
    assert rules[0]["EnableTLS"] is True


@pytest.mark.asyncio
async def test_lucky_ddns_enable_and_save(fake_server):
    payload = build_ddns_task(
        record_type="IPv4",
        enabled=True,
        provider="alidns",
        access_id="key-id",
        secret="key-secret",
        domains=["home.example.com"],
        interval=300,
    )
    async with LuckyClient(fake_server.base_url, "666", "666") as client:
        await client.ensure_ddns_enabled()
        await client.save_ddns_task("IPv4", payload)

    assert fake_server.ddns_enabled is True
    task = fake_server.ddns_tasks[0]
    assert task["TaskName"] == "OneTap 动态域名 IPv4"
    assert task["Records"][0]["SyncRecordData"]["fullDomainName"] == (
        "home.example.com"
    )
    assert task["Records"][0]["SyncRecordData"]["ipv4Address"] == "{ipv4Addr}"


@pytest.mark.asyncio
async def test_vaultwarden_sync_discovers_and_tracks_items(fake_server):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        app = App(
            name="jellyfin",
            display_name="Jellyfin",
            category="media",
            yaml_template="",
        )
        orchestration = AppOrchestration(
            name="media",
            display_name="影视",
            category="影视",
            app_composition=[],
        )
        group = AppOrchestrationInstance(
            orchestration=orchestration,
            instance_name="默认",
            shared_config={},
            app_configs={
                "jellyfin": {
                    "url": "http://192.168.1.2:8096",
                    "username": "admin",
                    "password": "secret",
                }
            },
            status="running",
        )
        instance = AppInstance(
            app=app,
            orchestration=orchestration,
            orchestration_group=group,
            instance_name="jellyfin",
            config={"url": "http://192.168.1.2:8096"},
            status="running",
        )
        db.add_all([app, orchestration, group, instance])
        await db.commit()

        await integration_service.save_vaultwarden(
            db,
            VaultwardenUpdate(
                enabled=True,
                url=fake_server.base_url,
                email="admin@example.com",
                password="master",
            ),
        )
        apps = await integration_service.list_vaultwarden_apps(db, "192.168.1.2")
        assert len(apps) == 1
        assert apps[0].username == "admin"
        assert apps[0].password_set is True
        assert apps[0].has_credentials is True

        result = await integration_service.sync_vaultwarden(db, None, "192.168.1.2")
        assert result.ok is True
        assert result.items[0].message == "已创建"

        apps = await integration_service.list_vaultwarden_apps(db, "192.168.1.2")
        assert apps[0].synced is True

        # 再次同步应更新原条目而不是重复创建
        result = await integration_service.sync_vaultwarden(db, None, "192.168.1.2")
        assert result.ok is True
        assert result.items[0].message == "已更新"
        assert len(fake_server.ciphers) == 1

        config_out = await integration_service.get_vaultwarden(db)
        assert config_out.synced_count == 1

    await engine.dispose()


@pytest.mark.asyncio
async def test_lucky_proxy_and_ddns_service(fake_server):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        app = App(
            name="jellyfin",
            display_name="Jellyfin",
            category="media",
            yaml_template="",
        )
        other = App(
            name="komga",
            display_name="Komga",
            category="comic",
            yaml_template="",
        )
        orchestration = AppOrchestration(
            name="media",
            display_name="影视",
            category="影视",
            app_composition=[],
        )
        first = AppInstance(
            app=app,
            orchestration=orchestration,
            instance_name="jellyfin",
            config={"url": "http://192.168.1.2:8096"},
            status="running",
        )
        second = AppInstance(
            app=other,
            orchestration=orchestration,
            instance_name="komga",
            config={"url": "http://192.168.1.2:25600"},
            status="running",
        )
        db.add_all([app, other, orchestration, first, second])
        await db.commit()

        await integration_service.save_network(
            db,
            NetworkUpdate(
                enabled=True,
                provider="lucky",
                target_host="192.168.1.10",
                lucky=LuckyConnectionUpdate(
                    url=fake_server.base_url,
                    username="666",
                    password="666",
                    listen_port=443,
                    enable_tls=True,
                ),
            ),
        )

        result = await integration_service.upsert_network_proxy(
            db,
            NetworkProxyRequest(
                app_instance_id=first.id,
                domains=["media.example.com", "media.lan"],
                enable_tls=True,
            ),
            "192.168.1.2",
        )
        assert result.ok is True
        assert fake_server.lucky_rules[0]["ProxyList"][0]["Locations"] == [
            "http://192.168.1.10:8096"
        ]
        assert fake_server.lucky_rules[0]["ProxyList"][0]["Domains"] == [
            "media.example.com",
            "media.lan",
        ]

        apps = await integration_service.list_network_apps(db, "192.168.1.2")
        jellyfin = next(item for item in apps if item.instance_id == first.id)
        assert jellyfin.domain == "media.example.com"
        assert jellyfin.domains == ["media.example.com", "media.lan"]
        assert jellyfin.proxy_enabled is True
        assert jellyfin.enable_tls is True

        certificates = await integration_service.list_network_certificates(db)
        assert certificates[0].id == "cert-1"
        assert certificates[0].domains == ["media.example.com"]

        with pytest.raises(Exception):
            await integration_service.upsert_network_proxy(
                db,
                NetworkProxyRequest(
                    app_instance_id=second.id,
                    domains=["media.lan"],
                ),
                "192.168.1.2",
            )

        state = await integration_service.save_network_ddns(
            db,
            NetworkDdnsUpdate(
                enabled=True,
                ipv4=NetworkDdnsUpdateFamily(
                    enabled=True,
                    provider="cloudflare",
                    access_id="",
                    secret="ddns-token",
                    domains=["home.example.com"],
                    interval=300,
                ),
            ),
        )
        assert state.enabled is True
        assert state.ipv4.domains == ["home.example.com"]
        task = fake_server.ddns_tasks[0]
        assert task["TaskName"] == "OneTap 动态域名 IPv4"
        assert task["Records"][0]["SyncRecordData"]["fullDomainName"] == (
            "home.example.com"
        )

        await integration_service.upsert_network_proxy(
            db,
            NetworkProxyRequest(
                app_instance_id=0,
                custom_name="内部 DNS",
                domains=["dns.lan"],
                target_url="http://[fd00::10]:8053",
            ),
            "192.168.1.2",
        )
        apps = await integration_service.list_network_apps(db, "192.168.1.2")
        custom = next(item for item in apps if item.is_custom)
        assert custom.instance_id < 0
        assert custom.target_url == "http://[fd00::10]:8053"
        custom_rule = next(
            rule
            for rule in fake_server.lucky_rules[0]["ProxyList"]
            if rule["Key"] == f"onetap-{custom.instance_id}"
        )
        assert custom_rule["Locations"] == ["http://[fd00::10]:8053"]
        assert custom_rule["Remark"] == "内部 DNS"

        removed = await integration_service.delete_network_proxy(db, first.id)
        assert removed.ok is True
        assert [
            rule["Key"] for rule in fake_server.lucky_rules[0]["ProxyList"]
        ] == [f"onetap-{custom.instance_id}"]

        removed = await integration_service.delete_network_proxy(
            db, custom.instance_id
        )
        assert removed.ok is True
        assert fake_server.lucky_rules == []

    await engine.dispose()


@pytest.mark.asyncio
async def test_lucky_toggle_off_removes_https_redirect(fake_server):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        app = App(
            name="jellyfin",
            display_name="Jellyfin",
            category="media",
            yaml_template="",
        )
        orchestration = AppOrchestration(
            name="media",
            display_name="影视",
            category="影视",
            app_composition=[],
        )
        instance = AppInstance(
            app=app,
            orchestration=orchestration,
            instance_name="jellyfin",
            config={"url": "http://192.168.1.2:8096"},
            status="running",
        )
        db.add_all([app, orchestration, instance])
        await db.commit()

        await _save_lucky_network(db, fake_server)
        base = {
            "app_instance_id": instance.id,
            "domains": ["media.example.com"],
            "listen_port": 443,
            "enable_tls": True,
        }

        await integration_service.upsert_network_proxy(
            db, NetworkProxyRequest(**base, ssl_forced=True), "192.168.1.2"
        )
        names = {rule["RuleName"] for rule in fake_server.lucky_rules}
        assert names == {"OneTap 443", "OneTap 443 HTTP"}

        await integration_service.upsert_network_proxy(
            db, NetworkProxyRequest(**base, ssl_forced=False), "192.168.1.2"
        )
        names = {rule["RuleName"] for rule in fake_server.lucky_rules}
        assert names == {"OneTap 443"}

    await engine.dispose()


async def _save_lucky_network(db, fake_server):
    await integration_service.save_network(
        db,
        NetworkUpdate(
            enabled=True,
            provider="lucky",
            target_host="192.168.1.10",
            lucky=LuckyConnectionUpdate(
                url=fake_server.base_url,
                username="666",
                password="666",
                listen_port=443,
                enable_tls=True,
            ),
        ),
    )


@pytest.mark.asyncio
async def test_auto_certificate_self_signed(fake_server):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        await _save_lucky_network(db, fake_server)
        result = await integration_service.auto_provision_certificate(
            db,
            NetworkCertificateRequest(domains=["nas.lan"], mode="auto"),
        )

        assert result.ok is True
        assert result.certificate is not None
        assert result.certificate.status == "valid"
        assert result.certificate.domains == ["nas.lan"]
        assert "自签名" in result.message
        created = fake_server.lucky_certs[-1]
        assert created["AddFrom"] == "file"
        assert created["Remark"].startswith("OneTap 自动证书")

    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_certificate_acme_pending(fake_server):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        await _save_lucky_network(db, fake_server)
        result = await integration_service.auto_provision_certificate(
            db,
            NetworkCertificateRequest(
                domains=["video.example.com"], mode="auto"
            ),
        )

        assert result.ok is True
        assert result.certificate is not None
        assert result.certificate.status == "pending"
        assert "申请" in result.message
        created = fake_server.lucky_certs[-1]
        assert created["AddFrom"] == "acme"
        assert created["ExtParams"]["acmeDomains"] == ["video.example.com"]
        assert created["ExtParams"]["acmeDNSServer"] == "http"
        assert (
            created["ExtParams"]["acmeCADirURL"]
            == "https://acme-v02.api.letsencrypt.org/directory"
        )

    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_certificate_reuses_existing(fake_server):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        await _save_lucky_network(db, fake_server)
        before = len(fake_server.lucky_certs)
        result = await integration_service.auto_provision_certificate(
            db,
            NetworkCertificateRequest(
                domains=["media.example.com"], mode="auto"
            ),
        )

        assert result.ok is True
        assert result.certificate is not None
        assert result.certificate.id == "cert-1"
        assert len(fake_server.lucky_certs) == before

    await engine.dispose()


@pytest.mark.asyncio
async def test_auto_certificate_rejects_wildcard_and_internal_acme(fake_server):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        await _save_lucky_network(db, fake_server)
        with pytest.raises(Exception):
            await integration_service.auto_provision_certificate(
                db,
                NetworkCertificateRequest(
                    domains=["*.example.com"], mode="auto"
                ),
            )
        with pytest.raises(Exception):
            await integration_service.auto_provision_certificate(
                db,
                NetworkCertificateRequest(domains=["nas.lan"], mode="acme"),
            )

    await engine.dispose()


@pytest.mark.asyncio
async def test_lucky_ddns_two_families(fake_server):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        await _save_lucky_network(db, fake_server)
        state = await integration_service.save_network_ddns(
            db,
            NetworkDdnsUpdate(
                enabled=True,
                ipv4=NetworkDdnsUpdateFamily(
                    enabled=True,
                    provider="alidns",
                    access_id="key-id",
                    secret="key-secret",
                    domains=["home.example.com"],
                    interval=60,
                    get_type="netInterface",
                    net_interface="ens2",
                ),
                ipv6=NetworkDdnsUpdateFamily(
                    enabled=True,
                    provider="cloudflare",
                    access_id="",
                    secret="cf-token",
                    domains=["home6.example.com"],
                    interval=120,
                    get_type="url",
                    url="https://v6.example.com/ip",
                ),
            ),
        )

        assert state.ipv4.enabled is True
        assert state.ipv6.enabled is True
        assert len(fake_server.ddns_tasks) == 2
        v4 = next(
            task
            for task in fake_server.ddns_tasks
            if task["TaskName"].endswith("IPv4")
        )
        v6 = next(
            task
            for task in fake_server.ddns_tasks
            if task["TaskName"].endswith("IPv6")
        )
        assert v4["V4QueryIPType"] == "netInterface"
        assert v4["V4NetInterface"] == "ens2"
        assert v4["Records"][0]["SyncRecordData"]["type"] == "A"
        assert v6["V6QueryIPType"] == "url"
        assert v6["V6QueryUrl"] == "https://v6.example.com/ip"
        assert v6["Records"][0]["SyncRecordData"]["type"] == "AAAA"
        assert v6["TaskName"] == "OneTap 动态域名 IPv6"

    await engine.dispose()


@pytest.mark.asyncio
async def test_lucky_ddns_interfaces(fake_server):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        await _save_lucky_network(db, fake_server)
        interfaces = await integration_service.network_ddns_interfaces(db)

        assert interfaces.ipv4[0].name == "ens2"
        assert interfaces.ipv4[0].addresses == ["192.168.1.2"]
        assert interfaces.ipv6[0].addresses == ["fd00::2"]

    await engine.dispose()


@pytest.mark.asyncio
async def test_proxy_domains_auto_written_to_ddns(fake_server):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    Session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

    async with Session() as db:
        app = App(
            name="jellyfin",
            display_name="Jellyfin",
            category="media",
            yaml_template="",
        )
        orchestration = AppOrchestration(
            name="media",
            display_name="影视",
            category="影视",
            app_composition=[],
        )
        instance = AppInstance(
            app=app,
            orchestration=orchestration,
            instance_name="jellyfin",
            config={"url": "http://192.168.1.2:8096"},
            status="running",
        )
        db.add_all([app, orchestration, instance])
        await db.commit()

        await _save_lucky_network(db, fake_server)
        await integration_service.save_network_ddns(
            db,
            NetworkDdnsUpdate(
                enabled=True,
                ipv4=NetworkDdnsUpdateFamily(
                    enabled=True,
                    provider="alidns",
                    access_id="key-id",
                    secret="key-secret",
                    domains=["home.example.com"],
                ),
                ipv6=NetworkDdnsUpdateFamily(
                    enabled=True,
                    provider="cloudflare",
                    access_id="",
                    secret="cf-token",
                    domains=["home6.example.com"],
                ),
            ),
        )

        result = await integration_service.upsert_network_proxy(
            db,
            NetworkProxyRequest(
                app_instance_id=instance.id,
                domains=[
                    "media.example.com",
                    "media.lan",
                    "*.media.example.com",
                ],
                network="tcp",
            ),
            "192.168.1.2",
        )

        assert "media.example.com" in result.message
        assert "IPv4 + IPv6" in result.message
        v4 = next(
            task
            for task in fake_server.ddns_tasks
            if task["TaskName"].endswith("IPv4")
        )
        v6 = next(
            task
            for task in fake_server.ddns_tasks
            if task["TaskName"].endswith("IPv6")
        )
        v4_domains = [
            record["SyncRecordData"]["fullDomainName"]
            for record in v4["Records"]
        ]
        v6_domains = [
            record["SyncRecordData"]["fullDomainName"]
            for record in v6["Records"]
        ]
        assert "media.example.com" in v4_domains
        assert "media.example.com" in v6_domains
        assert "media.lan" not in v4_domains
        assert "*.media.example.com" not in v6_domains

    await engine.dispose()
