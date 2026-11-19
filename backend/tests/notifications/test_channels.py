"""通知渠道请求格式测试。"""

import json

import httpx
import pytest

from app.services.notifications.channels.base import ChannelError, ChannelMessage
from app.services.notifications.channels.dingtalk import DingTalkChannel
from app.services.notifications.channels.dingtalk_app import DingTalkAppChannel
from app.services.notifications.channels.feishu import FeishuChannel
from app.services.notifications.channels.qqbot import QQBotChannel
from app.services.notifications.channels.wechatclawbot import WeChatClawBotChannel


def _mock_client(handler, monkeypatch, module):
    original = httpx.AsyncClient

    def factory(*args, **kwargs):
        kwargs.pop("timeout", None)
        return original(transport=httpx.MockTransport(handler), timeout=5)

    monkeypatch.setattr(module.httpx, "AsyncClient", factory)


def _message(**overrides):
    values = {
        "title": "测试标题",
        "markdown": "**正文**",
        "text": "正文",
        "image_url": "https://img.example/a.jpg",
        "link": "https://onetap.local/item",
    }
    values.update(overrides)
    return ChannelMessage(**values)


async def test_feishu_sends_card_with_image(monkeypatch):
    import app.services.notifications.channels.feishu as feishu_module

    calls = []

    async def fake_fetch(url):
        return b"\xff\xd8jpeg", "image/jpeg"

    monkeypatch.setattr(feishu_module, "fetch_image", fake_fetch)

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.method, request.url.path, request))
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(200, json={"code": 0, "tenant_access_token": "t1", "expire": 7200})
        if request.url.path.endswith("/im/v1/images"):
            return httpx.Response(200, json={"code": 0, "data": {"image_key": "img_1"}})
        if request.url.path.endswith("/im/v1/messages"):
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_1"}})
        return httpx.Response(404)

    _mock_client(handler, monkeypatch, feishu_module)
    channel = FeishuChannel(
        "飞书",
        {"app_id": "cli", "receive_id": "oc_1", "receive_id_type": "chat_id"},
        {"app_secret": "secret"},
    )
    message_id = await channel.send(_message())
    assert message_id == "om_1"
    assert [call[1] for call in calls] == [
        "/open-apis/auth/v3/tenant_access_token/internal",
        "/open-apis/im/v1/images",
        "/open-apis/im/v1/messages",
    ]
    send_request = calls[-1][2]
    body = json.loads(send_request.content)
    assert send_request.url.params["receive_id_type"] == "chat_id"
    assert body["receive_id"] == "oc_1"
    assert body["msg_type"] == "interactive"
    card = json.loads(body["content"])
    columns = card["elements"][0]["columns"]
    assert columns[0]["elements"][0]["img_key"] == "img_1"
    assert columns[0]["width"] == "120px"
    assert columns[1]["weight"] == 4
    assert "**测试标题**" in columns[1]["elements"][0]["text"]["content"]


def test_feishu_card_shows_music_metadata_without_duplicate_title():
    channel = FeishuChannel("飞书", {}, {})
    card = channel._card(
        _message(
            title="《月下西楼》已整理入库",
            markdown="**《月下西楼》已整理入库**\n\n月下西楼",
            link=None,
            event_type="organize.completed",
            domain="music",
            payload={
                "path": "/music/时生 - 月下西楼.flac",
                "options": {
                    "artist": "时生",
                    "album": "月下西楼",
                    "file_size": 55713936,
                },
            },
        ),
        "img_1",
    )

    assert card["header"]["title"]["content"] == "《月下西楼》已整理入库"
    columns = card["elements"][0]["columns"]
    assert columns[0]["width"] == "120px"
    assert columns[1]["weight"] == 4
    details = columns[1]["elements"][0]["text"]["content"]
    assert details == (
        "**资源类型：** 音乐 / 单曲\n"
        "**歌手：** 时生\n"
        "**专辑：** 月下西楼\n"
        "**音频格式：** FLAC\n"
        "**文件大小：** 53.1 MB"
    )
    assert "下载模式" not in details
    assert len(card["elements"]) == 1


