"""Nginx Proxy Manager / DDNS-Go 客户端与服务层联调测试。

使用本地 HTTP 服务模拟两个外部系统，验证登录、代理主机维护、
DDNS-Go 配置合并与删除都不会破坏用户已有配置。
"""

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.core.exceptions import APIException
from app.database import Base
from app.models.app_store import App
from app.models.orchestration import AppInstance, AppOrchestration
from app.schemas.integration import (
    DdnsGoConnectionUpdate,
    NetworkDdnsUpdate,
    NetworkDdnsUpdateFamily,
    NetworkProxyRequest,
    NetworkUpdate,
    NpmConnectionUpdate,
)
from app.services.integrations import integration_service
from app.services.integrations.discovery import DeployedApp
from app.services.integrations.ddnsgo import (
    DdnsGoClient,
    build_entry,
    merge_entry,
)
from app.services.integrations.npm import NpmClient, NpmError, build_proxy_host
from app.services.integrations.service import (
    _ddns_families_for_network,
    _deployed_in_docker,
    _is_ddns_domain,
    _network_app_list,
)


@pytest.fixture(autouse=True)
def _skip_docker_check(monkeypatch):
    """单元测试不依赖宿主机 Docker，跳过容器存在性校验。"""

    async def _no_docker():
        return None

    monkeypatch.setattr(
        "app.services.integrations.service._docker_app_keys", _no_docker
    )


class _FakeNpm:
    def __init__(self):
        self.hosts: list[dict] = []
        self.certificates = [
            {
                "id": 3,
                "nice_name": "example.com",
                "domain_names": ["example.com"],
                "expires_on": "2027-01-01T00:00:00.000Z",
            }
        ]
        self._next_id = 1
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

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

            def _json(self):
                length = int(self.headers.get("content-length", 0) or 0)
                if not length:
                    return None
                return json.loads(self.rfile.read(length))

            def _send(self, payload, status=200):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _authorized(self):
                return self.headers.get("Authorization") == "Bearer npm-token"

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/api/nginx/proxy-hosts":
                    if not self._authorized():
                        return self._send(
                            {"error": {"message": "Unauthorized"}}, 401
                        )
                    return self._send(outer.hosts)
                if path == "/api/nginx/certificates":
                    if not self._authorized():
                        return self._send(
                            {"error": {"message": "Unauthorized"}}, 401
                        )
                    return self._send(outer.certificates)
                return self._send({"error": {"message": "not found"}}, 404)

            def do_POST(self):
                path = urlsplit(self.path).path
                body = self._json()
                if path == "/api/tokens":
                    if body.get("secret") == "needs-2fa":
                        return self._send({"requires_2fa": True})
                    if body.get("secret") != "npm-password":
                        return self._send(
                            {"error": {"message": "Invalid email or password"}}, 401
                        )
                    return self._send({"token": "npm-token", "expires": "x"})
                if path == "/api/nginx/proxy-hosts":
                    if not self._authorized():
                        return self._send(
                            {"error": {"message": "Unauthorized"}}, 401
                        )
                    host = dict(body)
                    host["id"] = outer._next_id
                    outer._next_id += 1
                    outer.hosts.append(host)
                    return self._send(host, 201)
                return self._send({"error": {"message": "not found"}}, 404)

            def do_PUT(self):
                path = urlsplit(self.path).path
                if not self._authorized():
                    return self._send({"error": {"message": "Unauthorized"}}, 401)
                host_id = int(path.rsplit("/", 1)[-1])
                body = self._json()
                for index, host in enumerate(outer.hosts):
                    if host["id"] == host_id:
                        outer.hosts[index] = {**host, **body}
                        return self._send(outer.hosts[index])
                return self._send({"error": {"message": "not found"}}, 404)

            def do_DELETE(self):
                path = urlsplit(self.path).path
                if not self._authorized():
                    return self._send({"error": {"message": "Unauthorized"}}, 401)
                host_id = int(path.rsplit("/", 1)[-1])
                outer.hosts = [h for h in outer.hosts if h["id"] != host_id]
                return self._send(True)

        return Handler


