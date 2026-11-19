"""图片代理路由。

封面 URL 使用 HMAC 签名的无状态 token，聊天客户端和浏览器可直接加载。
"""

from fastapi import APIRouter, Response

from app.core.custom_route import CustomAPIRoute
from app.core.exceptions import APIException
from app.services.notifications.assets import fetch_image, resolve_cover_token

router = APIRouter(prefix="/api/assets", tags=["assets"], route_class=CustomAPIRoute)


@router.get("/cover/{token}.jpg")
async def cover_image(token: str):
    url = resolve_cover_token(token)
    if url is None:
        raise APIException("图片地址无效或已过期", 404)
    image = await fetch_image(url)
    if image is None:
        raise APIException("封面获取失败", 404)
    data, content_type = image
    return Response(
        content=data,
        media_type=content_type,
        headers={"Cache-Control": "public, max-age=86400"},
    )
