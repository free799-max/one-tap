"""存储目录服务单元测试。"""

import pytest
import pytest_asyncio
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

import app.models  # noqa: F401 — 注册所有模型到 Base.metadata
from app.core.exceptions import APIException
from app.database import Base
from app.schemas.storage_directory import StorageDirectoryCreate, StorageDirectoryUpdate
from app.services.system import storage_directory_service


@pytest_asyncio.fixture
async def db():
    """提供内存 SQLite 会话。"""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    SessionLocal = async_sessionmaker(
        engine, class_=AsyncSession, expire_on_commit=False
    )
    async with SessionLocal() as session:
        yield session
    await engine.dispose()


def _make_dirs(tmp_path, *names: str) -> list[str]:
    """在 tmp_path 下创建真实目录并返回其路径列表。"""
    paths = []
    for name in names:
        directory = tmp_path / name
        directory.mkdir(exist_ok=True)
        paths.append(str(directory))
    return paths


def _create_data(name: str, host_path: str, mount_path: str, **kwargs):
    return StorageDirectoryCreate(
        name=name, host_path=host_path, mount_path=mount_path, **kwargs
    )


class TestStorageDirectoryService:
    """测试数据目录的增删改查与保存校验。"""

    async def test_create_and_list(self, db, tmp_path):
        """创建后按 sort_order、id 排序返回，visible 反映容器内可见性。"""
        host_a, mount_a = _make_dirs(tmp_path, "host_a", "mount_a")
        host_b, mount_b = _make_dirs(tmp_path, "host_b", "mount_b")

        await storage_directory_service.create(
            db, _create_data("b", host_b, mount_b, sort_order=2)
        )
        await storage_directory_service.create(
            db, _create_data("a", host_a, mount_a, sort_order=1, remark="备注")
        )

        directories = await storage_directory_service.list(db)
        assert [d.name for d in directories] == ["a", "b"]
        assert directories[0].remark == "备注"
        assert directories[0].enabled is True
        # 两个 mount_path 都是真实目录，容器内可见
        assert all(
            storage_directory_service.to_dict(d)["visible"] for d in directories
        )

    async def test_list_container_directories(self, tmp_path):
        """容器内路径浏览：从根目录开始直接浏览文件系统，只返回目录。"""
        target = tmp_path / "media"
        (target / "movies").mkdir(parents=True)
        (tmp_path / "file.txt").write_text("x", encoding="utf-8")

        listing = await storage_directory_service.list_container_directories(
            str(tmp_path)
        )
        assert listing["path"] == str(tmp_path)
        assert [entry["name"] for entry in listing["entries"]] == ["media"]

        root = await storage_directory_service.list_container_directories("/")
        assert root["path"] == "/"
        assert root["entries"]

        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.list_container_directories("relative/path")
        assert exc_info.value.status_code == 400

    async def test_get_not_found(self, db):
        """获取不存在的数据目录抛 404。"""
        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.get(db, 999)
        assert exc_info.value.status_code == 404

    async def test_update(self, db, tmp_path):
        """更新备注/启用状态/排序；路径尾部斜杠被规范化。"""
        host, mount = _make_dirs(tmp_path, "host_u", "mount_u")
        directory = await storage_directory_service.create(
            db, _create_data("u", host + "/", mount + "/")
        )
        assert directory.host_path == host  # 尾部斜杠已去除

        updated = await storage_directory_service.update(
            db,
            directory.id,
            StorageDirectoryUpdate(remark="新备注", enabled=False, sort_order=5),
        )
        assert updated.remark == "新备注"
        assert updated.enabled is False
        assert updated.sort_order == 5
        assert updated.host_path == host

    async def test_update_not_found(self, db):
        """更新不存在的数据目录抛 404。"""
        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.update(
                db, 999, StorageDirectoryUpdate(remark="x")
            )
        assert exc_info.value.status_code == 404

    async def test_delete(self, db, tmp_path):
        """删除后 list 不再返回，重复删除抛 404。"""
        host, mount = _make_dirs(tmp_path, "host_d", "mount_d")
        directory = await storage_directory_service.create(
            db, _create_data("d", host, mount)
        )
        await storage_directory_service.delete(db, directory.id)
        assert await storage_directory_service.list(db) == []
        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.delete(db, directory.id)
        assert exc_info.value.status_code == 404

    async def test_name_unique(self, db, tmp_path):
        """name 冲突时抛 400。"""
        host, mount = _make_dirs(tmp_path, "host_n", "mount_n")
        await storage_directory_service.create(db, _create_data("dup", host, mount))

        host2, mount2 = _make_dirs(tmp_path, "host_n2", "mount_n2")
        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.create(
                db, _create_data("dup", host2, mount2)
            )
        assert exc_info.value.status_code == 400

        # 更新为已有名称同样冲突
        other = await storage_directory_service.create(
            db, _create_data("other", host2, mount2)
        )
        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.update(
                db, other.id, StorageDirectoryUpdate(name="dup")
            )
        assert exc_info.value.status_code == 400

    async def test_non_absolute_path_rejected(self, db, tmp_path):
        """host_path / mount_path 不是绝对路径时抛 400。"""
        host, mount = _make_dirs(tmp_path, "host_r", "mount_r")
        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.create(
                db, _create_data("r1", "relative/path", mount)
            )
        assert exc_info.value.status_code == 400
        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.create(
                db, _create_data("r2", host, "relative/path")
            )
        assert exc_info.value.status_code == 400

    async def test_nested_path_conflict(self, db, tmp_path):
        """与其他数据目录的同类路径相同或嵌套时抛 400。"""
        host, mount = _make_dirs(tmp_path, "host_p", "mount_p")
        await storage_directory_service.create(db, _create_data("p", host, mount))

        # host_path 相同
        mount2 = _make_dirs(tmp_path, "mount_p2")[0]
        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.create(
                db, _create_data("c1", host, mount2)
            )
        assert exc_info.value.status_code == 400

        # host_path 互为父子路径
        with pytest.raises(APIException):
            await storage_directory_service.create(
                db, _create_data("c2", f"{host}/sub", mount2)
            )
        with pytest.raises(APIException):
            await storage_directory_service.create(
                db, _create_data("c3", str(tmp_path), mount2)
            )

        # mount_path 嵌套（使用真实存在的子目录，排除可见性校验干扰）
        mount_sub = _make_dirs(tmp_path, "mount_p/sub2")[0]
        host3 = _make_dirs(tmp_path, "host_p3")[0]
        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.create(
                db, _create_data("c4", host3, mount_sub)
            )
        assert exc_info.value.status_code == 400

    async def test_app_data_nested_conflict(self, db, tmp_path):
        """与应用数据目录（is_app_data 记录）的路径相同或嵌套时抛 400。"""
        host_app, mount_app = _make_dirs(tmp_path, "host_app", "mount_app")
        await storage_directory_service.create(
            db, _create_data("app-data", host_app, mount_app, is_app_data=True)
        )

        host = _make_dirs(tmp_path, "host_m")[0]

        # mount_path 位于应用数据目录 mount_path 下
        sub_mount = _make_dirs(tmp_path, "mount_app/media")[0]
        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.create(
                db, _create_data("m1", host, sub_mount)
            )
        assert exc_info.value.status_code == 400

        # host_path 与应用数据目录 host_path 相同
        mount = _make_dirs(tmp_path, "mount_m")[0]
        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.create(
                db, _create_data("m2", host_app, mount)
            )
        assert exc_info.value.status_code == 400

    async def test_app_data_unique(self, db, tmp_path):
        """全局至多一条应用数据目录：第二条设为 True 后第一条自动变 False。"""
        host_a, mount_a = _make_dirs(tmp_path, "host_ad_a", "mount_ad_a")
        host_b, mount_b = _make_dirs(tmp_path, "host_ad_b", "mount_ad_b")

        first = await storage_directory_service.create(
            db, _create_data("ad-a", host_a, mount_a, is_app_data=True)
        )
        second = await storage_directory_service.create(
            db, _create_data("ad-b", host_b, mount_b, is_app_data=True)
        )

        directories = {d.name: d for d in await storage_directory_service.list(db)}
        assert directories["ad-a"].is_app_data is False
        assert directories["ad-b"].is_app_data is True

        # update 方式设置 is_app_data 同样互斥
        await storage_directory_service.update(
            db, first.id, StorageDirectoryUpdate(is_app_data=True)
        )
        directories = {d.name: d for d in await storage_directory_service.list(db)}
        assert directories["ad-a"].is_app_data is True
        assert directories["ad-b"].is_app_data is False

        # 再次更新自身为 True 不影响自身
        updated = await storage_directory_service.update(
            db, second.id, StorageDirectoryUpdate(is_app_data=True)
        )
        assert updated.is_app_data is True

    async def test_app_data_pair_and_enabled_pairs(self, db, tmp_path):
        """get_app_data_pair 只返回启用的应用数据目录；list_enabled_pairs 排除它。"""
        host_app, mount_app = _make_dirs(tmp_path, "host_pair_app", "mount_pair_app")
        host_data, mount_data = _make_dirs(tmp_path, "host_pair_data", "mount_pair_data")

        assert await storage_directory_service.get_app_data_pair(db) is None

        await storage_directory_service.create(
            db, _create_data("pair-app", host_app, mount_app, is_app_data=True)
        )
        await storage_directory_service.create(
            db, _create_data("pair-data", host_data, mount_data)
        )

        assert await storage_directory_service.get_app_data_pair(db) == (
            host_app,
            mount_app,
        )
        assert await storage_directory_service.list_enabled_pairs(db) == [
            (host_data, mount_data)
        ]

        # 应用数据目录停用后 get_app_data_pair 返回 None
        directories = {d.name: d for d in await storage_directory_service.list(db)}
        await storage_directory_service.update(
            db, directories["pair-app"].id, StorageDirectoryUpdate(enabled=False)
        )
        assert await storage_directory_service.get_app_data_pair(db) is None

    async def test_invisible_mount_path_rejected(self, db, tmp_path):
        """mount_path 在后端容器内不可见时抛 400，且提示 compose 挂载示例。"""
        host = _make_dirs(tmp_path, "host_v")[0]
        invisible = str(tmp_path / "not-exists")

        with pytest.raises(APIException) as exc_info:
            await storage_directory_service.create(
                db, _create_data("v", host, invisible)
            )
        assert exc_info.value.status_code == 400
        assert "不可见" in exc_info.value.message
        assert "docker-compose.yml" in exc_info.value.message
        assert f"- {host}:{invisible}" in exc_info.value.message

    async def test_visible_mount_path_accepted(self, db, tmp_path):
        """mount_path 为真实存在的目录时校验通过。"""
        host, mount = _make_dirs(tmp_path, "host_ok", "mount_ok")
        directory = await storage_directory_service.create(
            db, _create_data("ok", host, mount)
        )
        assert directory.id is not None
        assert storage_directory_service.to_dict(directory)["visible"] is True
