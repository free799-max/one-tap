"""OneTap 内 Reader 书架自动同步测试。"""

import zipfile
from unittest.mock import AsyncMock

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册所有模型到 Base.metadata
from app.core.exceptions import APIException
from app.database import Base
from app.models.app_store import App
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    AppOrchestrationInstance,
    ReaderAutoSyncBook,
)
from app.services.apps.reader import clean_markup, fix_epub
from app.services.orchestration.reader_auto import (
    ReaderAutoSyncService,
    normalize_config,
    safe_name,
    validate_config,
)


@pytest_asyncio.fixture(name="session_local")
async def _session_local(monkeypatch):
    """内存 SQLite 会话工厂，接管 reader_auto 模块的 async_session。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    monkeypatch.setattr(
        "app.services.orchestration.reader_auto.async_session", SessionLocal
    )
    yield SessionLocal
    await engine.dispose()


async def _seed_group(
    session_local,
    app_configs: dict | None = None,
    *,
    reader_volumes: list | None = None,
    ermao_volumes: list | None = None,
) -> int:
    async with session_local() as db:
        orch = AppOrchestration(
            name="book-auto",
            display_name="书籍自动化",
            category="books",
            app_composition=[],
            shared_config_schema={},
        )
        db.add(orch)
        await db.flush()
        group = AppOrchestrationInstance(
            orchestration_id=orch.id,
            instance_name="book-auto",
            status="running",
            app_configs=app_configs or {},
        )
        db.add(group)
        await db.flush()

        for name, instance_name, volumes in (
            (
                "reader",
                "book-auto-reader",
                reader_volumes
                if reader_volumes is not None
                else [
                    {
                        "host_path": "/opt/docker/book-auto-reader/reader/storage",
                        "container_path": "/storage",
                        "mode": "rw",
                    },
                    {
                        "host_path": "/setup/books",
                        "container_path": "/storage/localStore/ermao",
                        "mode": "rw",
                    },
                ],
            ),
            (
                "ermao-library",
                "book-auto-ermao-library",
                ermao_volumes
                if ermao_volumes is not None
                else [
                    {
                        "host_path": "/setup/books",
                        "container_path": "/storage/localStore/ermao",
                        "mode": "rw",
                    },
                    {
                        "host_path": "/opt/docker/book-auto-ermao-library/ermao/storage",
                        "container_path": "/app/storage",
                        "mode": "rw",
                    },
                ],
            ),
        ):
            app = App(
                name=name,
                display_name=name,
                yaml_template="services: {}",
            )
            db.add(app)
            await db.flush()
            db.add(
                AppInstance(
                    app_id=app.id,
                    orchestration_group_id=group.id,
                    instance_name=instance_name,
                    config={"volumes": volumes},
                    status="running",
                )
            )
        await db.commit()
        return group.id


def _make_epub(path) -> None:
    with zipfile.ZipFile(path, "w") as archive:
        archive.writestr("mimetype", "application/epub+zip")
        archive.writestr(
            "META-INF/container.xml",
            '<container><rootfile href="x">A & B</rootfile></container>',
        )
        archive.writestr(
            "OEBPS/ch1.xhtml", "<html><body>A & B\x01</body></html>"
        )


def test_normalize_config_fills_defaults_and_norms_paths():
    config = normalize_config(
        {
            "enabled": "true",
            "interval_minutes": 0,
            "library_dir": "/storage/books/",
            "library_host_dir": "/data/books/",
            "max_seconds": 10,
        }
    )

    assert config["enabled"] is True
    assert config["interval_minutes"] == 1
    assert config["library_dir"] == "/storage/books"
    assert config["library_host_dir"] == "/data/books"
    assert config["max_seconds"] == 60
    assert config["scan_enabled"] is True
    assert config["auto_setup"] is True


def test_validate_config_checks_paths_and_scan_credentials():
    with pytest.raises(APIException):
        validate_config(normalize_config({"reader_url": ""}))
    with pytest.raises(APIException):
        validate_config(
            normalize_config(
                {
                    "reader_url": "http://reader",
                    "library_dir": "books",
                    "library_host_dir": "/data/books",
                }
            )
        )
    with pytest.raises(APIException):
        validate_config(
            normalize_config(
                {
                    "reader_url": "http://reader",
                    "library_dir": "/storage/books",
                    "library_host_dir": "/data/books",
                    "ermao_url": "http://ermao",
                    "ermao_email": "",
                }
            )
        )

    validate_config(
        normalize_config(
            {
                "reader_url": "http://reader",
                "library_dir": "/storage/books",
                "library_host_dir": "/data/books",
                "scan_enabled": False,
            }
        )
    )


def test_clean_markup_and_fix_epub(tmp_path):
    assert (
        clean_markup("a & b &amp; &#38; &#x26; &custom;")
        == "a &amp; b &amp; &#38; &#x26; &custom;"
    )

    source = tmp_path / "source.epub"
    fixed = tmp_path / "fixed.epub"
    _make_epub(source)
    outcome = fix_epub(source, fixed)

    assert outcome.entries == 3
    assert len(outcome.failures_before) == 2
    assert outcome.failures_after == []

    with zipfile.ZipFile(fixed) as archive:
        infos = archive.infolist()
        assert infos[0].filename == "mimetype"
        assert infos[0].compress_type == zipfile.ZIP_STORED
        assert archive.read("mimetype") == b"application/epub+zip"
        body = archive.read("OEBPS/ch1.xhtml").decode()
        assert "&amp;" in body
        assert "\x01" not in body
        assert b"&amp;" in archive.read("META-INF/container.xml")


def test_safe_name_strips_unsafe_characters():
    assert safe_name('a/b:c*?"<>|') == "a_b_c______"
    assert safe_name("   ") == "untitled"
    assert len(safe_name("x" * 300)) == 120


@pytest.mark.asyncio
async def test_detect_reads_library_from_instance_volumes(session_local, monkeypatch):
    group_id = await _seed_group(
        session_local,
        {
            "reader": {"url": "http://reader:8080"},
            "ermao-library": {"url": "http://ermao:3000"},
        },
    )
    monkeypatch.setattr(
        ReaderAutoSyncService,
        "_container_mounts",
        AsyncMock(return_value=[]),
    )
    service = ReaderAutoSyncService()

    async with session_local() as db:
        group = await service._load_group(db, group_id)
        detected = await service._detect(group, db)

    assert detected == {
        "reader_url": "http://reader:8080",
        "reader_username": "",
        "reader_password": "",
        "reader_token": "",
        "ermao_url": "http://ermao:3000",
        "library_dir": "/storage/localStore/ermao",
        "library_host_dir": "/setup/books",
        "ermao_library_dir": "/storage/localStore/ermao",
        "ermao_email": "admin@example.com",
        "ermao_password": "",
        "source": "config",
    }


@pytest.mark.asyncio
async def test_panel_reuses_app_auth_settings(session_local, monkeypatch):
    group_id = await _seed_group(
        session_local,
        {
            "reader": {
                "url": "http://reader-new/",
                "auth_type": "basic",
                "username": "reader-user",
                "password": "reader-pass",
                "auto_sync": {
                    "reader_url": "http://reader-old",
                    "reader_username": "old-user",
                    "reader_password": "old-pass",
                    "reader_token": "stale-token",
                },
            },
            "ermao-library": {
                "url": "http://ermao-new/",
                "username": "reader@example.com",
                "password": "ermao-pass",
            },
        },
    )
    monkeypatch.setattr(
        ReaderAutoSyncService,
        "_container_mounts",
        AsyncMock(return_value=[]),
    )
    service = ReaderAutoSyncService()

    async with session_local() as db:
        panel = await service.get_panel(db, group_id)

    config = panel["config"]
    assert config["reader_url"] == "http://reader-new"
    assert config["reader_username"] == "reader-user"
    assert config["reader_password"] == "reader-pass"
    assert config["reader_token"] == ""
    assert config["ermao_url"] == "http://ermao-new"
    assert config["ermao_email"] == "reader@example.com"
    assert config["ermao_password"] == "ermao-pass"


@pytest.mark.asyncio
async def test_save_config_generates_password_and_persists(session_local, monkeypatch):
    group_id = await _seed_group(session_local, {"reader": {"url": "http://reader"}})
    monkeypatch.setattr(
        ReaderAutoSyncService,
        "_detect",
        AsyncMock(
            return_value={
                "reader_url": "http://reader",
                "ermao_url": "http://ermao",
                "library_dir": "/storage/localStore/ermao",
                "library_host_dir": "/setup/books",
                "ermao_library_dir": "/storage/localStore/ermao",
                "ermao_email": "admin@example.com",
                "source": "config",
            }
        ),
    )
    service = ReaderAutoSyncService()

    async with session_local() as db:
        panel = await service.save_config(db, group_id, {"enabled": True})

    assert panel["config"]["enabled"] is True
    assert panel["config"]["library_host_dir"] == "/setup/books"
    assert panel["config"]["ermao_password"]
    async with session_local() as db:
        group = await db.get(AppOrchestrationInstance, group_id)
        auto = group.app_configs["reader"]["auto_sync"]
        assert auto["enabled"] is True
        assert auto["ermao_password"] == panel["config"]["ermao_password"]


class _FakeReaderSession:
    """模拟 Reader 接口，export_epub 写入一个可修复的 EPUB。"""

    exported: list[str] = []

    def __init__(self, *_args, **_kwargs) -> None:
        pass

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def shelf(self):
        return [
            {
                "name": "测试书",
                "bookUrl": "https://source/book",
                "cachedChapterCount": 0,
                "totalChapterNum": 1,
            }
        ]

    async def chapter_list(self, _url):
        return [{"title": "第 1 章"}]

    async def cache_chapter(self, _url, _index):
        return True

    async def export_epub(self, _url, destination):
        self.__class__.exported.append(str(destination))
        _make_epub(destination)


class _FakeErmaoClient:
    """模拟二毛：未初始化、自动建号建库并返回一条导入任务。"""

    instances: list["_FakeErmaoClient"] = []

    def __init__(self, *_args, **_kwargs) -> None:
        self.scanned = False
        self.created_root = ""
        _FakeErmaoClient.instances.append(self)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False

    async def setup_status(self):
        return False

    async def setup(self, email, password):
        self.email = email
        self.password = password

    async def login(self, email, password):
        self.email = email
        self.password = password

    async def libraries(self):
        return []

    async def create_library(self, root_path, name):
        self.created_root = root_path
        return {"id": "lib-1", "rootPath": root_path, "name": name}

    async def scan(self, library_id):
        self.scanned = True
        return {"taskId": "task-1"}

    async def import_tasks(self, _library_id):
        if not self.scanned:
            return []
        return [
            {
                "id": "task-1",
                "state": "SUCCEEDED",
                "sourceName": "测试书.epub",
            }
        ]


@pytest.mark.asyncio
async def test_run_group_exports_epub_and_scans_ermao(
    session_local, tmp_path, monkeypatch
):
    library = tmp_path / "books"
    group_id = await _seed_group(
        session_local,
        {
            "reader": {
                "url": "http://reader",
                "auto_sync": {
                    "enabled": True,
                    "reader_url": "http://reader",
                    "library_dir": "/storage/localStore/ermao",
                    "library_host_dir": str(library),
                    "ermao_library_dir": "/storage/localStore/ermao",
                    "ermao_url": "http://ermao",
                    "ermao_email": "admin@example.com",
                    "ermao_password": "secret12345",
                    "scan_enabled": True,
                    "scan_wait_seconds": 10,
                },
            }
        },
    )
    monkeypatch.setattr(
        "app.services.orchestration.reader_auto.ReaderSession",
        _FakeReaderSession,
    )
    monkeypatch.setattr(
        "app.services.orchestration.reader_auto.ErmaoClient",
        _FakeErmaoClient,
    )
    monkeypatch.setattr(
        ReaderAutoSyncService,
        "_detect",
        AsyncMock(return_value={}),
    )
    service = ReaderAutoSyncService()

    await service._run_group(group_id, "manual", force=True)

    destination = library / "测试书.epub"
    assert destination.is_file()
    with zipfile.ZipFile(destination) as archive:
        assert archive.infolist()[0].filename == "mimetype"
        assert b"&amp;" in archive.read("OEBPS/ch1.xhtml")

    fake = _FakeErmaoClient.instances[-1]
    assert fake.created_root == "/storage/localStore/ermao"

    async with session_local() as db:
        state = (
            await db.execute(
                ReaderAutoSyncBook.__table__.select().where(
                    ReaderAutoSyncBook.group_id == group_id
                )
            )
        ).first()
        assert state.status == "done"
        assert state.exported_total == 1
        group = await db.get(AppOrchestrationInstance, group_id)
        auto = group.app_configs["reader"]["auto_sync"]
        assert auto["ermao_lib_id"] == "lib-1"
        assert auto["last_result"]["status"] == "ok"
        assert auto["last_result"]["exported"] == 1
        assert auto["last_result"]["cached"] == 1
        assert auto["last_result"]["ermao"]["ok"] is True
