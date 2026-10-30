"""授权 API 的请求与脱敏响应模型。"""

from datetime import datetime

from pydantic import BaseModel, Field


class LicenseActivateRequest(BaseModel):
    license_key: str = Field(min_length=1)
    device_name: str | None = Field(default=None, max_length=255)


class LicenseStatusResponse(BaseModel):
    activated: bool
    valid: bool
    status: str
    edition: str = Field("free", description="free 免费版；licensed 授权版")
    perpetual: bool = Field(False, description="是否为永久授权")
    license_id: str | None = None
    activation_id: str | None = None
    license_key: str | None = Field(None, description="授权码，仅管理员可见")
    device_name: str | None = None
    expires_at: datetime | None = None
    last_refreshed_at: datetime | None = None
    last_error: str | None = None