class _FakeDdnsGo:
    def __init__(self):
        self.entries = [
            {
                "Name": "保留配置",
                "DnsName": "alidns",
                "DnsID": "existing-id",
                "DnsSecret": "existing-secret",
                "Ipv4Enable": True,
                "Ipv4Domains": "keep.example.com",
                "Ipv6Enable": False,
                "Ipv6Domains": "",
            }
        ]
        self.last_save: dict | None = None
        self._server = ThreadingHTTPServer(("127.0.0.1", 0), self._handler())
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

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

    def page(self) -> str:
        payload = json.dumps(self.entries, ensure_ascii=False)
        escaped = json.dumps(payload)[1:-1]
        return f"""
        <html><body>
        <form id="formDnsConf"></form>
        <input class="form-control" name="Username" id="Username" value="admin" />
        <input type="checkbox" id="NotAllowWanAccess" checked />
        <input class="form-control" name="WebhookURL" id="WebhookURL" value="" />
        <textarea id="WebhookRequestBody"></textarea>
        <textarea id="WebhookHeaders"></textarea>
        <span class="badge badge-secondary">v6.1.0</span>
        <script>
          window.__DDNS_GO_LANG__ = "zh";
          reloadConf("{escaped}");
        </script>
        </body></html>
        """

    def _handler(self):
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                return

            def _send(self, payload, status=200):
                body = json.dumps(payload).encode()
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def _send_html(self, text, status=200):
                body = text.encode()
                self.send_response(status)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def do_GET(self):
                path = urlsplit(self.path).path
                if path == "/":
                    cookie = self.headers.get("Cookie", "")
                    if "token=ddns-token" not in cookie:
                        return self._send_html("login page")
                    return self._send_html(outer.page())
                if path == "/login":
                    return self._send_html("login page")
                return self._send_html("not found", 404)

            def do_POST(self):
                path = urlsplit(self.path).path
                length = int(self.headers.get("content-length", 0) or 0)
                body = json.loads(self.rfile.read(length)) if length else None
                if path == "/loginFunc":
                    if body.get("Password") != "ddns-pass":
                        return self._send(
                            {"Code": 500, "Msg": "用户名或密码错误"}
                        )
                    self.send_response(200)
                    payload = json.dumps(
                        {"Code": 200, "Msg": "登录成功", "Data": "ddns-token"}
                    ).encode()
                    self.send_header("Content-Type", "application/json")
                    self.send_header(
                        "Set-Cookie", "token=ddns-token; Path=/; HttpOnly"
                    )
                    self.send_header("Content-Length", str(len(payload)))
                    self.end_headers()
                    self.wfile.write(payload)
                    return
                if path == "/save":
                    outer.last_save = body
                    outer.entries = body.get("DnsConf") or []
                    return self._send({"result": "ok", "dnsConf": "[]"})
                return self._send({"result": "not found"}, 404)

        return Handler


@pytest.fixture
def fake_npm():
    server = _FakeNpm().start()
    yield server
    server.stop()


@pytest.fixture
def fake_ddnsgo():
    server = _FakeDdnsGo().start()
    yield server
    server.stop()


def test_build_proxy_host_parses_target():
    payload = build_proxy_host(
        7,
        domains=["app.example.com", "app.lan"],
        target_url="https://192.168.1.2:8443/path",
        certificate_id=3,
        ssl_forced=True,
    )
    assert payload["forward_scheme"] == "https"
    assert payload["forward_host"] == "192.168.1.2"
    assert payload["forward_port"] == 8443
    assert payload["domain_names"] == ["app.example.com", "app.lan"]
    assert payload["certificate_id"] == 3
    assert payload["ssl_forced"] is True
    assert payload["meta"]["onetap_instance_id"] == "7"