def test_feishu_card_shows_novel_metadata_without_duplicate_progress():
    channel = FeishuChannel("飞书", {}, {})
    card = channel._card(
        _message(
            title="《万古神帝》章节已缓存",
            markdown=(
                "**《万古神帝》章节已缓存**\n\n万古神帝\n\n已缓存 4419/4419 章"
            ),
            link=None,
            event_type="download.completed",
            domain="novel",
            payload={
                "total": 4419,
                "cached": 4419,
                "options": {
                    "author": "飞天鱼",
                    "origin_name": "七猫小说（优+）",
                    "total_chapters": 4419,
                    "cached_chapters": 4419,
                },
            },
        ),
        "img_1",
    )

    columns = card["elements"][0]["columns"]
    assert columns[0]["elements"][0]["img_key"] == "img_1"
    details = columns[1]["elements"][0]["text"]["content"]
    assert details == (
        "**资源类型：** 小说\n"
        "**作者：** 飞天鱼\n"
        "**书源：** 七猫小说（优+）\n"
        "**章节：** 已缓存 4419/4419 章"
    )
    # 进度已经在字段里展示，正文不要重复
    assert len(card["elements"]) == 1


def test_feishu_card_shows_novel_export_fields():
    channel = FeishuChannel("飞书", {}, {})
    card = channel._card(
        _message(
            title="《万古神帝》已导出到书库",
            markdown="**《万古神帝》已导出到书库**\n\n万古神帝\n\n已导出 4419 章",
            link=None,
            event_type="organize.completed",
            domain="novel",
            payload={
                "total": 4419,
                "options": {
                    "author": "飞天鱼",
                    "origin_name": "七猫小说（优+）",
                    "total_chapters": 4419,
                    "file_size": 3355443,
                },
            },
        ),
        None,
    )

    details = card["elements"][0]["text"]["content"]
    assert details == (
        "**资源类型：** 小说\n"
        "**作者：** 飞天鱼\n"
        "**书源：** 七猫小说（优+）\n"
        "**章节：** 已导出 4419 章\n"
        "**文件大小：** 3.2 MB"
    )


def test_feishu_card_shows_comic_metadata_without_detail_link():
    channel = FeishuChannel("飞书", {}, {})
    card = channel._card(
        _message(
            title="新增漫画订阅：开局表白美女师尊",
            markdown=(
                "**新增漫画订阅：开局表白美女师尊**\n\n"
                "开局表白美女师尊\n\n后续会自动下载并整理入库。"
            ),
            link=None,
            event_type="subscription.added",
            domain="comic",
            payload={
                "options": {
                    "name": "开局表白美女师尊",
                    "author": "博易动漫",
                    "tags": "格斗,推理",
                    "category": "comic",
                    "comic_status": "ONGOING",
                    "total_chapters": 65,
                    "update_info": "更新至64话",
                    "cover": "https://img.example/cover.webp",
                }
            },
        ),
        "img_1",
    )

    columns = card["elements"][0]["columns"]
    assert columns[0]["elements"][0]["img_key"] == "img_1"
    assert columns[1]["weight"] == 4
    details = columns[1]["elements"][0]["text"]["content"]
    assert details == (
        "**资源类型：** 漫画\n"
        "**作者：** 博易动漫\n"
        "**状态：** 连载中\n"
        "**话数：** 共 65 话\n"
        "**更新：** 更新至64话\n"
        "**标签：** 格斗、推理"
    )
    assert "查看漫画详情" not in json.dumps(card, ensure_ascii=False)


def test_feishu_card_comic_progress_uses_downloaded_count():
    channel = FeishuChannel("飞书", {}, {})
    card = channel._card(
        _message(
            title="《开局表白美女师尊》已整理入库",
            markdown="**《开局表白美女师尊》已整理入库**\n\n开局表白美女师尊\n\n共 65 章",
            link=None,
            event_type="organize.completed",
            domain="comic",
            payload={
                "total_chapters": 65,
                "downloaded_chapters": 65,
                "options": {
                    "author": "博易动漫",
                    "category": "comic18",
                    "comic_status": "ENDED",
                },
            },
        ),
        None,
    )

    details = card["elements"][0]["text"]["content"]
    assert "**话数：** 已导出 65 话" in details
    assert "**资源类型：** 漫画18" in details
    assert "**状态：** 已完结" in details


def test_feishu_card_comic_without_metadata_keeps_default_summary():
    channel = FeishuChannel("飞书", {}, {})
    card = channel._card(
        _message(
            title="《海贼王》已加入在线书库",
            markdown="**《海贼王》已加入在线书库**\n\n海贼王\n\n可直接用 Tsumiru / Mihon 阅读。",
            link=None,
            event_type="subscription.added",
            domain="comic",
            payload={},
        ),
        None,
    )

    content = card["elements"][0]["text"]["content"]
    assert content.startswith("**《海贼王》已加入在线书库**")


