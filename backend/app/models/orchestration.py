"""应用编排与编排实例 ORM 模型。"""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


class AppOrchestration(Base):
    """自动化分类组合模板模型。

    定义某个自动化分类（如影视、漫画、书籍）下可一键部署的应用组合方案，
    由多个应用商店 App 组成，支持必选、可选、互斥等关系定义。
    """

    __tablename__ = "app_orchestrations"

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(50), unique=True, nullable=False)
    display_name: Mapped[str] = mapped_column(String(100), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    category: Mapped[str] = mapped_column(String(50), nullable=False, default="other")
    sort_order: Mapped[int] = mapped_column(
        Integer, nullable=False, default=0, server_default="0"
    )
    icon: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 官方网站链接
    website: Mapped[str | None] = mapped_column(String(255), nullable=True)
    # 开源社区/源码仓库链接
    source_url: Mapped[str | None] = mapped_column(String(255), nullable=True)
    version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    is_builtin: Mapped[bool] = mapped_column(Boolean, default=False)
    is_listed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=True, server_default="1"
    )
    # 组合定义：该编排由哪些应用商店 App 组成
    # 示例：
    # [
    #   {"app_name": "moviepilot", "relation": "required"},
    #   {"app_name": "qbittorrent", "relation": "required"},
    #   {"app_name": "jellyfin", "relation": "optional", "group": "player"},
    #   {"app_name": "emby", "relation": "optional", "group": "player"},
    # ]
    app_composition: Mapped[list[dict]] = mapped_column(JSON, default=list)
    # 共享配置 Schema：用于让用户一次性配置跨应用的公共变量
    shared_config_schema: Mapped[dict] = mapped_column(JSON, default=dict)
    # 管理端当前版本的更新说明，仅在同步时刷新
    changelog: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )

    instances: Mapped[list["AppInstance"]] = relationship(
        "AppInstance",
        back_populates="orchestration",
        cascade="all, delete-orphan",
    )
    deployment_groups: Mapped[list["AppOrchestrationInstance"]] = relationship(
        "AppOrchestrationInstance",
        back_populates="orchestration",
        cascade="all, delete-orphan",
    )


class AppOrchestrationInstance(Base):
    """一次组合部署记录，关联多个 AppInstance。"""

    __tablename__ = "app_orchestration_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    orchestration_id: Mapped[int] = mapped_column(
        ForeignKey("app_orchestrations.id"), nullable=False
    )
    instance_name: Mapped[str] = mapped_column(String(100), nullable=False)
    # 组合部署时由用户填写的共享配置
    shared_config: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )
    # 导入/部署时每个应用自身的配置，如访问地址、账号密码、API Key 等
    # 结构示例：
    # {
    #   "moviepilot": {"url": "http://...", "username": "...", "password": "..."},
    #   "qbittorrent": {"url": "http://...", "username": "...", "password": "..."}
    # }
    app_configs: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )
    # 实例运行状态：running / stopped / error / deploying
    status: Mapped[str] = mapped_column(String(20), default="deploying")
    # 部署/升级时对应的方案模板版本快照（注意与 AppInstance.orchestration_version
    # 区分，后者记录的是应用自身版本）
    orchestration_version: Mapped[str | None] = mapped_column(
        String(20), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    orchestration: Mapped["AppOrchestration"] = relationship(
        "AppOrchestration", back_populates="deployment_groups"
    )
    instances: Mapped[list["AppInstance"]] = relationship(
        "AppInstance", back_populates="orchestration_group"
    )


class AppInstance(Base):
    """应用实例模型。

    记录用户基于某个 AppOrchestration 或 App 创建的一次部署，
    一对一关联到 DockerComposeProject。
    """

    __tablename__ = "app_instances"

    id: Mapped[int] = mapped_column(primary_key=True)
    # 基于编排部署时填充
    orchestration_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_orchestrations.id"), nullable=True
    )
    # 基于应用商店应用部署时填充
    app_id: Mapped[int | None] = mapped_column(
        ForeignKey("apps.id"), nullable=True
    )
    # 组合部署时填充，标识属于同一次组合部署
    orchestration_group_id: Mapped[int | None] = mapped_column(
        ForeignKey("app_orchestration_instances.id"), nullable=True
    )
    project_id: Mapped[int | None] = mapped_column(
        ForeignKey("docker_compose_projects.id"), nullable=True, unique=True
    )
    instance_name: Mapped[str] = mapped_column(String(100), nullable=False)
    config: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )
    # 实例当前部署的版本
    orchestration_version: Mapped[str] = mapped_column(String(20), default="1.0.0")
    # 实例运行状态：running / stopped / error / upgrading / deploying
    status: Mapped[str] = mapped_column(String(20), default="running")
    # 是否通过应用级 DNS 加速接入 AdGuard（只影响该应用的 Compose 服务）
    acceleration_enabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default=text("false"), nullable=False
    )
    # 实例级备份配置，可覆盖编排默认配置
    backup_config: Mapped[dict] = mapped_column(
        MutableDict.as_mutable(JSON), default=dict
    )
    last_backup_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    orchestration: Mapped["AppOrchestration | None"] = relationship(
        "AppOrchestration", back_populates="instances"
    )
    orchestration_group: Mapped["AppOrchestrationInstance | None"] = relationship(
        "AppOrchestrationInstance", back_populates="instances"
    )
    app: Mapped["App | None"] = relationship(
        "App", back_populates="instances"
    )
    project: Mapped["DockerComposeProject"] = relationship(
        "DockerComposeProject"
    )
    backups: Mapped[list["AppInstanceBackup"]] = relationship(
        "AppInstanceBackup",
        back_populates="instance",
        cascade="all, delete-orphan",
        order_by="AppInstanceBackup.created_at.desc()",
    )


