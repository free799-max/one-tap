"""候选列表的展示结构。

搜索结果各域的字段名并不统一，这里统一归一成
「标题 + 副标题 + 一行元信息 + 封面」，供 MCP 推送和飞书卡片共用。
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

DOMAIN_LABELS = {
    "video": "影视",
    "comic": "漫画",
    "music": "音乐",
    "novel": "小说",
}

_COMIC_CATEGORY = {"comic": "漫画", "comic18": "漫画18"}
_COMIC_STATUS = {
    "ONGOING": "连载中",
    "COMPLETED": "已完结",
    "ENDED": "已完结",
}
_VIDEO_TYPES = {
    "movie": "电影",
    "电影": "电影",
    "tv": "电视剧",
    "series": "电视剧",
    "电视剧": "电视剧",
    "剧集": "电视剧",
}
_SIZE_UNITS = ("B", "KB", "MB", "GB", "TB")


@dataclass(frozen=True)
class PresentItem:
    title: str
    subtitle: str = ""
    meta: str = ""
    details: tuple[str, ...] = ()
    image: str | None = None
    link: str | None = None

    def first_line(self, index: int) -> str:
        line = f"**{index}. {self.title}**"
        if self.subtitle:
            line += f" — {self.subtitle}"
        return line

    def plain_line(self, index: int) -> str:
        line = f"{index}. {self.title}"
        if self.subtitle:
            line += f" — {self.subtitle}"
        if self.meta:
            line += f"\n  {self.meta}"
        for detail in self.details:
            line += f"\n  {detail}"
        return line

    def as_payload(self) -> dict[str, Any]:
        return {
            "title": self.title,
            "subtitle": self.subtitle,
            "meta": self.meta,
            "details": list(self.details),
            "image": self.image,
            "link": self.link,
        }


def _text(value: Any) -> str:
    return str(value).strip() if value is not None else ""


def _number(value: Any) -> float:
    if isinstance(value, bool):
        return 0
    if isinstance(value, (int, float)):
        return float(value)
    match = re.search(r"\d+(?:\.\d+)?", _text(value))
    return float(match.group(0)) if match else 0


def format_duration(value: Any) -> str:
    """秒数或已格式化时长统一成 mm:ss；超过一小时用 h:mm:ss。"""
    if isinstance(value, str) and ":" in value:
        return value.strip()
    seconds = int(_number(value))
    if seconds <= 0:
        return ""
    hours, rest = divmod(seconds, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes:02d}:{secs:02d}"


def format_size(value: Any) -> str:
    """大小紧凑书写：``75.6MB``，不带空格。"""
    text = _text(value)
    if not text:
        return ""
    upper = text.upper()
    for unit in reversed(_SIZE_UNITS):
        if upper.endswith(unit):
            number = upper[: -len(unit)].strip()
            amount = _number(number)
            return f"{amount:g}{unit}" if amount else ""
    size = _number(text)
    if size <= 0:
        return ""
    # 与 music-dl 的 size 文案一致，按 1024 换算
    unit = _SIZE_UNITS[0]
    for unit in _SIZE_UNITS:
        if size < 1024 or unit == _SIZE_UNITS[-1]:
            break
        size /= 1024
    return f"{size:.1f}{unit}" if unit != "B" else f"{int(size)}B"


def format_bitrate(value: Any) -> str:
    """码率统一成 ``320kbps``；上游可能给 bps 或已按 kbps 给值。"""
    text = _text(value)
    if not text:
        return ""
    amount = _number(text)
    if amount <= 0:
        return ""
    if "bps" in text.lower():
        return f"{amount:g}kbps"
    if amount >= 10000:
        amount /= 1000
    return f"{amount:g}kbps"


def _tags(value: Any) -> str:
    if isinstance(value, (list, tuple)):
        items = [_text(item) for item in value]
    else:
        text = _text(value).replace("，", ",").replace("、", ",")
        items = [_text(item) for item in text.split(",")]
    return "、".join(item for item in items if item)


def _joined(parts: list[str]) -> str:
    return " · ".join(part for part in parts if part)


def _vote_meta(raw: dict[str, Any]) -> str:
    vote = _number(raw.get("vote") or raw.get("vote_average"))
    return f"{vote:g} 分" if vote else ""


def _music_item(raw: dict[str, Any]) -> PresentItem:
    title = _text(raw.get("title") or raw.get("name"))
    album = _text(raw.get("album"))
    meta = [
        format_duration(raw.get("duration")),
        format_bitrate(raw.get("bitrate")),
        format_size(raw.get("size")),
    ]
    if album and album != title:
        meta.append(f"《{album}》")
    return PresentItem(
        title=title or "未命名歌曲",
        subtitle=_text(raw.get("artist")),
        meta=_joined(meta),
        image=_text(raw.get("cover")) or None,
    )


def _video_item(raw: dict[str, Any]) -> PresentItem:
    title = _text(raw.get("title") or raw.get("name"))
    year = _text(raw.get("year"))
    raw_type = _text(raw.get("type") or raw.get("type_name") or raw.get("media_type"))
    details = tuple(
        f"**{label}：** {value}"
        for label, value in (
            ("类型", _VIDEO_TYPES.get(raw_type.lower(), raw_type)),
            ("评分", _vote_meta(raw)),
            ("来源", _text(raw.get("media_source"))),
        )
        if value
    )
    return PresentItem(
        title=title or "未命名影视",
        # 年份已在第一行，第二行不重复
        subtitle=year,
        details=details,
        image=_text(raw.get("poster") or raw.get("cover")) or None,
    )


def _comic_item(raw: dict[str, Any]) -> PresentItem:
    title = _text(raw.get("title") or raw.get("name"))
    category = _text(raw.get("category"))
    raw_status = _text(raw.get("status"))
    in_library = bool(raw.get("in_library"))
    meta = [
        _COMIC_CATEGORY.get(category, category) or "漫画",
        _COMIC_STATUS.get(raw_status, raw_status),
        "已入库" if in_library else "未入库",
    ]
    return PresentItem(
        title=title or "未命名漫画",
        subtitle=_text(raw.get("author")),
        meta=_joined(meta),
        image=_text(raw.get("cover")) or None,
    )


def _novel_item(raw: dict[str, Any]) -> PresentItem:
    title = _text(raw.get("title") or raw.get("name"))
    total = int(_number(raw.get("total_chapters") or raw.get("total")))
    meta = [
        _text(raw.get("origin_name") or raw.get("origin")),
        f"{total} 章" if total > 0 else "",
    ]
    return PresentItem(
        title=title or "未命名书籍",
        subtitle=_text(raw.get("author")),
        meta=_joined(meta),
        image=_text(raw.get("cover")) or None,
    )


def _generic_item(raw: dict[str, Any]) -> PresentItem:
    meta = [
        _text(raw.get("year")),
        _tags(raw.get("genre") or raw.get("tags")),
    ]
    return PresentItem(
        title=_text(raw.get("title") or raw.get("name")) or "未命名资源",
        subtitle=_text(raw.get("artist") or raw.get("author")),
        meta=_joined(meta),
        image=_text(raw.get("cover") or raw.get("poster")) or None,
    )


_BUILDERS = {
    "music": _music_item,
    "video": _video_item,
    "comic": _comic_item,
    "novel": _novel_item,
}


def build_items(domain: str, items: list[dict[str, Any]]) -> list[PresentItem]:
    builder = _BUILDERS.get(domain, _generic_item)
    return [builder(raw) for raw in items if isinstance(raw, dict)]
