"""应用与编排方案目录同步服务。

定时从配套管理端（onetap-manage）拉取应用市场与自动化方案数据，
upsert 到本地 ``apps`` / ``app_orchestrations`` 表作为只读缓存：管理端
下架（远端不再返回）的应用会删除本地非内置、未部署的缓存；方案不会
因远端缺失而删除，而是通过 ``is_listed`` 从目录隐藏，重新下发时恢复；
仅额外清理与管理端方案重复且无实例引用的内置旧方案，已有记录的
``is_builtin`` 保持不变。
编排方案检测到版本变化时写一条 ``orchestration_update_events`` 记录，
供前端展示模板更新历史与实例组升级提示。
"""

import logging
from datetime import datetime, timezone

import httpx
from sqlalchemy import delete, func, select, update

from app.core.exceptions import APIException
from app.database import async_session
from app.models.app_store import App
from app.models.orchestration import (
    AppInstance,
    AppOrchestration,
    AppOrchestrationInstance,
    OrchestrationUpdateEvent,
)
from app.services.app_store.app_validator import validate_app_definition
from app.services.manage_client import manage_base_urls, with_manage_fallback

logger = logging.getLogger(__name__)

# 模块级同步状态，供 /sync-status 端点查询
last_sync_at: datetime | None = None
"""最近一次同步完成（成功或失败）的时间。"""

last_error: str | None = None
"""最近一次同步的错误信息，成功时为 None。"""

last_result: dict | None = None
"""最近一次成功同步的统计结果（created/updated/removed/total）。"""

categories: list[dict] = []
"""管理端下发的应用分类字典（value/label/order），本地不再维护映射。"""


def get_categories() -> list[dict]:
    """返回管理端同步下来的分类字典，未同步时为空列表。"""
    return categories


def get_category_rank() -> dict[str, int]:
    """分类值到展示顺序的映射，未知分类排在最后由调用方兜底。"""
    return {
        str(item.get("value")): int(item.get("order", index))
        for index, item in enumerate(categories)
        if isinstance(item, dict) and item.get("value")
    }


def _extract_items(payload) -> list[dict]:
    """从管理端响应中解析方案列表。

    管理端统一封装为 ``{"success": true, "data": [...]}``；
    防御性处理：若响应 JSON 含 list 类型的 ``data`` 键则取之，
    否则把整个 JSON 当作列表处理。
    """
    if isinstance(payload, dict) and isinstance(payload.get("data"), list):
        return payload["data"]
    if isinstance(payload, list):
        return payload
    raise ValueError("管理端响应格式无法识别：既不是列表，也不包含 data 列表")


def _resolve_icon(icon: str | None, base_url: str) -> str | None:
    """把管理端的相对图标路径转成可直接访问的绝对 URL。"""
    if not icon:
        return None
    if icon.startswith("/"):
        return f"{base_url}{icon}"
    return icon


def _parse_naive_utc(value: str | None) -> datetime | None:
    """把管理端 ISO 时间转成 SQLite 友好的无时区 UTC 时间。"""
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(timezone.utc).replace(tzinfo=None)
    return parsed


def _managed_default_image(item: dict) -> str | None:
    """自动发布时把未固定标签的默认镜像对齐到管理端最新版本。"""
    image = item.get("image")
    version = str(item.get("latest_version") or "").strip()
    if not image or not version or not item.get("auto_publish", False):
        return image
    if "@" in image:
        return image
    last_segment = image.rsplit("/", 1)[-1]
    if ":" in last_segment:
        repository, current_tag = image.rsplit(":", 1)
        if current_tag != "latest":
            return image
        image = repository
    return f"{image}:{version}"


async def _delete_removed_apps(session, remote_names: set[str]) -> int:
    """删除管理端已下架/移除的应用缓存。

    只处理由管理端同步来的非内置应用；被 ``AppInstance`` 引用的应用保留，
    避免影响已部署实例。``remote_names`` 为空时调用方不会触发清理。
    """
    candidates = list(
        (
            await session.execute(
                select(App).where(
                    App.is_builtin.is_(False),
                    App.name.not_in(remote_names),
                )
            )
        ).scalars()
    )
    if not candidates:
        return 0

    used = set(
        (
            await session.execute(
                select(AppInstance.app_id).where(
                    AppInstance.app_id.in_([app.id for app in candidates])
                )
            )
        ).scalars()
    )
    removed = 0
    for app in candidates:
        if app.id in used:
            logger.info("应用 '%s' 已从管理端下架，但本地存在部署实例，保留", app.name)
            continue
        logger.info("应用 '%s' 已从管理端下架，删除本地缓存", app.name)
        await session.delete(app)
        removed += 1
    return removed


