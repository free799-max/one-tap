"""通知文案构造。

标题放在通知的 ``title`` 字段里，正文只承载补充信息（年份、作者、
清晰度、进度等），避免卡片上标题与正文重复。
"""

from __future__ import annotations

from typing import Any

from app.services.notifications.presentation import format_size

DOMAIN_LABELS = {
    "video": "影视",
    "comic": "漫画",
    "novel": "小说",
    "music": "音乐",
}


def _text(value: Any) -> str:
    return str(value or "").strip()


def _line(*parts: Any) -> str:
    return " · ".join(text for text in (_text(part) for part in parts) if text)


def _year(year: str | None) -> str:
    return f"{year} 年" if _text(year) else ""


def _quality(options: dict[str, Any] | None) -> str:
    options = options or {}
    return _line(options.get("resolution"), options.get("quality"))


def _audio(options: dict[str, Any] | None) -> str:
    options = options or {}
    return _line(
        options.get("artist"),
        options.get("audio_format") or options.get("format"),
        format_size(options.get("file_size") or options.get("size")),
    )


def _compose(headline: str, *blocks: str) -> tuple[str, str, str]:
    """返回 (标题, Markdown 正文, 纯文本)；正文不含标题。"""
    body = [block.strip() for block in blocks if block and block.strip()]
    markdown = "\n\n".join(body)
    text = "\n".join([headline, *body]) if body else headline
    return headline, markdown, text


def added(
    domain: str,
    title: str,
    *,
    year: str | None = None,
    options: dict[str, Any] | None = None,
    variant: str | None = None,
) -> tuple[str, str, str]:
    name = title or "未命名资源"
    options = options or {}
    quality = _quality(options)
    if domain == "video":
        headline = f"《{name}》已加入订阅" + (f" · {quality}" if quality else "")
        return _compose(
            headline,
            _line(_year(year), options.get("type")),
            "后续更新会自动处理。",
        )
    if domain == "comic":
        if variant == "local":
            headline = f"新增漫画订阅：{name}"
            tail = "后续会自动下载并整理入库。"
        else:
            headline = f"《{name}》已加入在线书库"
            tail = "可直接用 Tsumiru / Mihon 阅读。"
        return _compose(
            headline,
            _line(_year(year), options.get("author")),
            tail,
        )
    if domain == "novel":
        headline = f"《{name}》已加入书架"
        return _compose(
            headline,
            _line(_year(year), options.get("author")),
            "后续更新会自动处理。",
        )
    headline = f"《{name}》已加入音乐下载队列"
    return _compose(headline, _audio(options))


def replaced(
    domain: str,
    title: str,
    *,
    year: str | None = None,
    options: dict[str, Any] | None = None,
    variant: str | None = None,
) -> tuple[str, str, str]:
    name = title or "未命名资源"
    quality = _quality(options)
    headline = f"《{name}》已切换版本"
    if variant == "cloud":
        return _compose(headline, "已替换为更合适的版本。")
    detail = f"新版本：{quality}" if quality else "已重新搜索目标版本"
    tail = {
        "video": "旧文件会保留到新版本入库。",
        "comic": "缺失章节会重新补齐，已有文件不会删除。",
        "novel": "会重新缓存缺失章节，已有缓存不会删除。",
        "music": "旧任务已取消，新任务已加入下载队列。",
    }.get(domain, "")
    return _compose(headline, _year(year), f"{detail}。{tail}")


def removed(domain: str, title: str, *, year: str | None = None) -> tuple[str, str, str]:
    name = title or "未命名资源"
    headline = f"已取消《{name}》"
    return _compose(headline, _year(year))


def updated(
    domain: str,
    title: str,
    *,
    count: int | None = None,
    detail: str | None = None,
    year: str | None = None,
) -> tuple[str, str, str]:
    name = title or "未命名资源"
    unit = {"video": "集", "comic": "章", "novel": "章"}.get(domain, "个文件")
    detail = f"新增 {count} {unit}" if count else (detail or "检测到新内容")
    headline = f"《{name}》有更新"
    return _compose(headline, _year(year), detail)


def completed(
    domain: str,
    title: str,
    *,
    detail: str | None = None,
    year: str | None = None,
) -> tuple[str, str, str]:
    name = title or "未命名资源"
    headline = f"《{name}》已全部就绪"
    return _compose(headline, _year(year), detail or "全部内容已获取。")


def download_completed(
    domain: str,
    title: str,
    *,
    detail: str | None = None,
    year: str | None = None,
    variant: str | None = None,
) -> tuple[str, str, str]:
    name = title or "未命名资源"
    if domain == "novel":
        action = "章节已缓存"
    elif domain == "comic" and variant != "local":
        # 在线漫画下载章节只是离线缓存，没有入库环节
        action = "章节已缓存"
    else:
        action = "下载完成"
    headline = f"《{name}》{action}"
    return _compose(headline, _year(year), detail or "")


def organize_completed(
    domain: str,
    title: str,
    *,
    detail: str | None = None,
    year: str | None = None,
    variant: str | None = None,
) -> tuple[str, str, str]:
    name = title or "未命名资源"
    action = {
        "video": "已整理入库",
        "music": "已整理入库",
        "novel": "已导出到书库",
        "comic": "已整理入库",
    }.get(domain, "已处理完成")
    if domain == "comic" and variant != "local":
        action = "已缓存完成"
    headline = f"《{name}》{action}"
    return _compose(headline, _year(year), detail or "")


def failed(
    domain: str, title: str, *, detail: str | None = None
) -> tuple[str, str, str]:
    name = title or "未命名资源"
    headline = f"《{name}》处理失败"
    return _compose(headline, detail or "请到 OneTap 查看详情")
