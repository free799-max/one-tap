"""自动化实例的源配置输入。"""

from urllib.parse import urlparse

from pydantic import BaseModel, Field, field_validator


class CustomResourceSourceIn(BaseModel):
    id: str = Field(
        min_length=1,
        max_length=100,
        pattern=r"^custom-[A-Za-z0-9-]+$",
    )
    name: str = Field(min_length=1, max_length=100)
    url: str = Field(min_length=1, max_length=2048)

    @field_validator("name")
    @classmethod
    def validate_name(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("源名称不能为空")
        return value

    @field_validator("url")
    @classmethod
    def validate_url(cls, value: str) -> str:
        value = value.strip()
        parsed = urlparse(value)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("源地址必须是有效的 HTTP/HTTPS 地址")
        return value


class ResourceSourceConfigIn(BaseModel):
    enabled: bool = True
    auto_filter: bool = True
    selected_ids: list[str] = Field(default_factory=list, max_length=100)
    custom_sources: list[CustomResourceSourceIn] = Field(
        default_factory=list, max_length=50
    )
    book_source_limit: int | None = Field(default=None, ge=1, le=100000)
