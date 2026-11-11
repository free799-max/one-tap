"""镜像标签获取测试（不访问网络）。"""

import httpx
import pytest

from app.services.docker import image_service


@pytest.fixture(autouse=True)
def clear_tag_cache():
    image_service._tag_cache.clear()
    yield
    image_service._tag_cache.clear()


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("nginx", ("", "library/nginx")),
        ("nginx:1.27", ("", "library/nginx")),
        ("docker.io/library/nginx", ("", "library/nginx")),
        ("bitnami/redis", ("", "bitnami/redis")),
        ("ghcr.io/open-webui/open-webui", ("ghcr.io", "open-webui/open-webui")),
        ("ghcr.io/open-webui/open-webui:main", ("ghcr.io", "open-webui/open-webui")),
        ("https://ghcr.io/open-webui/open-webui", ("ghcr.io", "open-webui/open-webui")),
        ("localhost:5000/team/app", ("localhost:5000", "team/app")),
        ("quay.io/prometheus/node-exporter@sha256:abc", ("quay.io", "prometheus/node-exporter")),
    ],
)
def test_split_image_ref(image, expected):
    assert image_service._split_image_ref(image) == expected


@pytest.mark.parametrize(
    ("image", "expected"),
    [
        ("nginx", ("docker.io", "library/nginx", "latest", None)),
        ("nginx:1.27", ("docker.io", "library/nginx", "1.27", None)),
        (
            "ghcr.io/open-webui/open-webui:main",
            ("ghcr.io", "open-webui/open-webui", "main", None),
        ),
        (
            "quay.io/prometheus/node-exporter@sha256:abc",
            ("quay.io", "prometheus/node-exporter", "latest", "sha256:abc"),
        ),
        ("localhost:5000/team/app", ("localhost:5000", "team/app", "latest", None)),
    ],
)
def test_parse_image_ref(image, expected):
    assert image_service._parse_image_ref(image) == expected


def test_mirror_ref_and_tag_target():
    assert (
        image_service._mirror_ref("ghcr.io/open-webui/open-webui:main", "https://abc.d.1ms.run/")
        == "abc.d.1ms.run/open-webui/open-webui:main"
    )
    assert (
        image_service._mirror_ref("nginx", "https://mirror.example.com")
        == "mirror.example.com/library/nginx:latest"
    )
    assert image_service._mirror_ref(
        "ghcr.io/a/b@sha256:abc", "https://abc.d.1ms.run"
    ) is None
    assert image_service._original_tag_target(
        "ghcr.io/open-webui/open-webui:main"
    ) == ("ghcr.io/open-webui/open-webui", "main")
    assert image_service._original_tag_target("nginx:1.27") == (
        "library/nginx",
        "1.27",
    )


def test_sort_tags_newest_first():
    tags = [
        {"name": "old", "last_updated": "2024-01-01T00:00:00Z"},
        {"name": "unknown", "last_updated": ""},
        {"name": "new", "last_updated": "2026-01-01T00:00:00Z"},
    ]
    assert [t["name"] for t in image_service._sort_tags(tags)] == ["new", "old", "unknown"]


@pytest.mark.asyncio
async def test_get_image_tags_uses_registry_for_ghcr(monkeypatch):
    calls = []

    async def fake_registry(host, repo, limit):
        calls.append((host, repo, limit))
        return [{"name": "latest", "last_updated": "2026-01-01T00:00:00Z", "size": 1, "digest": "sha256:x"}]

    async def fake_hub(repo, limit):
        raise AssertionError("不应调用 Docker Hub 接口")

    monkeypatch.setattr(image_service, "_registry_tags", fake_registry)
    monkeypatch.setattr(image_service, "_hub_tags", fake_hub)

    service = image_service.ImageService.__new__(image_service.ImageService)
    tags = await service.get_image_tags("ghcr.io/open-webui/open-webui")

    assert calls and calls[0][:2] == ("ghcr.io", "open-webui/open-webui")
    assert [t["name"] for t in tags] == ["latest"]
    # 第二次走缓存，不再发请求
    await service.get_image_tags("ghcr.io/open-webui/open-webui")
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_get_image_tags_raises_when_all_sources_fail(monkeypatch):
    async def fake_registry(host, repo, limit, **kwargs):
        raise RuntimeError("boom")

    monkeypatch.setattr(image_service, "_registry_tags", fake_registry)
    service = image_service.ImageService.__new__(image_service.ImageService)

    with pytest.raises(image_service.ImageTagsUnavailable, match="boom"):
        await service.get_image_tags("ghcr.io/foo/bar")