def test_feishu_card_shows_video_metadata_without_duplicate_title():
    channel = FeishuChannel("飞书", {}, {})
    card = channel._card(
        _message(
            title="《举起手来！》已加入订阅",
            markdown=(
                "**《举起手来！》已加入订阅**\n\n"
                "举起手来！（2005）\n\n后续更新会自动处理。"
            ),
            link=None,
            event_type="subscription.added",
            domain="video",
            payload={
                "options": {
                    "name": "举起手来！",
                    "type": "电影",
                    "year": "2005",
                    "media_source": "themoviedb",
                }
            },
        ),
        "img_1",
    )

    columns = card["elements"][0]["columns"]
    assert columns[0]["elements"][0]["img_key"] == "img_1"
    assert columns[0]["width"] == "120px"
    details = columns[1]["elements"][0]["text"]["content"]
    assert details == "**资源类型：** 电影\n**年份：** 2005"
    # 只剩尾部说明，不再重复「片名（年份）」
    assert card["elements"][1]["text"]["content"] == "后续更新会自动处理。"
    assert "举起手来！（2005）" not in json.dumps(card, ensure_ascii=False)


def test_feishu_card_video_shows_enriched_metadata():
    channel = FeishuChannel("飞书", {}, {})
    card = channel._card(
        _message(
            title="《举起手来！》已加入订阅",
            markdown="**《举起手来！》已加入订阅**\n\n举起手来！（2005）",
            link=None,
            event_type="subscription.added",
            domain="video",
            payload={
                "options": {
                    "type": "电影",
                    "year": "2005",
                    "media_source": "themoviedb",
                    "genres": ["历史", "喜剧", "战争"],
                    "rating": 7.1,
                    "vote_count": 27,
                    "runtime": 87,
                    "directors": ["冯小宁"],
                    "actors": ["郭达", "潘长江", "刘小微"],
                }
            },
        ),
        None,
    )

    details = card["elements"][0]["text"]["content"]
    assert details == (
        "**资源类型：** 电影\n"
        "**年份：** 2005\n"
        "**评分：** 7.1 分\n"
        "**时长：** 87 分钟\n"
        "**导演：** 冯小宁\n"
        "**主演：** 郭达、潘长江、刘小微"
    )


def test_feishu_card_movie_hides_season_and_repeated_year():
    """电影没有季；卡片字段区已有年份/类型，正文不再重复这一行。"""
    channel = FeishuChannel("飞书", {}, {})
    card = channel._card(
        _message(
            title="《变形金刚》已加入订阅",
            markdown="2007 年 · 电影\n\n后续更新会自动处理。",
            link=None,
            event_type="subscription.added",
            domain="video",
            payload={
                "options": {
                    "name": "变形金刚",
                    "type": "电影",
                    "year": "2007",
                    "season": 1,
                    "media_source": "mediary-scout",
                }
            },
        ),
        None,
    )

    details = card["elements"][0]["text"]["content"]
    assert details == "**资源类型：** 电影\n**年份：** 2007"
    assert "季" not in details
    assert card["elements"][1]["text"]["content"] == "后续更新会自动处理。"


def test_feishu_card_video_download_shows_progress_field():
    channel = FeishuChannel("飞书", {}, {})
    card = channel._card(
        _message(
            title="《举起手来！》下载完成",
            markdown="**《举起手来！》下载完成**\n\n举起手来！（2005）\n\n下载完成 · S01E01",
            link=None,
            event_type="download.completed",
            domain="video",
            payload={
                "options": {
                    "type": "电影",
                    "year": "2005",
                    "media_source": "themoviedb",
                    "detail": "下载完成 · S01E01",
                }
            },
        ),
        None,
    )

    details = card["elements"][0]["text"]["content"]
    assert "**进度：** S01E01" in details
    assert "下载完成 ·" not in details
    # 进度已经进字段区，正文不再重复一遍
    assert len(card["elements"]) == 1


async def test_dingtalk_markdown_and_sign(monkeypatch):
    import app.services.notifications.channels.dingtalk as dingtalk_module

    captured = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = request.url
        captured["body"] = json.loads(request.content)
        return httpx.Response(200, json={"errcode": 0, "errmsg": "ok"})

    _mock_client(handler, monkeypatch, dingtalk_module)
    channel = DingTalkChannel(
        "钉钉",
        {"webhook": "https://oapi.dingtalk.com/robot/send?access_token=abc"},
        {"secret": "SECtest"},
    )
    await channel.send(_message())
    body = captured["body"]
    assert body["msgtype"] == "markdown"
    assert "![封面]" not in body["markdown"]["text"]
    assert "[查看详情](https://onetap.local/item)" in body["markdown"]["text"]
    assert captured["url"].params["timestamp"]
    assert captured["url"].params["sign"]


