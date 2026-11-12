"""应用专属客户端包。"""

from app.services.apps.base import (
    AppClient,
    AppClientError,
    AuthVerifyResult,
    CommandRunner,
    FixResultItem,
    InspectItem,
    InspectReport,
    LogsReader,
    ProvisionResult,
)
from app.services.apps.registry import get_client, list_supported_clients, register_client

# 自动注册已实现的应用客户端
from app.services.apps.comic_auto import ComicAutoClient
from app.services.apps.ermao_library import ErmaoLibraryClient
from app.services.apps.fnos_media import FnosMediaClient
from app.services.apps.jellyfin import JellyfinClient
from app.services.apps.kavita import KavitaClient
from app.services.apps.komga import KomgaClient
from app.services.apps.moviepilot import MoviePilotClient
from app.services.apps.music_dl import MusicDLClient
from app.services.apps.music_tag_web import MusicTagWebClient
from app.services.apps.navidrome import NavidromeClient
from app.services.apps.qbittorrent import QBittorrentClient
from app.services.apps.reader import ReaderAppClient

__all__ = [
    "AppClient",
    "AppClientError",
    "AuthVerifyResult",
    "CommandRunner",
    "FixResultItem",
    "InspectItem",
    "InspectReport",
    "LogsReader",
    "ProvisionResult",
    "get_client",
    "list_supported_clients",
    "ComicAutoClient",
    "ErmaoLibraryClient",
    "FnosMediaClient",
    "JellyfinClient",
    "KavitaClient",
    "KomgaClient",
    "MoviePilotClient",
    "MusicDLClient",
    "MusicTagWebClient",
    "NavidromeClient",
    "QBittorrentClient",
    "ReaderAppClient",
    "register_client",
]