@pytest.mark.asyncio
async def test_hub_failure_falls_back_to_registry(monkeypatch):
    calls = []

    async def fake_hub(repo, limit):
        raise RuntimeError("hub unreachable")

    async def fake_registry(host, repo, limit, **kwargs):
        calls.append({"host": host, "repo": repo, "root": kwargs.get("root")})
        return [{"name": "latest", "last_updated": "", "size": 0, "digest": "sha256:x"}]

    monkeypatch.setattr(image_service, "_hub_tags", fake_hub)
    monkeypatch.setattr(image_service, "_registry_tags", fake_registry)
    service = image_service.ImageService.__new__(image_service.ImageService)

    tags = await service.get_image_tags("gitlab/gitlab-ce")

    assert [t["name"] for t in tags] == ["latest"]
    assert calls[0]["host"] == "registry-1.docker.io"
    assert calls[0]["repo"] == "gitlab/gitlab-ce"


@pytest.mark.asyncio
async def test_registry_falls_back_to_configured_mirror(monkeypatch):
    calls = []

    async def fake_hub(repo, limit):
        raise RuntimeError("hub unreachable")

    async def fake_registry(host, repo, limit, **kwargs):
        calls.append(kwargs.get("root"))
        if kwargs.get("root") is None:
            raise RuntimeError("registry unreachable")
        return [{"name": "19.5.1-ce.0", "last_updated": "", "size": 0, "digest": "sha256:x"}]

    monkeypatch.setattr(image_service, "_hub_tags", fake_hub)
    monkeypatch.setattr(image_service, "_registry_tags", fake_registry)
    service = image_service.ImageService.__new__(image_service.ImageService)

    tags = await service.get_image_tags(
        "gitlab/gitlab-ce", mirrors=["https://docker.m.daocloud.io"]
    )

    assert [t["name"] for t in tags] == ["19.5.1-ce.0"]
    assert calls == [None, "https://docker.m.daocloud.io"]


@pytest.mark.asyncio
async def test_mirror_provider_only_called_on_failure(monkeypatch):
    provider_calls = []

    async def fake_hub(repo, limit):
        return [{"name": "latest", "last_updated": "", "size": 0, "digest": "sha256:x"}]

    async def provider():
        provider_calls.append(True)
        return ["https://docker.1ms.run"]

    monkeypatch.setattr(image_service, "_hub_tags", fake_hub)
    service = image_service.ImageService.__new__(image_service.ImageService)

    tags = await service.get_image_tags("nginx", mirror_provider=provider)
    assert [t["name"] for t in tags] == ["latest"]
    assert provider_calls == []

    image_service._tag_cache.clear()

    async def failing_hub(repo, limit):
        raise RuntimeError("hub unreachable")

    async def fake_registry(host, repo, limit, **kwargs):
        if kwargs.get("root") is None:
            raise RuntimeError("registry unreachable")
        return [{"name": "mirror-tag", "last_updated": "", "size": 0, "digest": "sha256:y"}]

    monkeypatch.setattr(image_service, "_hub_tags", failing_hub)
    monkeypatch.setattr(image_service, "_registry_tags", fake_registry)

    tags = await service.get_image_tags("nginx", mirror_provider=provider)
    assert [t["name"] for t in tags] == ["mirror-tag"]
    assert provider_calls == [True]


@pytest.mark.asyncio
async def test_refresh_bypasses_tag_cache(monkeypatch):
    calls = []

    async def fake_hub(repo, limit):
        calls.append(repo)
        return [{"name": "latest", "last_updated": "", "size": 0, "digest": "sha256:x"}]

    monkeypatch.setattr(image_service, "_hub_tags", fake_hub)
    service = image_service.ImageService.__new__(image_service.ImageService)

    await service.get_image_tags("nginx")
    await service.get_image_tags("nginx")
    assert len(calls) == 1

    await service.get_image_tags("nginx", refresh=True)
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_mirror_tags_use_v2_protocol(monkeypatch):
    """镜像加速地址按 OCI Distribution 协议列出标签。"""
    requested = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(str(request.url))
        if request.url.path == "/v2/library/nginx/tags/list":
            return httpx.Response(200, json={"name": "library/nginx", "tags": ["latest"]})
        return httpx.Response(200, json={"tags": ["latest"]})

    real_client = httpx.AsyncClient
    monkeypatch.setattr(
        image_service.httpx,
        "AsyncClient",
        lambda **kwargs: real_client(transport=httpx.MockTransport(handler)),
    )

    tags = await image_service._registry_tags(
        "", "library/nginx", 5, root="https://docker.1ms.run"
    )

    assert [t["name"] for t in tags] == ["latest"]
    assert any(
        url.startswith("https://docker.1ms.run/v2/library/nginx/tags/list")
        for url in requested
    )


