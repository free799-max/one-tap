"""Docker Compose 编排相关 ORM 模型。"""

from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.database import Base


COMPOSE_PROJECT_LABEL = "onetap.compose.project"
"""Compose 项目标签键，用于标识容器归属。"""


class DockerComposeProject(Base):
    """Docker Compose 项目模型。

    记录 Compose 项目的元数据、当前激活版本与运行时配置文件路径。
    """

    __tablename__ = "docker_compose_projects"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_name: Mapped[str] = mapped_column(
        String(100), unique=True, nullable=False
    )
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    config_files: Mapped[str | None] = mapped_column(Text, nullable=True)
    working_dir: Mapped[str | None] = mapped_column(String(500), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    versions: Mapped[list["DockerComposeVersion"]] = relationship(
        "DockerComposeVersion",
        back_populates="project",
        cascade="all, delete-orphan",
        order_by="DockerComposeVersion.version_number.desc()",
    )
    stack: Mapped["DockerComposeStack | None"] = relationship(
        "DockerComposeStack",
        back_populates="project",
        uselist=False,
        cascade="all, delete-orphan",
    )


class DockerComposeVersion(Base):
    """Docker Compose 版本模型。

    保存每个 Compose 项目的历史 YAML 版本内容。
    """

    __tablename__ = "docker_compose_versions"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("docker_compose_projects.id"), nullable=False
    )
    version_number: Mapped[int] = mapped_column(Integer, nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_current: Mapped[bool] = mapped_column(Boolean, default=False)
    created_by_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime, default=func.now())

    project: Mapped["DockerComposeProject"] = relationship(
        "DockerComposeProject", back_populates="versions"
    )


class DockerComposeStack(Base):
    """Docker Compose Stack 运行时状态缓存。"""

    __tablename__ = "docker_compose_stacks"

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(
        ForeignKey("docker_compose_projects.id"), nullable=False, unique=True
    )
    status: Mapped[str] = mapped_column(String(20), default="unknown")
    service_count: Mapped[int] = mapped_column(Integer, default=0)
    running_count: Mapped[int] = mapped_column(Integer, default=0)
    ports: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_action: Mapped[str | None] = mapped_column(String(20), nullable=True)
    last_action_at: Mapped[datetime | None] = mapped_column(DateTime, nullable=True)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime, default=func.now(), onupdate=func.now()
    )

    project: Mapped["DockerComposeProject"] = relationship(
        "DockerComposeProject", back_populates="stack"
    )
