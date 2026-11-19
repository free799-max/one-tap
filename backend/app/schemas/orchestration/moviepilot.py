"""MoviePilot 目录与媒体库同步配置 Schema。"""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Directory(BaseModel):
    """MoviePilot 目录配置，保留上游支持的扩展字段。"""

    model_config = ConfigDict(extra="allow")

    name: str
    download_path: str
    library_path: str
    transfer_type: Literal["link", "softlink", "copy", "move"]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("目录名称不能为空")
        return value

    @field_validator("download_path", "library_path")
    @classmethod
    def validate_path(cls, value: str) -> str:
        value = value.strip()
        if not value.startswith("/"):
            raise ValueError("目录路径必须以 / 开头")
        return value


class DirectoriesUpdateRequest(BaseModel):
    directories: list[Directory]

    @field_validator("directories")
    @classmethod
    def validate_unique_names(cls, value: list[Directory]) -> list[Directory]:
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("目录名称不能重复")
        return value


class MediaLibrary(BaseModel):
    id: str
    name: str
    path: list[str]
    type: str | None = None


class MediaServer(BaseModel):
    name: str
    type: str
    enabled: bool
    sync_libraries: list[str]
    libraries: list[MediaLibrary]
    error: str | None = None


class MediaServerSyncUpdate(BaseModel):
    name: str
    sync_libraries: list[str]

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("媒体服务器名称不能为空")
        return value

    @field_validator("sync_libraries")
    @classmethod
    def validate_library_ids(cls, value: list[str]) -> list[str]:
        library_ids = [item.strip() for item in value]
        if any(not item for item in library_ids):
            raise ValueError("媒体库 ID 不能为空")
        if "all" in library_ids and library_ids != ["all"]:
            raise ValueError("sync_libraries 为 all 时不能同时指定媒体库 ID")
        return library_ids


class MediaLibrariesUpdateRequest(BaseModel):
    media_servers: list[MediaServerSyncUpdate]

    @field_validator("media_servers")
    @classmethod
    def validate_unique_names(
        cls, value: list[MediaServerSyncUpdate]
    ) -> list[MediaServerSyncUpdate]:
        names = [item.name for item in value]
        if len(names) != len(set(names)):
            raise ValueError("媒体服务器名称不能重复")
        return value


class MediaLibraryAutoSyncUpdate(BaseModel):
    """自动创建媒体库的本地调度配置。"""

    enabled: bool = True
    interval_minutes: int = Field(default=1440, ge=1, le=1440)
