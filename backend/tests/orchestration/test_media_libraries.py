"""Jellyfin/Emby 通用媒体库客户端测试。"""

import pytest

from app.services.apps import media_libraries


class _Response:
    def __init__(self, status_code: int, payload=None, text: str = "") -> None:
        self.status_code = status_code
        self._payload = payload
        self.text = text

    def json(self):
        return self._payload


class _FakeAsyncClient:
    libraries: list[dict] = []
    last_post: dict | None = None
    last_options_post: dict | None = None

    def __init__(self, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return None

    async def get(self, _url: str, headers: dict):
        assert headers["X-Emby-Token"] == "token"
        assert 'Token="token"' in headers["Authorization"]
        return _Response(200, list(self.libraries))

    async def post(
        self,
        url: str,
        params: list[tuple[str, str]] | None = None,
        headers: dict | None = None,
        json: dict | None = None,
    ):
        assert headers is not None
        assert headers["X-Emby-Token"] == "token"
        if url.endswith("/Library/VirtualFolders/LibraryOptions"):
            assert json is not None
            type(self).last_options_post = json
            for library in self.libraries:
                if library.get("ItemId") == json["Id"]:
                    library["LibraryOptions"] = json["LibraryOptions"]
            return _Response(204)
        assert params is not None
        type(self).last_post = {"params": params, "headers": headers}
        query = dict(params)
        self.libraries.append(
            {
                "Name": query["name"],
                "Locations": [query["paths"]],
                "CollectionType": query["collectionType"],
                "ItemId": "new-library",
                "LibraryOptions": {
                    "MetadataSavers": ["Nfo"],
                    "TypeOptions": [
                        {
                            "Type": "Movie",
                            "MetadataFetchers": [],
                            "MetadataFetcherOrder": ["TheMovieDb"],
                        }
                    ],
                },
            }
        )
        return _Response(204)


@pytest.fixture(name="fake_client")
def _fake_client(monkeypatch):
    _FakeAsyncClient.libraries = []
    _FakeAsyncClient.last_post = None
    _FakeAsyncClient.last_options_post = None
    monkeypatch.setattr(media_libraries.httpx, "AsyncClient", _FakeAsyncClient)
    return _FakeAsyncClient


def test_path_is_covered_handles_same_parent_and_child():
    assert media_libraries.path_is_covered("/media/a", ["/media/a"])
    assert media_libraries.path_is_covered("/media/a/b", ["/media/a"])
    assert media_libraries.path_is_covered("/media/a", ["/media/a/b"])
    assert media_libraries.path_is_covered("/media/a", ["/"])
    assert not media_libraries.path_is_covered("/media/a", ["/media/b"])


@pytest.mark.asyncio
async def test_create_media_library_uses_query_and_reads_back(fake_client):
    ok, err = await media_libraries.create_media_library(
        "http://jellyfin:8096",
        "token",
        name="动画电影",
        collection_type="movies",
        path="/mnt/movie/media/电影/动画电影",
    )

    assert ok is True
    assert err is None
    assert fake_client.last_post is not None
    assert fake_client.last_post["params"] == [
        ("name", "动画电影"),
        ("collectionType", "movies"),
        ("paths", "/mnt/movie/media/电影/动画电影"),
        ("refreshLibrary", "true"),
    ]
    options_payload = fake_client.last_options_post
    assert options_payload is not None
    assert options_payload["Id"] == "new-library"
    options = options_payload["LibraryOptions"]
    assert options["MetadataSavers"] == []
    assert options["TypeOptions"] == [
        {
            "Type": "Movie",
            "MetadataFetchers": ["Nfo", "TheMovieDb"],
            "MetadataFetcherOrder": ["Nfo", "TheMovieDb"],
        }
    ]


@pytest.mark.asyncio
async def test_ensure_local_nfo_skips_library_already_configured(fake_client):
    fake_client.libraries = [
        {
            "Name": "电影",
            "Locations": ["/mnt/movie/media/电影"],
            "CollectionType": "movies",
            "ItemId": "movies-1",
            "LibraryOptions": {
                "MetadataSavers": [],
                "TypeOptions": [
                    {
                        "Type": "Movie",
                        "MetadataFetchers": ["Nfo", "TheMovieDb"],
                        "MetadataFetcherOrder": ["Nfo", "TheMovieDb"],
                    }
                ],
            },
        }
    ]

    ok, err = await media_libraries.ensure_local_nfo(
        "http://jellyfin:8096", "token", name="电影"
    )

    assert ok is True
    assert err is None
    assert fake_client.last_options_post is None


def test_enable_local_nfo_options_keeps_other_types():
    options = {
        "MetadataSavers": ["Nfo"],
        "TypeOptions": [
            {"Type": "Series", "MetadataFetcherOrder": ["TheMovieDb"]},
            {"Type": "Audio", "MetadataFetchers": [], "MetadataFetcherOrder": []},
        ],
    }

    updated = media_libraries.enable_local_nfo_options(options)

    assert updated["MetadataSavers"] == []
    series, audio = updated["TypeOptions"]
    assert series["MetadataFetchers"] == ["Nfo", "TheMovieDb"]
    assert series["MetadataFetcherOrder"] == ["Nfo", "TheMovieDb"]
    assert audio == {
        "Type": "Audio",
        "MetadataFetchers": [],
        "MetadataFetcherOrder": [],
    }


@pytest.mark.asyncio
async def test_list_media_libraries_normalizes_paths(fake_client):
    fake_client.libraries = [
        {
            "Name": "电影",
            "Locations": ["/mnt/movie/media/电影/"],
            "CollectionType": "movies",
            "ItemId": "movies-1",
        }
    ]

    ok, libraries, err = await media_libraries.list_media_libraries(
        "http://emby:8096", "token"
    )

    assert ok is True
    assert err is None
    assert libraries == [
        media_libraries.MediaLibraryInfo(
            id="movies-1",
            name="电影",
            paths=("/mnt/movie/media/电影",),
            collection_type="movies",
        )
    ]
