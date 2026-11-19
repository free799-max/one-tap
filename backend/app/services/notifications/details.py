"""通知卡片的字段文案。

把事件 payload 里的资源元数据渲染成「**字段：** 值」形式的 Markdown，
飞书交互卡片和钉钉互动卡片共用同一份渲染结果，避免两个渠道展示不一致。
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.services.notifications.channels.base import ChannelMessage

VIDEO_TYPES = {
    "movie": "电影",
    "电影": "电影",
    "tv": "电视剧",
    "series": "电视剧",
    "电视剧": "电视剧",
    "剧集": "电视剧",
}

COMIC_CATEGORY = {"comic": "漫画", "comic18": "漫画18"}
COMIC_STATUS = {
    "ONGOING": "连载中",
    "RELEASING": "连载中",
    "COMPLETED": "已完结",
    "ENDED": "已完结",
    "FINISHED": "已完结",
    "ON_HIATUS": "休载中",
    "HIATUS": "休载中",
    "CANCELLED": "已下架",
    "连载": "连载中",
    "完结": "已完结",
}


def file_size(value: Any) -> str:
    text = str(value or "").strip()
    units = ("B", "KB", "MB", "GB", "TB")
    for unit in units:
        if text.upper().endswith(unit):
            number = text[: -len(unit)].strip()
            try:
                float(number)
            except ValueError:
                continue
            return f"{number} {unit}"
    try:
        size = int(value)
    except (TypeError, ValueError):
        return ""
    if size <= 0:
        return ""
    # music-dl 的 size 文案按 1024 换算，这里保持一致，避免同一文件下载前/入库后显示不同大小
    amount = float(size)
    unit = units[0]
    for unit in units:
        if amount < 1024 or unit == units[-1]:
            break
        amount /= 1024
    return f"{amount:.1f} {unit}" if unit != "B" else f"{size} B"


def _int_value(value: Any) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return 0


def _rows_text(rows: list[tuple[str, str]]) -> str:
    return "\n".join(f"**{label}：** {text}" for label, text in rows if text)


def _value_getter(message: ChannelMessage):
    payload = message.payload or {}
    options = payload.get("options") or {}

    def value(*keys: str) -> str:
        for source in (payload, options):
            for key in keys:
                text = str(source.get(key) or "").strip()
                if text:
                    return text
        return ""

    return value


def music_details(message: ChannelMessage) -> str:
    value = _value_getter(message)
    audio_format = value("audio_format", "format").upper()
    path = value("path")
    if not audio_format and "." in Path(path).name:
        audio_format = Path(path).suffix[1:].upper()
    rows = [
        ("资源类型", "音乐 / 单曲"),
        ("歌手", value("artist")),
        ("专辑", value("album")),
        ("音频格式", audio_format),
        ("文件大小", file_size(value("file_size", "size"))),
    ]
    return _rows_text(rows)


def novel_details(message: ChannelMessage) -> tuple[str, str]:
    """小说字段；返回字段文本与已展示的进度文案。"""
    value = _value_getter(message)
    payload = message.payload or {}
    options = payload.get("options") or {}
    total = _int_value(
        options.get("total_chapters")
        or payload.get("total")
        or options.get("total")
    )
    cached = _int_value(
        options.get("cached_chapters")
        or payload.get("cached")
        or options.get("cached")
    )
    chapter_text = ""
    if total > 0:
        if message.event_type == "download.completed":
            chapter_text = f"已缓存 {cached or total}/{total} 章"
        elif message.event_type == "organize.completed":
            chapter_text = f"已导出 {total} 章"
        else:
            chapter_text = f"共 {total} 章"
    rows = [
        ("资源类型", "小说"),
        ("作者", value("author")),
        ("书源", value("origin_name", "origin", "source")),
        ("章节", chapter_text),
        ("文件大小", file_size(value("file_size", "size"))),
    ]
    return _rows_text(rows), chapter_text


def _format_tags(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        items = [str(item).strip() for item in value]
    else:
        text = str(value or "").replace("，", ",").replace("、", ",")
        items = [item.strip() for item in text.split(",")]
    return "、".join(item for item in items if item)


def comic_details(message: ChannelMessage) -> str:
    """漫画字段：资源类型/作者/状态/话数/更新/标签。"""
    value = _value_getter(message)
    payload = message.payload or {}
    options = payload.get("options") or {}
    category = value("category")
    raw_status = value("comic_status", "status")
    total = _int_value(value("total_chapters", "chapter_count"))
    downloaded = _int_value(value("downloaded_chapters"))
    has_downloaded = "downloaded_chapters" in options or "downloaded_chapters" in payload
    progress = ""
    if total > 0:
        if message.event_type == "download.completed":
            progress = f"已缓存 {downloaded or total}/{total} 话"
        elif message.event_type == "organize.completed":
            progress = f"已导出 {total} 话"
        else:
            progress = (
                f"{downloaded}/{total} 话" if has_downloaded else f"共 {total} 话"
            )
    latest = value("update_info", "latest_chapter")
    rows = [
        ("资源类型", COMIC_CATEGORY.get(category, category) or "漫画"),
        ("作者", value("author")),
        ("状态", COMIC_STATUS.get(raw_status, raw_status)),
        ("话数", progress),
        ("更新", latest),
        ("标签", _format_tags(value("tags"))),
    ]
    filled = [(label, text) for label, text in rows if text]
    if len(filled) == 1 and filled[0][0] == "资源类型":
        # 只有「资源类型」时保留默认摘要行（事件 · 类型），否则卡片会丢掉事件信息
        return ""
    return _rows_text(filled)


def video_details(message: ChannelMessage) -> tuple[str, str]:
    """影视字段；返回字段文本与已展示的进度文案。"""
    value = _value_getter(message)
    payload = message.payload or {}
    options = payload.get("options") or {}

    def names(key: str) -> str:
        raw = options.get(key) or payload.get(key)
        if isinstance(raw, (list, tuple)):
            items = [
                str(item.get("name") if isinstance(item, dict) else item or "").strip()
                for item in raw
            ]
        else:
            items = [item.strip() for item in str(raw or "").replace("，", ",").split(",")]
        return "、".join(item for item in items if item)

    raw_type = value("type", "type_name", "media_type")
    category = VIDEO_TYPES.get(raw_type.lower(), raw_type)
    # 电影没有「季」的概念，巡影对电影也会回 seasonNumber=1
    is_movie = category == "电影"
    season = 0 if is_movie else _int_value(value("season"))
    total = _int_value(value("total_episode"))
    quality = " · ".join(
        text for text in (value("resolution"), value("quality")) if text
    )
    rating = options.get("rating") or payload.get("rating")
    rating_text = ""
    try:
        if rating:
            rating_text = f"{float(rating):g} 分"
    except (TypeError, ValueError):
        rating_text = ""
    runtime = _int_value(options.get("runtime") or payload.get("runtime"))
    episode_text = f"共 {total} 集" if total > 0 else ""
    detail = value("progress", "detail")
    progress = detail
    for prefix in ("下载完成 · ", "整理入库 · "):
        if progress.startswith(prefix):
            progress = progress[len(prefix) :]
            break
    rows = [
        ("资源类型", category or "影视"),
        ("年份", value("year")),
        ("评分", rating_text),
        ("时长", f"{runtime} 分钟" if runtime else ""),
        ("导演", names("directors")),
        ("主演", names("actors")),
        ("季", f"第 {season} 季" if season > 0 else ""),
        ("画质", quality),
        ("集数", episode_text),
        # 与「集数」重复的进度不再单列一行
        ("进度", "" if progress == episode_text else progress),
    ]
    return _rows_text(rows), detail


def detail_markdown(message: ChannelMessage) -> str:
    """按资源类型渲染字段区；候选列表类消息没有单条元数据，返回空串。"""
    if (message.payload or {}).get("presentation"):
        return ""
    domain = (message.domain or "").strip()
    if domain == "music":
        return music_details(message)
    if domain == "novel":
        return novel_details(message)[0]
    if domain == "comic":
        return comic_details(message)
    if domain == "video":
        return video_details(message)[0]
    return ""


DOMAIN_LABELS = {
    "video": "影视",
    "comic": "漫画",
    "novel": "小说",
    "music": "音乐",
}


def summary_line(message: ChannelMessage) -> str:
    """卡片摘要行：事件 · 资源类型 · 作者/歌手。"""
    from app.services.notifications.events import EVENT_LABELS

    parts: list[str] = []
    if message.event_type:
        parts.append(
            "通知汇总"
            if message.event_type == "batch"
            else EVENT_LABELS.get(message.event_type, message.event_type)
        )
    if message.domain:
        parts.append(DOMAIN_LABELS.get(message.domain, message.domain))
    options = (message.payload or {}).get("options") or {}
    creator = str(options.get("artist") or options.get("author") or "").strip()
    if creator:
        parts.append(creator)
    return " · ".join(part for part in parts if part)


def content_text(message: ChannelMessage) -> str:
    """正文：去掉标题、字段区已展示的进度/元信息，避免与字段重复。"""
    import re

    title = message.title.strip()
    content = (message.markdown or message.text or "").strip()
    for repeated_title in (f"**{title}**", title):
        if title and content.startswith(repeated_title):
            content = content[len(repeated_title) :].strip()
            break
    shown_detail = ""
    domain = (message.domain or "").strip()
    if domain == "novel":
        shown_detail = novel_details(message)[1]
    elif domain == "video":
        shown_detail = video_details(message)[1]
    paragraphs: list[str] = []
    for paragraph in content.split("\n\n"):
        paragraph = paragraph.strip()
        if not paragraph or paragraph == shown_detail:
            continue
        # 去掉「片名（年份）」这类重复标题行，字段区已经展示年份
        plain = re.sub(r"（\d{4}）$", "", paragraph).strip()
        if "\n" not in paragraph and (paragraph in title or plain in title):
            continue
        # 字段区已经有年份/类型，正文里「2007 年 · 电影」这类元信息不再重复
        if domain == "video" and re.fullmatch(
            r"\d{4} 年(?:\s*·\s*[^·]+)?", paragraph
        ):
            continue
        paragraphs.append(paragraph)
    return "\n\n".join(paragraphs)


def presentation_markdown(message: ChannelMessage) -> tuple[str, str]:
    """候选列表：标题 + 摘要 + 编号候选 + 备注，与飞书候选卡片一致。

    钉钉 Markdown 会把单个换行折叠成空格，候选项内部用空行分段，
    保证标题、类型、评分逐行显示；不附封面图，只保留文字信息。
    """
    presentation = (message.payload or {}).get("presentation") or {}
    header = str(presentation.get("header") or message.title).strip() or message.title
    blocks: list[str] = []
    for index, item in enumerate(presentation.get("items") or [], 1):
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip() or "未命名资源"
        headline = f"**{index}. {title}**"
        author = str(item.get("subtitle") or "").strip()
        if author:
            headline += f" — {author}"
        lines = [headline]
        meta = str(item.get("meta") or "").strip()
        if meta:
            lines.append(meta)
        for detail in item.get("details") or []:
            detail = str(detail).strip()
            if detail:
                lines.append(detail)
        blocks.append("\n\n".join(lines))
    subtitle = str(presentation.get("subtitle") or "").strip()
    note = str(presentation.get("note") or "").strip()
    body = "\n\n".join(
        part for part in (subtitle, "\n\n".join(blocks), note) if part
    )
    return header, body


def render_markdown(message: ChannelMessage) -> tuple[str, str]:
    """把一条通知渲染成「标题 + Markdown 正文」，字段与飞书卡片保持一致。"""
    if (message.payload or {}).get("presentation"):
        return presentation_markdown(message)
    title = message.title.strip() or "OneTap 通知"
    domain = (message.domain or "").strip()
    fields = detail_markdown(message)
    if fields:
        # 钉钉 Markdown 单个换行会被折叠，字段之间用空行保证逐行展示
        fields = fields.replace("\n", "\n\n")
    else:
        # 漫画字段为空时退回到「事件 · 类型 · 作者」摘要，和飞书一致
        fields = summary_line(message) if domain else ""
    body = content_text(message)
    return title, "\n\n".join(part for part in (fields, body) if part)