async def sync_categories() -> dict:
    """从管理端拉取应用分类字典并缓存到进程内。

    分类显示名与排序由管理端统一维护，本地不再内置映射；
    MANAGE_BASE_URL 未配置时直接跳过，前端回退为显示原始分类值。
    """
    global categories

    if not manage_base_urls():
        return {"skipped": True}

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp, _ = await with_manage_fallback(
            lambda base_url: client.get(f"{base_url}/api/public/categories")
        )
        resp.raise_for_status()
        items = _extract_items(resp.json())

    categories = [
        {
            "value": str(item.get("value")),
            "label": str(item.get("label") or item.get("value")),
            "order": int(item.get("order", index)),
        }
        for index, item in enumerate(items)
        if isinstance(item, dict) and item.get("value")
    ]
    return {"total": len(categories)}


async def sync_apps() -> dict:
    """从管理端拉取应用市场应用并 upsert 到本地缓存。

    只同步已发布且带有 Compose 模板的应用：模板缺失或校验失败时跳过
    该条记录，避免用不完整的数据覆盖本地可部署应用。管理端已下架
    （或删除）的应用会从本地缓存移除：仅限非内置且没有部署实例的记录；
    远端返回空列表时不做清理，避免管理端异常导致缓存被清空。
    ``is_builtin`` 等本地字段保持不变。

    MANAGE_BASE_URL 未配置时直接跳过。
    """
    if not manage_base_urls():
        return {"skipped": True}

    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp, base_url = await with_manage_fallback(
            lambda url: client.get(f"{url}/api/public/apps")
        )
        resp.raise_for_status()
        items = _extract_items(resp.json())

    remote_names = {
        str(item.get("slug") or "").strip() for item in items if isinstance(item, dict)
    }
    remote_names.discard("")

    created = 0
    updated = 0
    skipped = 0
    async with async_session() as session:
        for item in items:
            name = str(item.get("slug") or "").strip()
            app_type = str(item.get("type") or "compose")
            yaml_template = item.get("compose_template") or ""
            if not name or (app_type != "external" and not yaml_template):
                logger.warning(
                    "管理端应用缺少 slug 或 compose_template，已跳过：%s",
                    item.get("slug") or item.get("name"),
                )
                skipped += 1
                continue
            if len(name) > 50:
                logger.warning("管理端应用标识 '%s' 超过 50 字符，已跳过", name)
                skipped += 1
                continue

            config_schema = item.get("config_schema") or {}
            if app_type != "external":
                try:
                    validate_app_definition(
                        yaml_template, config_schema, item.get("image") or ""
                    )
                except APIException as exc:
                    logger.warning("管理端应用 '%s' 校验失败，已跳过：%s", name, exc.message)
                    skipped += 1
                    continue

            result = await session.execute(select(App).where(App.name == name))
            app = result.scalar_one_or_none()

            values = {
                "display_name": item.get("name") or name,
                "description": item.get("description"),
                "category": item.get("category") or "other",
                "tags": item.get("tags") or [],
                "icon": _resolve_icon(item.get("icon"), base_url),
                "website": item.get("homepage"),
                "source_url": item.get("repo_url"),
                "architectures": item.get("architectures") or [],
                "image": _managed_default_image(item),
                "image_size": item.get("image_size"),
                "image_pushed_at": _parse_naive_utc(item.get("image_pushed_at")),
                "image_digest": item.get("image_digest"),
                "default_ports": item.get("default_ports") or [],
                "config_schema": config_schema,
                "yaml_template": yaml_template,
                "readme": item.get("readme"),
                "type": item.get("type") or "compose",
                "changelog": item.get("changelog"),
                "version": str(
                    item.get("latest_version")
                    or (app.version if app is not None else "1.0.0")
                )[:20],
            }
            # 旧版管理端不返回 backup_paths，此时保留本地已有配置
            if "backup_paths" in item:
                values["backup_paths"] = item.get("backup_paths") or []
            # 旧版管理端不返回 is_paid，此时保留本地已有标识
            if "is_paid" in item:
                values["is_paid"] = bool(item.get("is_paid", False))

            if app is None:
                session.add(App(name=name, is_builtin=False, **values))
                created += 1
                continue

            if all(getattr(app, key) == value for key, value in values.items()):
                continue

            for key, value in values.items():
                setattr(app, key, value)
            updated += 1

        removed = 0
        if remote_names:
            removed = await _delete_removed_apps(session, remote_names)
        await session.commit()

    stats = {
        "created": created,
        "updated": updated,
        "removed": removed,
        "skipped": skipped,
        "total": created + updated,
    }
    logger.info("应用市场同步完成：%s", stats)
    return stats


