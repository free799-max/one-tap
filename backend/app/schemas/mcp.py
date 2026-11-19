"""MCP 聚合管理接口 Schema。"""

from datetime import datetime, timezone

from pydantic import BaseModel, ConfigDict, Field, field_validator


def _as_utc(value: datetime | None) -> datetime | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


class McpConfigOut(BaseModel):
    enabled: bool
    endpoint: str = "/mcp"


class McpConfigUpdate(BaseModel):
    enabled: bool


class McpUpstreamOut(BaseModel):
    instance_id: int
    app_name: str
    display_name: str
    instance_name: str
    status: str
    prefix: str
    available: bool
    unavailable_reason: str


class McpUpstreamToolOut(BaseModel):
    name: str
    title: str | None = None
    description: str = ""
    input_schema: dict
    output_schema: dict | None = None
    annotations: dict | None = None


class McpUpstreamToolsOut(BaseModel):
    tools: list[McpUpstreamToolOut]


class McpCatalogSourceOut(BaseModel):
    id: str
    instance_id: int
    kind: str
    name: str
    available: bool
    error: str = ""
    count: int


class McpCatalogEntryOut(BaseModel):
    kind: str
    entry_id: str
    source: str
    source_name: str
    domain: str
    name: str
    title: str
    description: str = ""
    method: str
    path: str = ""
    public_name: str
    tags: list[str] = Field(default_factory=list)
    risk: str
    transport: str
    required_role: str
    invocable: bool
    blocked_reason: str = ""
    upstream_instance_id: int | None = None


class McpCatalogOut(BaseModel):
    entries: list[McpCatalogEntryOut]
    sources: list[McpCatalogSourceOut]
    generated_at: datetime


class McpCatalogDetailOut(McpCatalogEntryOut):
    input_schema: dict | None = None
    output_schema: dict | None = None
    annotations: dict | None = None


class McpKeyOut(BaseModel):
    key: str
    masked_key: str
    created_at: datetime
    last_used_at: datetime | None

    @field_validator("created_at", "last_used_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value)


class McpConnectOut(BaseModel):
    endpoint: str
    masked_key: str
    config_json: str
    prompt: str


class McpTokenOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    prefix: str
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    last_used_at: datetime | None

    @field_validator("created_at", "expires_at", "revoked_at", "last_used_at")
    @classmethod
    def normalize_datetime(cls, value: datetime | None) -> datetime | None:
        return _as_utc(value)


class McpAuditOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    tool_name: str
    source: str
    upstream_instance_id: int | None
    success: bool
    error_message: str
    duration_ms: int
    created_at: datetime

    @field_validator("created_at")
    @classmethod
    def normalize_datetime(cls, value: datetime) -> datetime:
        result = _as_utc(value)
        assert result is not None
        return result
