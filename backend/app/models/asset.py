from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, Float, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamps, UTCDateTime, Versioned


class Asset(Timestamps, Versioned, Base):
    __tablename__ = "assets"
    __table_args__ = (
        CheckConstraint("type IN ('image', 'video')", name="type"),
        CheckConstraint("width > 0 AND height > 0 AND size_bytes > 0", name="size"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    shot_id: Mapped[int | None] = mapped_column(ForeignKey("shots.id"))
    source_task_id: Mapped[int | None] = mapped_column(
        ForeignKey("tasks.id"),
        unique=True,
    )
    type: Mapped[str] = mapped_column(String(8))
    tags: Mapped[list] = mapped_column(JSON, default=list)
    name: Mapped[str] = mapped_column(String(200), default="")
    file_path: Mapped[str] = mapped_column(Text, unique=True)
    thumbnail_path: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str] = mapped_column(String(64))
    size_bytes: Mapped[int]
    mime_type: Mapped[str] = mapped_column(String(100))
    width: Mapped[int]
    height: Mapped[int]
    duration_seconds: Mapped[float | None] = mapped_column(Float)
    has_audio: Mapped[bool] = mapped_column(default=False)
    audio_sample_rate: Mapped[int | None]
    audio_channels: Mapped[int | None]
    metadata_json: Mapped[dict] = mapped_column(JSON, default=dict)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
