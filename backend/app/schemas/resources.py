"""资源搜索与订阅请求模型。"""

from typing import Any, Literal

from pydantic import BaseModel, Field

ResourceDomain = Literal["video", "comic", "music", "novel"]


class ResourceSubscribeIn(BaseModel):
    domain: ResourceDomain
    item_id: str = ""
    options: dict[str, Any] = Field(default_factory=dict)
    instance_id: int | None = None