async def fetch_app_versions(name: str) -> list[dict]:
    """按需读取单个应用在管理端最新发布的 20 个版本。"""
    if not manage_base_urls():
        return []
    async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
        resp, _ = await with_manage_fallback(
            lambda base_url: client.get(
                f"{base_url}/api/public/apps/{name}/versions"
            )
        )
        resp.raise_for_status()
        return _extract_items(resp.json())[:20]


async def _find_renamed_orchestration(
    session,
    *,
    name: str,
    display_name: str,
    category: str,
    remote_names: set[str],
) -> AppOrchestration | None:
    """在管理端改了 slug 时，按显示名 + 分类认领本地旧记录。

    管理端方案改名（``name`` 变化）后，如果只按 name 匹配就会新建一条，
    旧记录仍留在本地，页面上会出现两个同名方案。内置方案同样按此规则
    认领：内置数据是早期版本带过来的默认项，管理端才是唯一数据源。
    """
    result = await session.execute(
        select(AppOrchestration)
        .where(
            AppOrchestration.display_name == display_name,
            AppOrchestration.category == category,
            AppOrchestration.name != name,
        )
        .order_by(AppOrchestration.id.desc())
    )
    for candidate in result.scalars().all():
        # 旧 slug 仍在远端返回列表中，说明是另一个方案，不能认领
        if candidate.name not in remote_names:
            return candidate
    return None


async def _has_instances(session, orchestration_id: int) -> bool:
    """方案是否还被部署实例引用。"""
    for model in (AppOrchestrationInstance, AppInstance):
        count = await session.scalar(
            select(func.count())
            .select_from(model)
            .where(model.orchestration_id == orchestration_id)
        )
        if count:
            return True
    return False


async def _drop_legacy_builtins(
    session, *, remote_names: set[str], remote_categories: set[str]
) -> int:
    """删除与远端方案重复的内置旧方案。

    早期版本在本地内置过 ``media-stack`` / ``comics-stack`` 等方案，
    管理端接管后这些记录与远端方案同分类，页面上会重复展示。
    只删除无实例引用、且 slug 不在远端列表中的内置记录。
    """
    result = await session.execute(
        select(AppOrchestration).where(
            AppOrchestration.is_builtin.is_(True),
            AppOrchestration.category.in_(remote_categories),
            AppOrchestration.name.notin_(remote_names),
        )
    )
    removed = 0
    for legacy in result.scalars().all():
        if await _has_instances(session, legacy.id):
            logger.info("内置方案 %s 仍被实例引用，保留", legacy.name)
            continue
        await session.execute(
            delete(OrchestrationUpdateEvent).where(
                OrchestrationUpdateEvent.orchestration_name == legacy.name
            )
        )
        await session.delete(legacy)
        removed += 1
        logger.info("已清理重复的内置方案：%s（%s）", legacy.name, legacy.display_name)
    return removed


