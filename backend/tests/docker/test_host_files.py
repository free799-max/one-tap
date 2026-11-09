"""宿主机文件读写测试。"""

import io
import tarfile

from app.services.docker import host_files


class _FakeImage:
    id = "fake-image"


class _FakeOwnContainer:
    image = _FakeImage()


class _FakeHelper:
    def __init__(self, files: dict[str, str]):
        self.files = files
        self.removed = False

    def get_archive(self, path: str):
        name = path.rsplit("/", 1)[-1]
        if name not in self.files:
            raise FileNotFoundError(name)
        data = self.files[name].encode("utf-8")
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            tar.addfile(info, io.BytesIO(data))
        buffer.seek(0)
        return [buffer.getvalue()], {}

    def put_archive(self, path: str, buffer: io.BytesIO):
        with tarfile.open(fileobj=buffer, mode="r") as tar:
            member = tar.getmembers()[0]
            extracted = tar.extractfile(member)
            self.files[member.name] = (
                extracted.read() if extracted is not None else b""
            ).decode("utf-8")
        return True

    def remove(self, force: bool = False):
        self.removed = True


class _FakeContainers:
    def __init__(self, helper: _FakeHelper):
        self.helper = helper
        self.created: dict | None = None

    def get(self, name: str):
        return _FakeOwnContainer()

    def create(self, **kwargs):
        self.created = kwargs
        return self.helper


class _FakeClient:
    def __init__(self, helper: _FakeHelper):
        self.containers = _FakeContainers(helper)


def test_update_text_prefers_direct_access(tmp_path):
    target = tmp_path / "config.txt"
    target.write_text("hello", encoding="utf-8")

    assert host_files.update_text(target, lambda raw: raw + " world") is True
    assert target.read_text(encoding="utf-8") == "hello world"
    # 内容未变化或放弃更新时不写文件
    assert host_files.update_text(target, lambda raw: raw) is False
    assert host_files.update_text(target, lambda raw: None) is False


def test_update_text_uses_helper_container_when_not_mounted(monkeypatch):
    helper = _FakeHelper(
        {"AdGuardHome.yaml": "users:\n  - name: admin\n    password: old\n"}
    )
    client = _FakeClient(helper)
    monkeypatch.setattr(host_files, "_client", lambda: client)

    changed = host_files.update_text(
        "/definitely-not-mounted/conf/AdGuardHome.yaml",
        lambda raw: raw.replace("old", "new"),
    )

    assert changed is True
    assert "new" in helper.files["AdGuardHome.yaml"]
    assert helper.removed is True
    assert client.containers.created is not None
    assert client.containers.created["volumes"] == {
        "/definitely-not-mounted/conf": {
            "bind": host_files._MOUNT_TARGET,
            "mode": "rw",
        }
    }


def test_update_text_returns_false_when_helper_cannot_read(monkeypatch):
    helper = _FakeHelper({})
    client = _FakeClient(helper)
    monkeypatch.setattr(host_files, "_client", lambda: client)

    assert (
        host_files.update_text(
            "/definitely-not-mounted/conf/AdGuardHome.yaml",
            lambda raw: raw,
        )
        is False
    )
    assert helper.removed is True