async def test_qqbot_markdown_message(monkeypatch):
    import app.services.notifications.channels.qqbot as qqbot_module

    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append((request.url.path, request))
        if request.url.path.endswith("/app/getAppAccessToken"):
            return httpx.Response(
                200, json={"access_token": "qq-token", "expires_in": 7200}
            )
        if request.url.path.endswith("/v2/groups/g1/messages"):
            return httpx.Response(200, json={"id": "qq-msg-1"})
        return httpx.Response(404)

    _mock_client(handler, monkeypatch, qqbot_module)
    channel = QQBotChannel(
        "QQ",
        {"app_id": "app1", "target": "group:g1"},
        {"app_secret": "secret"},
    )
    message_id = await channel.send(_message())
    assert message_id == "qq-msg-1"
    send_request = calls[-1][1]
    assert send_request.headers["Authorization"] == "QQBot qq-token"
    body = json.loads(send_request.content)
    assert body["msg_type"] == 2
    assert "![封面](https://img.example/a.jpg)" in body["markdown"]["content"]


async def test_wechatclawbot_text_and_image(monkeypatch):
    import app.services.notifications.channels.wechatclawbot as claw_module

    sent = []

    async def fake_fetch(url):
        return b"image-bytes", "image/jpeg"

    monkeypatch.setattr(claw_module, "fetch_image", fake_fetch)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/ilink/bot/getuploadurl"):
            return httpx.Response(200, json={"upload_param": "up1"})
        if path.endswith("/c2c/upload"):
            return httpx.Response(
                200,
                content=b"ok",
                headers={"x-encrypted-param": "download-1"},
            )
        if path.endswith("/ilink/bot/sendmessage"):
            sent.append(json.loads(request.content))
            return httpx.Response(200, json={"errcode": 0, "msg_id": "claw-1"})
        return httpx.Response(404)

    _mock_client(handler, monkeypatch, claw_module)
    channel = WeChatClawBotChannel(
        "ClawBot",
        {
            "base_url": "https://ilinkai.weixin.qq.com",
            "account_id": "bot-account",
            "to_user": "wxid_1",
        },
        {"bot_token": "token"},
    )
    message_id = await channel.send(_message())
    assert message_id == "claw-1"
    assert len(sent) == 1
    items = sent[0]["msg"]["item_list"]
    assert [item["type"] for item in items] == [1, 2]
    assert "正文" in items[0]["text_item"]["text"]
    assert items[1]["image_item"]["media"]["encrypt_query_param"] == "download-1"
    assert items[1]["image_item"]["mid_size"] % 16 == 0


async def test_wechatclawbot_falls_back_to_separate_messages(monkeypatch):
    import app.services.notifications.channels.wechatclawbot as claw_module

    sent = []

    async def fake_fetch(url):
        return b"image-bytes", "image/jpeg"

    monkeypatch.setattr(claw_module, "fetch_image", fake_fetch)

    def handler(request: httpx.Request) -> httpx.Response:
        path = request.url.path
        if path.endswith("/ilink/bot/getuploadurl"):
            return httpx.Response(200, json={"upload_param": "up1"})
        if path.endswith("/c2c/upload"):
            return httpx.Response(
                200,
                content=b"ok",
                headers={"x-encrypted-param": "download-1"},
            )
        if path.endswith("/ilink/bot/sendmessage"):
            body = json.loads(request.content)
            sent.append(body)
            if len(body["msg"]["item_list"]) > 1:
                return httpx.Response(200, json={"ret": -2, "errmsg": "不支持图文"})
            return httpx.Response(200, json={"errcode": 0, "msg_id": "claw-1"})
        return httpx.Response(404)

    _mock_client(handler, monkeypatch, claw_module)
    channel = WeChatClawBotChannel(
        "ClawBot",
        {
            "base_url": "https://ilinkai.weixin.qq.com",
            "account_id": "bot-account",
            "to_user": "wxid_1",
        },
        {"bot_token": "token"},
    )
    message_id = await channel.send(_message())
    assert message_id == "claw-1"
    items = [body["msg"]["item_list"] for body in sent]
    assert [len(item) for item in items] == [2, 2, 1, 1]
    assert items[2][0]["type"] == 1
    assert items[3][0]["type"] == 2