@pytest.mark.asyncio
async def test_npm_login_and_proxy_host_crud(fake_npm):
    async with NpmClient(fake_npm.base_url, "user@example.com", "npm-password") as client:
        await client.login()
        payload = build_proxy_host(
            7,
            domains=["app.example.com"],
            target_url="http://192.168.1.2:8096",
            certificate_id=0,
        )
        await client.save_proxy_host(7, payload)

        assert len(fake_npm.hosts) == 1
        assert fake_npm.hosts[0]["domain_names"] == ["app.example.com"]
        assert fake_npm.hosts[0]["meta"]["onetap_instance_id"] == "7"

        payload["domain_names"] = ["new.example.com"]
        await client.save_proxy_host(7, payload)
        assert len(fake_npm.hosts) == 1
        assert fake_npm.hosts[0]["domain_names"] == ["new.example.com"]

        assert await client.delete_proxy_host(7) is True
        assert fake_npm.hosts == []

        certificates = await client.list_certificates()
        assert certificates[0]["nice_name"] == "example.com"


@pytest.mark.asyncio
async def test_npm_rejects_two_factor_account(fake_npm):
    async with NpmClient(fake_npm.base_url, "user@example.com", "needs-2fa") as client:
        with pytest.raises(NpmError) as exc:
            await client.login()
    assert "两步验证" in exc.value.message


def _ddns_page(entries):
    payload = json.dumps(entries, ensure_ascii=False)
    escaped = json.dumps(payload)[1:-1]
    return f'<form id="formDnsConf"></form><script>reloadConf("{escaped}");</script>'


@pytest.mark.asyncio
async def test_ddnsgo_parse_and_login(fake_ddnsgo):
    async with DdnsGoClient(fake_ddnsgo.base_url, "admin", "ddns-pass") as client:
        assert await client.test() == "v6.1.0"
        config = await client.fetch_config()
    assert config.global_conf["Username"] == "admin"
    assert len(config.dns_conf) == 1


def test_ddnsgo_merge_preserves_other_entries():
    entries = [{"Name": "保留配置", "DnsSecret": "existing"}]
    entry = build_entry(
        enabled=True,
        provider="cloudflare",
        access_id="",
        secret="token",
        ipv4={
            "enabled": True,
            "domains": ["home.example.com"],
            "get_type": "url",
            "url": "",
            "net_interface": "",
            "ip_reg": "",
        },
    )
    merged = merge_entry(entries, entry)
    assert merged[0]["Name"] == "保留配置"
    assert merged[1]["Name"] == "OneTap"
    assert merged[1]["Ipv4Domains"] == "home.example.com"
    assert merged[1]["Ipv6Enable"] is False