async def sync_orchestrations(mode: str = "auto") -> dict:
    """从管理端拉取编排方案并 upsert 到本地缓存。

    ``mode`` 仅用于更新事件的来源标记：定时同步传 ``auto``，
    手动触发（POST /sync）传 ``manual``。

    管理端修改方案 slug 时，按显示名 + 分类认领本地旧记录并更新 name，
    避免同一方案在本地重复出现。
    MANAGE_BASE_URL 未配置时直接跳过；网络或解析异常会记录
    last_error 并重新抛出，由调用方决定如何处理。
    """
    global last_sync_at, last_error, last_result

    if not manage_base_urls():
        return {"skipped": True}

    try:
        async with httpx.AsyncClient(timeout=10, follow_redirects=True) as client:
            resp, _ = await with_manage_fallback(
                lambda base_url: client.get(f"{base_url}/api/public/automations")
            )
            resp.raise_for_status()
            items = _extract_items(resp.json())

        created = 0
        updated = 0
        events = 0
        removed = 0
        remote_names = {
            (item.get("name") or "").strip() for item in items if item.get("name")
        }
        remote_categories = {
            item.get("category") or "other"
            for item in items
            if item.get("name")
        }
        async with async_session() as session:
            for sort_order, item in enumerate(items):
                name = item.get("name")
                if not name:
                    logger.warning("管理端返回的方案缺少 name 字段，已跳过：%s", item)
                    continue

                remote_version = item.get("version") or "1.0.0"
                remote_composition = item.get("app_composition") or []
                remote_changelog = item.get("changelog")
                remote_display_name = item.get("display_name") or name
                remote_category = item.get("category") or "other"

                result = await session.execute(
                    select(AppOrchestration).where(AppOrchestration.name == name)
                )
                orchestration = result.scalar_one_or_none()
                from_version: str | None = None
                renamed_from: str | None = None

                if orchestration is None:
                    orchestration = await _find_renamed_orchestration(
                        session,
                        name=name,
                        display_name=remote_display_name,
                        category=remote_category,
                        remote_names=remote_names,
                    )
                    if orchestration is not None:
                        renamed_from = orchestration.name
                        orchestration.name = name
                        from_version = orchestration.version
                        version_changed = from_version != remote_version
                        updated += 1
                        logger.info(
                            "方案 slug 变更：%s → %s（按显示名认领）",
                            renamed_from,
                            name,
                        )
                    else:
                        orchestration = AppOrchestration(name=name, is_builtin=False)
                        session.add(orchestration)
                        created += 1
                        version_changed = True
                else:
                    from_version = orchestration.version
                    version_changed = from_version != remote_version
                    if (
                        not version_changed
                        and (orchestration.app_composition or [])
                        == remote_composition
                        and (orchestration.icon or None)
                        == (item.get("icon") or None)
                        and orchestration.sort_order == sort_order
                        and orchestration.category == remote_category
                        and orchestration.is_listed
                    ):
                        # 内容、顺序与上架状态都没变：跳过更新，避免无谓写入与噪声
                        continue
                    updated += 1

                # 仅更新目录内容与展示状态，is_builtin 保持不变
                orchestration.display_name = remote_display_name
                orchestration.description = item.get("description")
                orchestration.category = remote_category
                orchestration.sort_order = sort_order
                orchestration.is_listed = True
                orchestration.icon = item.get("icon")
                orchestration.website = item.get("website")
                orchestration.source_url = item.get("source_url")
                orchestration.version = remote_version
                orchestration.app_composition = remote_composition
                orchestration.shared_config_schema = (
                    item.get("shared_config_schema") or {}
                )
                orchestration.changelog = remote_changelog

                # 仅版本变化（或首次创建）写更新事件；同版本内容微调不记录
                if version_changed:
                    detail = {
                        "changelog": remote_changelog,
                        "created": from_version is None,
                    }
                    if renamed_from:
                        detail["renamed_from"] = renamed_from
                    session.add(
                        OrchestrationUpdateEvent(
                            orchestration_name=name,
                            scope="template",
                            from_version=from_version,
                            to_version=remote_version,
                            mode=mode,
                            status="applied",
                            detail=detail,
                        )
                    )
                    events += 1

            await session.execute(
                update(AppOrchestration)
                .where(AppOrchestration.name.notin_(remote_names))
                .values(is_listed=False)
            )

            if remote_names and remote_categories:
                removed = await _drop_legacy_builtins(
                    session,
                    remote_names=remote_names,
                    remote_categories=remote_categories,
                )
                if removed:
                    logger.info("编排方案同步：清理 %s 个内置旧方案", removed)

            await session.commit()

        stats = {
            "created": created,
            "updated": updated,
            "removed": removed,
            "total": created + updated,
            "events": events,
        }
        last_sync_at = datetime.now(timezone.utc)
        last_error = None
        last_result = stats
        logger.info("编排方案同步完成：%s", stats)
        return stats
    except Exception as exc:
        last_sync_at = datetime.now(timezone.utc)
        last_error = str(exc)
        logger.warning("编排方案同步失败：%s", exc)
        await _record_sync_failure(exc, mode)
        raise


async def _record_sync_failure(exc: Exception, mode: str) -> None:
    """记录一条同步失败事件。

    失败可能发生在建连阶段或事务提交之后，因此单独开会话写入，
    且写事件本身出错不影响原始异常向上抛出。
    """
    try:
        async with async_session() as session:
            session.add(
                OrchestrationUpdateEvent(
                    orchestration_name="sync",
                    scope="template",
                    mode=mode,
                    status="failed",
                    message=str(exc),
                )
            )
            await session.commit()
    except Exception:
        logger.exception("记录编排方案同步失败事件时出错")


async def sync_all(mode: str = "manual") -> dict:
    """依次同步应用分类、应用市场与编排方案，汇总各部分结果。

    单类同步失败不影响其余部分，失败信息收集到 ``errors`` 中由调用方
    展示；未配置管理端地址时各部分均返回 ``skipped``。
    """
    if not manage_base_urls():
        return {"skipped": True}

    result: dict = {}
    errors: list[str] = []
    steps = (
        ("categories", "应用分类", sync_categories),
        ("apps", "应用市场", sync_apps),
        ("orchestrations", "编排方案", sync_orchestrations),
    )
    for key, label, sync in steps:
        try:
            if key == "orchestrations":
                result[key] = await sync(mode=mode)
            else:
                result[key] = await sync()
        except Exception as exc:
            logger.warning("%s同步失败：%s", label, exc)
            errors.append(f"{label}：{exc}")
            result[key] = {"error": str(exc)}
    result["errors"] = errors
    return result


def get_sync_status() -> dict:
    """返回最近一次同步的状态信息。"""
    return {
        "last_sync_at": last_sync_at,
        "last_error": last_error,
        "last_result": last_result,
    }