async def test_wechatclawbot_refreshes_stale_context_token(monkeypatch):
    import app.services.notifications.channels.wechatclawbot as claw_module

    sent = []

    async def fake_updates(token, base_url, buffer, timeout):
        return {
            "messages": [
                {
                    "from_user_id": "wxid_1",
                    "message_type": 1,
                    "context_token": "fresh-token",
                    "item_list": [{"type": 1, "text_item": {"text": "hi"}}],
                }
            ]
        }

    monkeypatch.setattr(claw_module, "fetch_updates", fake_updates)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ilink/bot/sendmessage"):
            body = json.loads(request.content)
            sent.append(body)
            if body["msg"].get("context_token") == "stale-token":
                return httpx.Response(200, json={"ret": -2, "errmsg": "prepare failed"})
            return httpx.Response(200, json={"errcode": 0, "msg_id": "claw-2"})
        return httpx.Response(404)

    _mock_client(handler, monkeypatch, claw_module)
    channel = WeChatClawBotChannel(
        "ClawBot",
        {
            "base_url": "https://ilinkai.weixin.qq.com",
            "account_id": "bot-account",
            "to_user": "wxid_1",
            "context_token": "stale-token",
        },
        {"bot_token": "token"},
    )
    message_id = await channel.send(_message(image_url=None, link=None))
    assert message_id == "claw-2"
    assert len(sent) == 3
    assert all(body["msg"].get("context_token") == "stale-token" for body in sent[:2])
    assert sent[2]["msg"]["context_token"] == "fresh-token"
    assert channel.config["context_token"] == "fresh-token"


async def test_wechatclawbot_reports_expired_session(monkeypatch):
    import app.services.notifications.channels.wechatclawbot as claw_module

    async def fake_updates(token, base_url, buffer, timeout):
        return {"messages": []}

    monkeypatch.setattr(claw_module, "fetch_updates", fake_updates)

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/ilink/bot/sendmessage"):
            return httpx.Response(200, json={"ret": -2, "errmsg": "prepare failed"})
        return httpx.Response(404)

    _mock_client(handler, monkeypatch, claw_module)
    channel = WeChatClawBotChannel(
        "ClawBot",
        {
            "base_url": "https://ilinkai.weixin.qq.com",
            "account_id": "bot-account",
            "to_user": "wxid_1",
            "context_token": "stale-token",
        },
        {"bot_token": "token"},
    )
    with pytest.raises(ChannelError) as excinfo:
        await channel.send(_message(image_url=None, link=None))
    assert "会话已过期" in str(excinfo.value)


def test_feishu_presentation_card_lists_candidates_with_thumbnails():
    channel = FeishuChannel("飞书", {}, {})
    card = channel._presentation_card(
        _message(title="《花开了》", link=None),
        {
            "header": "《花开了》",
            "subtitle": "音乐 · 2 条候选",
            "items": [
                {
                    "title": "花开了就好",
                    "subtitle": "姚佳顺子",
                    "meta": "03:45 · 320kbps · 8.2MB",
                    "image": "https://img.example/a.jpg",
                },
                {
                    "title": "茶花开了",
                    "subtitle": "加木",
                    "meta": "04:02 · 128kbps · 3.9MB",
                    "image": "",
                },
            ],
            "note": "回复序号选择一首",
        },
        ["img_1", None],
    )

    assert card["header"]["title"]["content"] == "《花开了》"
    elements = card["elements"]
    assert elements[0]["text"]["content"] == "音乐 · 2 条候选"
    first = elements[1]["columns"]
    assert first[0]["width"] == "64px"
    assert first[0]["elements"][0]["img_key"] == "img_1"
    assert first[1]["elements"][0]["text"]["content"] == (
        "**1. 花开了就好** — 姚佳顺子\n03:45 · 320kbps · 8.2MB"
    )
    # 没有封面的候选退化成纯文本行
    assert elements[2]["text"]["content"] == (
        "**2. 茶花开了** — 加木\n04:02 · 128kbps · 3.9MB"
    )
    assert elements[3]["tag"] == "hr"
    assert elements[4]["text"]["content"] == "回复序号选择一首"


def test_feishu_presentation_card_renders_labeled_details():
    channel = FeishuChannel("飞书", {}, {})
    card = channel._presentation_card(
        _message(title="《好东西》", link=None),
        {
            "header": "《好东西》",
            "subtitle": "影视 · 1 条候选",
            "items": [
                {
                    "title": "好东西",
                    "subtitle": "2024",
                    "details": [
                        "**类型：** 电影",
                        "**评分：** 7.6 分",
                        "**来源：** themoviedb",
                    ],
                    "image": "",
                }
            ],
            "note": "回复序号选择一部",
        },
        [None],
    )

    assert card["elements"][1]["text"]["content"] == (
        "**1. 好东西** — 2024\n"
        "**类型：** 电影\n"
        "**评分：** 7.6 分\n"
        "**来源：** themoviedb"
    )