@pytest.mark.asyncio
async def test_get_image_tags_ghcr_falls_back_to_source_mirror(monkeypatch):
    calls = []

    async def fake_registry(host, repo, limit, *, root=None):
        calls.append((host, root))
        if root is None:
            raise RuntimeError("direct down")
        return [
            {
                "name": "latest",
                "last_updated": "2026-01-01T00:00:00Z",
                "size": 1,
                "digest": "sha256:x",
            }
        ]

    monkeypatch.setattr(image_service, "_registry_tags", fake_registry)
    service = image_service.ImageService.__new__(image_service.ImageService)

    tags = await service.get_image_tags(
        "ghcr.io/open-webui/open-webui",
        source_mirrors={"ghcr.io": ["https://abc.d.1ms.run"]},
    )

    assert [t["name"] for t in tags] == ["latest"]
    assert calls[-1] == ("", "https://abc.d.1ms.run")


def test_managed_release_tags_maps_metadata_and_filters_repository():
    from app.api.docker.images import _managed_release_tags

    releases = [
        {
            "version": "v2.4.2490",
            "image": "ghcr.io/suwayomi/suwayomi-server:v2.4.2490",
            "image_size": 678509168,
            "pushed_at": "2026-11-07T18:27:23Z",
            "published_at": "2026-11-07T21:34:11Z",
            "digest": "sha256:new",
        },
        {
            "version": "latest",
            "image": None,
            "published_at": "2026-10-29T04:04:50Z",
        },
        {
            "version": "old",
            "image": "docker.io/suwayomi/suwayomi-server:old",
        },
        {"version": "", "image": None},
    ]

    assert _managed_release_tags(
        releases, "ghcr.io/suwayomi/suwayomi-server"
    ) == [
        {
            "name": "v2.4.2490",
            "last_updated": "2026-11-07T18:27:23Z",
            "size": 678509168,
            "digest": "sha256:new",
        },
        {
            "name": "latest",
            "last_updated": "2026-10-29T04:04:50Z",
            "size": 0,
            "digest": "",
        },
    ]


@pytest.mark.asyncio
async def test_market_image_tags_prefer_managed_releases(monkeypatch):
    from app.api.docker import images

    async def fake_versions(name):
        assert name == "suwayomi"
        return [
            {
                "version": "v2.4.2490",
                "image": "ghcr.io/suwayomi/suwayomi-server:v2.4.2490",
            }
        ]

    async def unexpected_registry(*args, **kwargs):
        raise AssertionError("管理端有发布版本时不应查询实时仓库")

    monkeypatch.setattr(images.catalog_sync, "fetch_app_versions", fake_versions)
    monkeypatch.setattr(images.docker_manager, "get_image_tags", unexpected_registry)

    tags = await images.get_image_tags(
        image="ghcr.io/suwayomi/suwayomi-server",
        app_name="suwayomi",
        db=None,
        current_user=None,
    )

    assert [tag["name"] for tag in tags] == ["v2.4.2490"]


@pytest.mark.asyncio
async def test_market_image_tags_fall_back_to_registry(monkeypatch):
    from app.api.docker import images

    async def failed_versions(name):
        raise RuntimeError("manage unavailable")

    async def registry_tags(*args, **kwargs):
        return [{"name": "latest", "last_updated": "", "size": 0, "digest": ""}]

    async def no_mirrors(db):
        return {}

    monkeypatch.setattr(images.catalog_sync, "fetch_app_versions", failed_versions)
    monkeypatch.setattr(images.docker_manager, "get_image_tags", registry_tags)
    monkeypatch.setattr(images, "_acceleration_map", no_mirrors)

    tags = await images.get_image_tags(
        image="ghcr.io/suwayomi/suwayomi-server",
        app_name="suwayomi",
        db=None,
        current_user=None,
    )

    assert [tag["name"] for tag in tags] == ["latest"]
