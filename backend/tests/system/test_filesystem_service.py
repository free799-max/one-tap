"""FilesystemService 单元测试。"""

import pytest

from app.core.exceptions import APIException
from app.services.host.filesystem_service import FilesystemService


class TestFilesystemService:
    """测试通用文件系统服务。"""

    def test_list_directories(self, tmp_path):
        """列出目录条目。"""
        service = FilesystemService()
        (tmp_path / "a").mkdir()
        (tmp_path / "b").mkdir()
        (tmp_path / "file.txt").write_text("x")

        result = service.list_directories(str(tmp_path))

        assert result["path"] == str(tmp_path)
        names = {e["name"] for e in result["entries"]}
        assert names == {"a", "b"}

    def test_list_directories_with_root_path_defense(self, tmp_path):
        """超出 root_path 的路径应被禁止。"""
        service = FilesystemService()
        allowed = tmp_path / "allowed"
        allowed.mkdir()
        outside = tmp_path / ".." / "outside"

        with pytest.raises(APIException):
            service.list_directories(str(outside), root_paths=[str(allowed)])

    def test_list_directories_with_root_path_slash(self, tmp_path):
        """root_path 为 / 时不应误判子目录越界。"""
        service = FilesystemService()
        (tmp_path / "subdir").mkdir()

        result = service.list_directories(str(tmp_path), root_paths=["/"])

        assert result["path"] == str(tmp_path)
        names = {e["name"] for e in result["entries"]}
        assert "subdir" in names

    def test_list_directories_with_multiple_root_paths(self, tmp_path):
        """目标路径落在任一 root 下（含 root 本身）即允许。"""
        service = FilesystemService()
        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        root_a.mkdir()
        root_b.mkdir()
        outside = tmp_path / "outside"
        outside.mkdir()

        roots = [str(root_a), str(root_b)]
        assert service.list_directories(str(root_b), root_paths=roots)["path"] == str(
            root_b.resolve()
        )
        assert service.list_directories(str(root_a), root_paths=roots)["path"] == str(
            root_a.resolve()
        )
        with pytest.raises(APIException) as exc_info:
            service.list_directories(str(outside), root_paths=roots)
        assert exc_info.value.status_code == 403

    def test_delete_directory_any_root_itself_forbidden(self, tmp_path):
        """删除任一允许访问的根目录本身都应被禁止。"""
        service = FilesystemService()
        root_a = tmp_path / "root_a"
        root_b = tmp_path / "root_b"
        root_a.mkdir()
        root_b.mkdir()

        with pytest.raises(APIException) as exc_info:
            service.delete_directory(
                str(root_b), root_paths=[str(root_a), str(root_b)]
            )
        assert exc_info.value.status_code == 403
        assert root_b.is_dir()

    def test_create_directory(self, tmp_path):
        """创建目录。"""
        service = FilesystemService()
        result = service.create_directory(
            str(tmp_path / "newdir"),
            root_paths=[str(tmp_path)],
        )
        assert result["name"] == "newdir"
        assert (tmp_path / "newdir").is_dir()

    def test_create_directory_already_exists(self, tmp_path):
        """目录已存在时报错。"""
        service = FilesystemService()
        (tmp_path / "exists").mkdir()

        with pytest.raises(APIException):
            service.create_directory(
                str(tmp_path / "exists"),
                root_paths=[str(tmp_path)],
            )

    def test_create_directory_parent_missing(self, tmp_path):
        """父目录不存在时报错。"""
        service = FilesystemService()

        with pytest.raises(APIException):
            service.create_directory(
                str(tmp_path / "missing" / "child"),
                root_paths=[str(tmp_path)],
            )

    def test_create_directory_traversal_defense(self, tmp_path):
        """创建目录时路径越界应被禁止。"""
        service = FilesystemService()

        with pytest.raises(APIException):
            service.create_directory(
                "/etc/onetap-test-dir",
                root_paths=[str(tmp_path)],
            )

    def test_rename_directory(self, tmp_path):
        """重命名目录。"""
        service = FilesystemService()
        (tmp_path / "old").mkdir()

        result = service.rename_directory(
            str(tmp_path / "old"),
            "new",
            root_paths=[str(tmp_path)],
        )

        assert result["name"] == "new"
        assert (tmp_path / "new").is_dir()
        assert not (tmp_path / "old").exists()

    def test_rename_directory_target_exists(self, tmp_path):
        """重命名目标已存在时报错。"""
        service = FilesystemService()
        (tmp_path / "old").mkdir()
        (tmp_path / "new").mkdir()

        with pytest.raises(APIException):
            service.rename_directory(
                str(tmp_path / "old"),
                "new",
                root_paths=[str(tmp_path)],
            )

    def test_rename_directory_traversal_defense(self, tmp_path):
        """重命名时路径越界应被禁止。"""
        service = FilesystemService()

        with pytest.raises(APIException):
            service.rename_directory(
                "/etc",
                "new",
                root_paths=[str(tmp_path)],
            )

    def test_delete_directory(self, tmp_path):
        """删除目录（含非空）。"""
        service = FilesystemService()
        (tmp_path / "del" / "sub").mkdir(parents=True)
        (tmp_path / "del" / "file.txt").write_text("x")

        service.delete_directory(
            str(tmp_path / "del"),
            root_paths=[str(tmp_path)],
        )

        assert not (tmp_path / "del").exists()

    def test_delete_directory_traversal_defense(self, tmp_path):
        """删除时路径越界应被禁止。"""
        service = FilesystemService()

        with pytest.raises(APIException):
            service.delete_directory(
                "/etc",
                root_paths=[str(tmp_path)],
            )

    def test_delete_directory_root_path_itself_forbidden(self, tmp_path):
        """删除允许访问的根目录本身应被禁止。"""
        service = FilesystemService()
        (tmp_path / "sub").mkdir()

        with pytest.raises(APIException) as exc_info:
            service.delete_directory(str(tmp_path), root_paths=[str(tmp_path)])

        assert exc_info.value.status_code == 403
        assert (tmp_path / "sub").is_dir()  # 目录未被删除

    def test_delete_filesystem_root_forbidden(self):
        """删除文件系统根目录应被禁止。"""
        service = FilesystemService()

        with pytest.raises(APIException) as exc_info:
            service.delete_directory("/", root_paths=["/"])

        assert exc_info.value.status_code == 403