async def test_feishu_send_uses_presentation_card_and_uploads_covers(monkeypatch):
    import app.services.notifications.channels.feishu as feishu_module

    calls = []
    uploaded = []
    sent_requests = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/im/v1/messages"):
            sent_requests.append(request)
        if request.url.path.endswith("/tenant_access_token/internal"):
            return httpx.Response(
                200, json={"code": 0, "tenant_access_token": "t1", "expire": 7200}
            )
        if request.url.path.endswith("/im/v1/images"):
            uploaded.append(request)
            return httpx.Response(
                200, json={"code": 0, "data": {"image_key": f"img_{len(uploaded)}"}}
            )
        if request.url.path.endswith("/im/v1/messages"):
            return httpx.Response(200, json={"code": 0, "data": {"message_id": "om_9"}})
        return httpx.Response(404)

    _mock_client(handler, monkeypatch, feishu_module)

    async def fake_fetch(url):
        return b"\xff\xd8jpeg", "image/jpeg"

    monkeypatch.setattr(feishu_module, "fetch_image", fake_fetch)

    channel = FeishuChannel(
        "飞书",
        {"app_id": "cli", "receive_id": "oc_1", "receive_id_type": "chat_id"},
        {"app_secret": "secret"},
    )
    message_id = await channel.send(
        ChannelMessage(
            title="《花开了》",
            markdown="**《花开了》**",
            text="《花开了》",
            payload={
                "presentation": {
                    "header": "《花开了》",
                    "subtitle": "音乐 · 1 条候选",
                    "items": [
                        {
                            "title": "花开了就好",
                            "subtitle": "姚佳顺子",
                            "meta": "03:45 · 320kbps",
                            "image": "https://img.example/a.jpg",
                        }
                    ],
                    "note": "回复序号选择一首",
                }
            },
            domain="music",
        )
    )

    assert message_id == "om_9"
    assert calls.count("/open-apis/im/v1/images") == 1
    body = json.loads(sent_requests[0].content)
    assert body["msg_type"] == "interactive"
    card = json.loads(body["content"])
    assert card["header"]["title"]["content"] == "《花开了》"
    first = card["elements"][1]["columns"]
    assert first[0]["width"] == "64px"
    assert first[0]["elements"][0]["img_key"] == "img_1"
    assert first[1]["elements"][0]["text"]["content"] == (
        "**1. 花开了就好** — 姚佳顺子\n03:45 · 320kbps"
    )


def _dingtalk_app_message(**overrides):
    values = {
        "title": "《变形金刚》已加入订阅",
        "markdown": "**《变形金刚》已加入订阅**\n\n2007 年 · 电影\n\n后续更新会自动处理。",
        "text": "《变形金刚》已加入订阅\n2007 年 · 电影\n后续更新会自动处理。",
        "image_url": "https://img.example/poster.jpg",
        "link": "https://onetap.local/item",
        "event_type": "subscription.added",
        "domain": "video",
        "payload": {
            "options": {
                "type": "movie",
                "year": "2007",
                "rating": 6.9,
                "runtime": 144,
            }
        },
    }
    values.update(overrides)
    return ChannelMessage(**values)


async def test_dingtalk_app_sends_interactive_card_to_group(monkeypatch):
    import app.services.notifications.channels.dingtalk_app as dingtalk_app_module

    dingtalk_app_module._TOKEN_CACHE.clear()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(
                200, json={"accessToken": "dt_token", "expireIn": 7200}
            )
        if request.url.path == "/v1.0/card/instances/createAndDeliver":
            return httpx.Response(
                200, json={"success": True, "result": {"outTrackId": "track-1"}}
            )
        return httpx.Response(404)

    _mock_client(handler, monkeypatch, dingtalk_app_module)
    channel = DingTalkAppChannel(
        "钉钉应用",
        {
            "app_key": "ding_group",
            "robot_code": "robot_group",
            "receive_id": "cid_group",
            "receive_id_type": "open_conversation_id",
            "card_template_id": "tpl_group",
        },
        {"app_secret": "secret"},
    )

    message_id = await channel.send(_dingtalk_app_message())

    assert message_id == "track-1"
    assert [call.url.path for call in calls] == [
        "/v1.0/oauth2/accessToken",
        "/v1.0/card/instances/createAndDeliver",
    ]
    send_request = calls[-1]
    assert send_request.headers["x-acs-dingtalk-access-token"] == "dt_token"
    body = json.loads(send_request.content)
    assert body["cardTemplateId"] == "tpl_group"
    assert body["openSpaceId"] == "dtv1.card//IM_GROUP.cid_group"
    assert body["imGroupOpenDeliverModel"] == {"robotCode": "robot_group"}
    params = body["cardData"]["cardParamMap"]
    assert params["title"] == "《变形金刚》已加入订阅"
    assert params["subtitle"] == "添加订阅 · 影视"
    assert params["image"] == "https://img.example/poster.jpg"
    assert params["link"] == "https://onetap.local/item"
    assert "**资源类型：** 电影" in params["fields"]
    assert "**时长：** 144 分钟" in params["fields"]
    # 标题已经在卡片头部，正文不重复；年份/类型在字段区，正文也不再重复
    assert params["content"] == "后续更新会自动处理。"