@pytest.mark.asyncio
async def test_network_service_npm_proxy(fake_npm, fake_ddnsgo):
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

        await integration_service.save_network(
            db,
            NetworkUpdate(
                enabled=True,
                provider="npm",
                target_host="192.168.1.10",
                npm=NpmConnectionUpdate(
                    url=fake_npm.base_url,
                    email="user@example.com",
                    password="npm-password",
                    certificate_id=3,
                    ssl_forced=True,
                ),
                ddnsgo=DdnsGoConnectionUpdate(
                    url=fake_ddnsgo.base_url,
                    username="admin",
                    password="ddns-pass",
                ),
            ),
        )

        result = await integration_service.upsert_network_proxy(
            db,
            NetworkProxyRequest(
                app_instance_id=instance.id,
                domains=["media.example.com", "media.lan"],
                certificate_id=3,
                ssl_forced=True,
            ),
            "192.168.1.2",
        )
        assert result.ok is True
        assert fake_npm.hosts[0]["forward_host"] == "192.168.1.10"
        assert fake_npm.hosts[0]["forward_port"] == 8096
        assert fake_npm.hosts[0]["certificate_id"] == 3
        assert fake_npm.hosts[0]["domain_names"] == [
            "media.example.com",
            "media.lan",
        ]

        apps = await integration_service.list_network_apps(db, "192.168.1.2")
        jellyfin = next(item for item in apps if item.instance_id == instance.id)
        assert jellyfin.proxy_enabled is True

        certificates = await integration_service.list_npm_certificates(db)
        assert certificates[0].id == 3

        state = await integration_service.save_network_ddns(
            db,
            NetworkDdnsUpdate(
                enabled=True,
                ipv4=NetworkDdnsUpdateFamily(
                    enabled=True,
                    provider="cloudflare",
                    access_id="",
                    secret="cf-token",
                    domains=["home.example.com"],
                ),
            ),
        )
        assert state.ipv4.manual_domains == ["home.example.com"]
        assert state.ipv4.auto_domains == ["media.example.com"]
        saved = fake_ddnsgo.last_save
        assert saved is not None
        entry = next(item for item in saved["DnsConf"] if item["Name"] == "OneTap")
        assert entry["Ipv4Domains"].splitlines() == [
            "home.example.com",
            "media.example.com",
        ]

        await integration_service.delete_network_proxy(db, instance.id)
        assert fake_npm.hosts == []
        state = await integration_service.network_ddns_state(db)
        assert state.ipv4.manual_domains == ["home.example.com"]
        assert state.ipv4.auto_domains == []
        saved = fake_ddnsgo.last_save
        assert saved is not None
        entry = next(item for item in saved["DnsConf"] if item["Name"] == "OneTap")
        assert entry["Ipv4Domains"] == "home.example.com"

        with pytest.raises(APIException, match="自定义应用名称"):
            await integration_service.upsert_network_proxy(
                db,
                NetworkProxyRequest(
                    app_instance_id=0,
                    domains=["internal.lan"],
                    target_url="http://192.168.1.30:9000",
                ),
                "192.168.1.2",
            )
        with pytest.raises(APIException, match="后端地址"):
            await integration_service.upsert_network_proxy(
                db,
                NetworkProxyRequest(
                    app_instance_id=0,
                    custom_name="内部服务",
                    domains=["internal.lan"],
                ),
                "192.168.1.2",
            )

        await integration_service.upsert_network_proxy(
            db,
            NetworkProxyRequest(
                app_instance_id=0,
                custom_name="内部服务",
                domains=["internal.lan"],
                target_url="192.168.1.30:9000",
            ),
            "192.168.1.2",
        )
        apps = await integration_service.list_network_apps(db, "192.168.1.2")
        custom = next(item for item in apps if item.is_custom)
        assert custom.instance_id < 0
        assert custom.display_name == "内部服务"
        assert custom.target_url == "http://192.168.1.30:9000"
        assert custom.proxy_enabled is True
        assert fake_npm.hosts[0]["meta"]["onetap_instance_id"] == str(
            custom.instance_id
        )

        await integration_service.upsert_network_proxy(
            db,
            NetworkProxyRequest(
                app_instance_id=custom.instance_id,
                custom_name="内部 DNS",
                domains=["internal.lan"],
                target_url="http://[fd00::10]:8053",
            ),
            "192.168.1.2",
        )
        apps = await integration_service.list_network_apps(db, "192.168.1.2")
        updated = next(item for item in apps if item.is_custom)
        assert updated.instance_id == custom.instance_id
        assert updated.display_name == "内部 DNS"
        assert updated.target_url == "http://[fd00::10]:8053"
        assert len(fake_npm.hosts) == 1
        assert fake_npm.hosts[0]["forward_host"] == "fd00::10"
        assert fake_npm.hosts[0]["forward_port"] == 8053

        with pytest.raises(APIException, match="已被其他应用使用"):
            await integration_service.upsert_network_proxy(
                db,
                NetworkProxyRequest(
                    app_instance_id=0,
                    custom_name="另一个服务",
                    domains=["internal.lan"],
                    target_url="http://192.168.1.31:9000",
                ),
                "192.168.1.2",
            )

        await integration_service.delete_network_proxy(db, custom.instance_id)
        assert fake_npm.hosts == []

        state = await integration_service.save_network_ddns(
            db,
            NetworkDdnsUpdate(
                enabled=True,
                ipv4=NetworkDdnsUpdateFamily(
                    enabled=True,
                    provider="cloudflare",
                    access_id="",
                    secret="cf-token",
                    domains=["home.example.com"],
                    interval=300,
                ),
            ),
        )
        assert state.tasks[0].task_name == "OneTap"
        assert state.tasks[0].domains == ["home.example.com"]
        saved = fake_ddnsgo.last_save
        assert saved is not None
        assert saved["Password"] == ""
        assert saved["Username"] == "admin"
        assert [item["Name"] for item in saved["DnsConf"]] == [
            "保留配置",
            "OneTap",
        ]
        assert saved["DnsConf"][1]["DnsSecret"] == "cf-token"

        await integration_service.delete_network_ddns(db)
        assert [item["Name"] for item in fake_ddnsgo.entries] == ["保留配置"]

    await engine.dispose()