class AppInstanceBackup(Base):
    """应用实例备份记录模型。

    记录每次手动或自动备份的元数据，归档文件保存在文件系统中。
    """

    __tablename__ = "app_instance_backups"

    id: Mapped[int] = mapped_column(primary_key=True)
    instance_id: Mapped[int] = mapped_column(
        ForeignKey("app_instances.id"), nullable=False
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    # 备份归档文件绝对路径
    path: Mapped[str] = mapped_column(String(500), nullable=False)
    # 归档文件大小（字节）
    size: Mapped[int] = mapped_column(Integer, default=0)
    # 备份类型：manual 手动 / auto 自动 / pre_upgrade 升级前自动备份
    backup_type: Mapped[str] = mapped_column(String(20), default="manual")
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())

    instance: Mapped["AppInstance"] = relationship(
        "AppInstance", back_populates="backups"
    )


class OrchestrationUpdateEvent(Base):
    """编排方案/实例组版本变更记录。

    模板同步发现版本变化、实例组部署/升级/重建时各写一条，
    供前端展示"最近更新"与排查升级结果。
    scope: template（模板同步）/ instance（实例组）
    mode: auto（定时同步）/ manual（用户触发）
    status: applied / failed
    """

    __tablename__ = "orchestration_update_events"

    id: Mapped[int] = mapped_column(primary_key=True)
    orchestration_name: Mapped[str] = mapped_column(String(50), index=True)
    scope: Mapped[str] = mapped_column(String(20))
    group_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    from_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    mode: Mapped[str] = mapped_column(String(20))
    status: Mapped[str] = mapped_column(String(20))
    # 附加信息：模板事件记录 changelog/created，实例事件记录应用增减清单
    detail: Mapped[dict] = mapped_column(JSON, default=dict)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )


class MusicAutoOrganizeFile(Base):
    """Music Tag Web 自动整理的文件处理状态。

    记录每个源文件的处理结果与失败重试次数，避免同一文件被反复刮削/整理。
    文件被整理移动后源路径消失，状态保留用于回溯；同一路径再次出现且
    大小/修改时间变化时视为新文件重新处理。
    """

    __tablename__ = "music_auto_organize_files"
    __table_args__ = (
        UniqueConstraint("group_id", "path", name="uq_music_auto_file_group_path"),
        Index("ix_music_auto_file_group_status", "group_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # 所属编排实例组（或单应用实例组）
    group_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # 文件在 Music Tag Web 容器内的路径
    path: Mapped[str] = mapped_column(String(2048), nullable=False)
    size: Mapped[int] = mapped_column(Integer, default=0)
    # Music Tag Web file_list 返回的更新时间字符串
    mtime: Mapped[str | None] = mapped_column(String(32), nullable=True)
    # pending / done / failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    processed_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class ReaderAutoSyncBook(Base):
    """Reader 书架自动导出的书籍状态。

    记录每本书最近一次成功导出的章节数与目标文件，用于判断是否需要
    重新缓存章节或导出 EPUB；失败重试次数与最近错误也保存在这里，
    避免同一本坏书在每轮定时任务里反复占用时间。
    """

    __tablename__ = "reader_auto_sync_books"
    __table_args__ = (
        UniqueConstraint(
            "group_id", "book_url", name="uq_reader_auto_book_group_url"
        ),
        Index("ix_reader_auto_book_group_status", "group_id", "status"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    # 所属编排实例组
    group_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    # Reader 书架中的 bookUrl
    book_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    name: Mapped[str] = mapped_column(String(255), nullable=False, default="")
    # 最近一次导出的 EPUB 宿主机路径
    destination: Mapped[str | None] = mapped_column(String(2048), nullable=True)
    # 最近一次成功导出时的章节总数，-1 表示尚未成功导出
    exported_total: Mapped[int] = mapped_column(Integer, default=-1)
    # 最近一次检查到的章节总数
    total_chapters: Mapped[int] = mapped_column(Integer, default=0)
    # pending / done / failed
    status: Mapped[str] = mapped_column(String(20), default="pending")
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    exported_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), onupdate=func.now()
    )


class OrchestrationUpgradeBackup(Base):
    """升级前的实例组配置快照。

    只保存配置与 Compose YAML，不移动应用数据目录；用于升级失败或
    用户需要回查旧配置时恢复挂载、端口、环境变量等运行时设置。
    不设置外键，实例组删除后快照仍保留。
    """

    __tablename__ = "orchestration_upgrade_backups"

    id: Mapped[int] = mapped_column(primary_key=True)
    group_id: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    orchestration_name: Mapped[str] = mapped_column(String(50), nullable=False)
    instance_name: Mapped[str] = mapped_column(String(100), nullable=False)
    from_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    to_version: Mapped[str | None] = mapped_column(String(20), nullable=True)
    # 完整快照：group.shared_config / group.app_configs /
    # 各应用 config 与当前 Compose 版本内容（可能含密码，仅存库不外发）
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime, server_default=func.now(), index=True
    )