async def test_dingtalk_app_card_single_chat_uses_robot_space(monkeypatch):
    import app.services.notifications.channels.dingtalk_app as dingtalk_app_module

    dingtalk_app_module._TOKEN_CACHE.clear()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(
                200, json={"accessToken": "dt_token", "expireIn": 7200}
            )
        return httpx.Response(
            200, json={"success": True, "result": {"outTrackId": "track-2"}}
        )

    _mock_client(handler, monkeypatch, dingtalk_app_module)
    channel = DingTalkAppChannel(
        "钉钉应用",
        {
            "app_key": "ding_user",
            "robot_code": "robot_user",
            "receive_id": "manager1234",
            "receive_id_type": "user_id",
            "card_template_id": "tpl_user",
        },
        {"app_secret": "secret"},
    )

    await channel.send(_dingtalk_app_message())

    body = json.loads(calls[-1].content)
    assert body["openSpaceId"] == "dtv1.card//IM_ROBOT.manager1234"
    assert body["imRobotOpenDeliverModel"] == {
        "spaceType": "IM_ROBOT",
        "robotCode": "robot_user",
    }
    assert body["imRobotOpenSpaceModel"]["notification"]["alertContent"] == (
        "《变形金刚》已加入订阅"
    )


async def test_dingtalk_app_without_template_sends_action_card(monkeypatch):
    import app.services.notifications.channels.dingtalk_app as dingtalk_app_module

    dingtalk_app_module._TOKEN_CACHE.clear()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(
                200, json={"accessToken": "dt_token", "expireIn": 7200}
            )
        return httpx.Response(200, json={"processQueryKey": "pq-1"})

    _mock_client(handler, monkeypatch, dingtalk_app_module)
    channel = DingTalkAppChannel(
        "钉钉应用",
        {
            "app_key": "ding_markdown",
            "robot_code": "robot_md",
            "receive_id": "manager1234",
            "receive_id_type": "user_id",
            "card_template_id": "",
        },
        {"app_secret": "secret"},
    )

    message_id = await channel.send(_dingtalk_app_message())

    assert message_id == "pq-1"
    assert calls[-1].url.path == "/v1.0/robot/oToMessages/batchSend"
    body = json.loads(calls[-1].content)
    assert body["robotCode"] == "robot_md"
    assert body["userIds"] == ["manager1234"]
    assert body["msgKey"] == "sampleActionCard"
    param = json.loads(body["msgParam"])
    assert param["title"] == "《变形金刚》已加入订阅"
    assert param["singleTitle"] == "查看详情"
    assert param["singleURL"] == "https://onetap.local/item"
    text = param["text"]
    assert text.startswith("### 《变形金刚》已加入订阅")
    # 钉钉 Markdown 图片无法与字段并排，通知只带文字信息
    assert "![封面]" not in text
    # 钉钉 Markdown 单个换行会被折叠，字段之间用空行分段
    assert "**资源类型：** 电影\n\n**年份：** 2007" in text
    assert "后续更新会自动处理。" in text
    # 标题只在开头出现一次，正文不重复
    assert text.count("《变形金刚》已加入订阅") == 1
    assert "2007 年 · 电影" not in text


async def test_dingtalk_app_falls_back_to_markdown_when_action_card_rejected(
    monkeypatch,
):
    import app.services.notifications.channels.dingtalk_app as dingtalk_app_module

    dingtalk_app_module._TOKEN_CACHE.clear()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(
                200, json={"accessToken": "dt_token", "expireIn": 7200}
            )
        body = json.loads(request.content)
        calls.append(body)
        if body["msgKey"] == "sampleActionCard":
            return httpx.Response(
                400, json={"code": "msgKey.unsupported", "message": "msgKey 不支持"}
            )
        return httpx.Response(200, json={"processQueryKey": "pq-md"})

    _mock_client(handler, monkeypatch, dingtalk_app_module)
    channel = DingTalkAppChannel(
        "钉钉应用",
        {
            "app_key": "ding_fallback",
            "robot_code": "robot_fallback",
            "receive_id": "manager1234",
            "receive_id_type": "user_id",
            "card_template_id": "",
        },
        {"app_secret": "secret"},
    )

    message_id = await channel.send(_dingtalk_app_message())

    assert message_id == "pq-md"
    assert [call["msgKey"] for call in calls] == ["sampleActionCard", "sampleMarkdown"]
    param = json.loads(calls[-1]["msgParam"])
    assert param["text"].startswith("### 《变形金刚》已加入订阅")
    assert "[查看详情](https://onetap.local/item)" in param["text"]