def test_network_app_list_drops_stale_duplicates():
    def app(
        instance_id: int,
        name: str,
        instance_name: str,
        status: str,
    ) -> DeployedApp:
        return DeployedApp(
            instance_id=instance_id,
            app_name=name,
            display_name=name,
            instance_name=instance_name,
            status=status,
        )

    apps = [
        app(16, "moviepilot", "moviepilot", "error"),
        app(18, "moviepilot", "moviepilot", "error"),
        app(19, "moviepilot", "moviepilot", "error"),
        app(20, "moviepilot", "moviepilot-test", "error"),
        app(173, "moviepilot", "movie-moviepilot", "running"),
        app(33, "qbittorrent", "qbittorrent", "error"),
        app(174, "qbittorrent", "movie-qbittorrent", "running"),
        app(163, "cloudreve", "cloudreve", "error"),
        app(164, "cloudreve", "cloudreve", "error"),
        app(165, "homepage", "homepage", "error"),
    ]

    result = _network_app_list(apps)

    assert [item.instance_id for item in result] == [164, 165, 173, 174]


def test_deployed_in_docker_keeps_only_live_records():
    def app(
        instance_id: int,
        name: str,
        instance_name: str,
        *,
        project_id: int | None = None,
        container_name: str | None = None,
    ) -> DeployedApp:
        return DeployedApp(
            instance_id=instance_id,
            app_name=name,
            display_name=name,
            instance_name=instance_name,
            status="error",
            project_id=project_id,
            container_name=container_name,
        )

    apps = [
        app(16, "moviepilot", "moviepilot"),
        app(173, "moviepilot", "movie-moviepilot", project_id=169),
        app(163, "cloudreve", "cloudreve"),
        app(165, "homepage", "homepage", container_name="homepage"),
        app(174, "qbittorrent", "movie-qbittorrent"),
    ]

    result = _deployed_in_docker(
        apps,
        project_names={"movie-moviepilot", "cloudreve"},
        container_keys={"homepage", "movie-qbittorrent"},
        project_by_id={169: "movie-moviepilot"},
    )

    assert [item.instance_id for item in result] == [173, 163, 165, 174]


def test_ddns_domain_filter_and_family_mapping():
    assert _is_ddns_domain("media.example.com") is True
    assert _is_ddns_domain("Media.Example.com") is True
    assert _is_ddns_domain("media.lan") is False
    assert _is_ddns_domain("nas") is False
    assert _is_ddns_domain("192.168.1.10") is False
    assert _is_ddns_domain("*.example.com") is False

    assert _ddns_families_for_network("tcp4") == ["IPv4"]
    assert _ddns_families_for_network("tcp6") == ["IPv6"]
    assert _ddns_families_for_network("tcp") == ["IPv4", "IPv6"]
