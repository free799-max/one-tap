"""MoviePilot 分类目录到媒体库目标规划测试。"""

from app.services.orchestration.media_library_targets import (
    build_library_targets,
)


def _category(
    category_id: str,
    media_type: str,
    name: str,
    path: list[str],
    *,
    enabled: bool = True,
) -> dict:
    return {
        "id": category_id,
        "media_type": media_type,
        "name": name,
        "path": path,
        "enabled": enabled,
    }


def _policy() -> dict:
    return {
        "categories": [
            _category("movie.animation", "电影", "动画电影", ["动画电影"]),
            _category("movie.chinese", "电影", "华语电影", ["华语电影"]),
            _category("movie.foreign", "电影", "外语电影", ["外语电影"]),
            _category("movie.uncategorized", "电影", "未分类", ["未分类"]),
            _category("tv.dongman.cn", "电视剧", "国漫", ["国漫"]),
            _category("tv.dongman.jp", "电视剧", "日番", ["日番"]),
            _category("tv.animation.other", "电视剧", "其他动画", ["其他动画"]),
            _category("tv.documentary", "电视剧", "纪录片", ["纪录片"]),
            _category("tv.kids", "电视剧", "儿童", ["儿童"]),
            _category("tv.variety", "电视剧", "综艺", ["综艺"]),
            _category("tv.chinese", "电视剧", "国产剧", ["国产剧"]),
            _category("tv.western", "电视剧", "欧美剧", ["欧美剧"]),
            _category("tv.asian", "电视剧", "亚洲剧", ["亚洲剧"]),
            _category("tv.uncategorized", "电视剧", "未分类", ["未分类"]),
            _category("music.uncategorized", "音乐", "未分类", ["未分类"]),
            _category("music.album", "音乐", "Album", ["Album"]),
            _category(
                "music.compilation",
                "音乐",
                "Album / Compilation",
                ["Album", "Compilation"],
            ),
            _category("music.ep", "音乐", "EP", ["EP"]),
            _category("music.single", "音乐", "Single", ["Single"]),
        ]
    }


def _directory(**overrides) -> dict:
    directory = {
        "name": "默认",
        "library_storage": "local",
        "library_path": "/mnt/movie/media",
        "library_type_folder": True,
        "library_category_folder": True,
        "media_type": None,
        "media_category_id": None,
    }
    directory.update(overrides)
    return directory


def test_default_directories_generate_all_enabled_categories():
    targets = build_library_targets([_directory()], _policy())

    assert len(targets) == 19
    assert sum(target.collection_type == "movies" for target in targets) == 4
    assert sum(target.collection_type == "tvshows" for target in targets) == 10
    assert sum(target.collection_type == "music" for target in targets) == 5
    assert {
        target.name: target.path
        for target in targets
        if target.collection_type == "movies"
    } == {
        "动画电影": "/mnt/movie/media/电影/动画电影",
        "华语电影": "/mnt/movie/media/电影/华语电影",
        "外语电影": "/mnt/movie/media/电影/外语电影",
        "电影未分类": "/mnt/movie/media/电影/未分类",
    }
    assert {
        target.name: target.path
        for target in targets
        if "未分类" in target.name
    } == {
        "电影未分类": "/mnt/movie/media/电影/未分类",
        "剧集未分类": "/mnt/movie/media/电视剧/未分类",
        "音乐未分类": "/mnt/movie/media/音乐/未分类",
    }


def test_disabled_category_is_skipped():
    policy = _policy()
    policy["categories"][0]["enabled"] = False

    targets = build_library_targets([_directory()], policy)

    assert all(target.category_id != "movie.animation" for target in targets)
    assert len(targets) == 18


def test_fixed_category_only_generates_that_category():
    targets = build_library_targets(
        [_directory(media_category_id="movie.animation")],
        _policy(),
    )

    assert len(targets) == 1
    assert targets[0].name == "动画电影"
    assert targets[0].path == "/mnt/movie/media/电影/动画电影"
    assert targets[0].collection_type == "movies"
    assert targets[0].category_id == "movie.animation"


def test_category_path_deduplicates_type_folder():
    policy = {
        "categories": [
            _category("movie.action", "电影", "动作电影", ["电影", "动作"]),
        ]
    }

    targets = build_library_targets(
        [_directory(media_category_id="movie.action")],
        policy,
    )

    assert len(targets) == 1
    assert targets[0].path == "/mnt/movie/media/电影/动作"


def test_category_folder_disabled_falls_back_to_type_libraries():
    targets = build_library_targets(
        [_directory(library_category_folder=False)],
        _policy(),
    )

    assert {(target.collection_type, target.path) for target in targets} == {
        ("movies", "/mnt/movie/media/电影"),
        ("tvshows", "/mnt/movie/media/电视剧"),
        ("music", "/mnt/movie/media/音乐"),
    }


def test_remote_or_invalid_directory_is_ignored():
    targets = build_library_targets(
        [
            _directory(library_storage="alist"),
            _directory(library_path="relative/path"),
        ],
        _policy(),
    )

    assert targets == []