async def test_dingtalk_app_presentation_matches_feishu_candidates(monkeypatch):
    import app.services.notifications.channels.dingtalk_app as dingtalk_app_module

    dingtalk_app_module._TOKEN_CACHE.clear()
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(
                200, json={"accessToken": "dt_token", "expireIn": 7200}
            )
        calls.append(json.loads(request.content))
        return httpx.Response(200, json={"processQueryKey": "pq-present"})

    _mock_client(handler, monkeypatch, dingtalk_app_module)
    channel = DingTalkAppChannel(
        "钉钉应用",
        {
            "app_key": "ding_present",
            "robot_code": "robot_present",
            "receive_id": "manager1234",
            "receive_id_type": "user_id",
            "card_template_id": "",
        },
        {"app_secret": "secret"},
    )

    await channel.send(
        ChannelMessage(
            title="《神偷奶爸》",
            markdown="**《神偷奶爸》**\n\n影视 · 2 条候选",
            text="《神偷奶爸》",
            domain="video",
            payload={
                "presentation": {
                    "header": "《神偷奶爸》",
                    "subtitle": "影视 · 2 条候选",
                    "items": [
                        {
                            "title": "神偷奶爸",
                            "subtitle": "2010",
                            "meta": "电影 · 7.3 分",
                        },
                        {
                            "title": "神偷奶爸4",
                            "subtitle": "2024",
                            "meta": "电影 · 6.9 分",
                        },
                    ],
                    "note": "回复序号选择一部",
                }
            },
        )
    )

    param = json.loads(calls[-1]["msgParam"])
    assert calls[-1]["msgKey"] == "sampleMarkdown"
    assert param["title"] == "《神偷奶爸》"
    text = param["text"]
    assert text.startswith("### 《神偷奶爸》")
    assert text.count("《神偷奶爸》") == 1
    assert "影视 · 2 条候选" in text
    assert "**1. 神偷奶爸** — 2010\n\n电影 · 7.3 分" in text
    assert "**2. 神偷奶爸4** — 2024\n\n电影 · 6.9 分" in text
    assert text.endswith("回复序号选择一部")
    assert "#singleTitle#" not in text


async def test_dingtalk_app_raises_when_card_template_missing(monkeypatch):
    import app.services.notifications.channels.dingtalk_app as dingtalk_app_module

    dingtalk_app_module._TOKEN_CACHE.clear()

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/v1.0/oauth2/accessToken":
            return httpx.Response(
                200, json={"accessToken": "dt_token", "expireIn": 7200}
            )
        return httpx.Response(
            400,
            json={"code": "param.templateNotExist", "message": "模板不存在"},
        )

    _mock_client(handler, monkeypatch, dingtalk_app_module)
    channel = DingTalkAppChannel(
        "钉钉应用",
        {
            "app_key": "ding_error",
            "robot_code": "robot_error",
            "receive_id": "cid_group",
            "receive_id_type": "open_conversation_id",
            "card_template_id": "tpl_missing",
        },
        {"app_secret": "secret"},
    )

    with pytest.raises(ChannelError, match="模板不存在"):
        await channel.send(_dingtalk_app_message())


def test_detail_markdown_skips_candidate_lists():
    from app.services.notifications.details import detail_markdown

    card_message = _dingtalk_app_message()
    assert "**资源类型：** 电影" in detail_markdown(card_message)

    candidates = _dingtalk_app_message(
        payload={
            "presentation": {
                "header": "《变形金刚》",
                "items": [{"title": "变形金刚 2007"}],
            }
        }
    )
    assert detail_markdown(candidates) == ""


def test_presentation_markdown_keeps_candidate_details_without_cover():
    from app.services.notifications.details import presentation_markdown

    _, body = presentation_markdown(
        ChannelMessage(
            title="《神偷奶爸》",
            markdown="",
            text="",
            payload={
                "presentation": {
                    "header": "《神偷奶爸》",
                    "subtitle": "影视 · 1 条候选",
                    "items": [
                        {
                            "title": "神偷奶爸",
                            "subtitle": "2010",
                            "meta": "类型：电影",
                            "details": ["评分：7.3 分", "来源：mediary-scout"],
                            "image": "https://img.example/cover.jpg",
                        }
                    ],
                    "note": "回复序号选择一部",
                }
            },
        )
    )

    assert body == (
        "影视 · 1 条候选\n\n"
        "**1. 神偷奶爸** — 2010\n\n"
        "类型：电影\n\n"
        "评分：7.3 分\n\n"
        "来源：mediary-scout\n\n"
        "回复序号选择一部"
    )
