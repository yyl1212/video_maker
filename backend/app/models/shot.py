from datetime import datetime

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Float,
    ForeignKey,
    Index,
    String,
    Text,
    UniqueConstraint,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamps, UTCDateTime, Versioned, utc_now


class Shot(Timestamps, Versioned, Base):
    __tablename__ = "shots"
    __table_args__ = (
        CheckConstraint("order_index >= 1", name="positive_order"),
        CheckConstraint("duration_seconds BETWEEN 1 AND 8", name="duration"),
        CheckConstraint("status IN ('DRAFT', 'READY')", name="status"),
        Index(
            "uq_active_shot_order",
            "episode_id",
            "order_index",
            unique=True,
            sqlite_where=text("deleted_at IS NULL"),
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    episode_id: Mapped[int] = mapped_column(ForeignKey("episodes.id"), index=True)
    order_index: Mapped[int]
    raw_description: Mapped[str] = mapped_column(Text, default="")
    dialogue: Mapped[str] = mapped_column(Text, default="")
    character_ids: Mapped[list] = mapped_column(JSON, default=list)
    image_prompt: Mapped[str] = mapped_column(Text, default="")
    video_prompt: Mapped[str] = mapped_column(Text, default="")
    audio_prompt: Mapped[str] = mapped_column(Text, default="")
    duration_seconds: Mapped[float] = mapped_column(Float, default=5)
    image_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    video_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(10), default="DRAFT")
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime())


class ShotReferenceAsset(Base):
    __tablename__ = "shot_reference_assets"
    __table_args__ = (
        UniqueConstraint("shot_id", "position"),
        CheckConstraint("position >= 0", name="position"),
        CheckConstraint("role IN ('character', 'scene', 'other')", name="role"),
    )

    shot_id: Mapped[int] = mapped_column(ForeignKey("shots.id"), primary_key=True)
    asset_id: Mapped[int] = mapped_column(ForeignKey("assets.id"), primary_key=True)
    position: Mapped[int]
    role: Mapped[str] = mapped_column(String(12), default="other")


class ShotSelection(Versioned, Base):
    __tablename__ = "shot_selections"

    shot_id: Mapped[int] = mapped_column(ForeignKey("shots.id"), primary_key=True)
    first_frame_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    selected_video_asset_id: Mapped[int | None] = mapped_column(ForeignKey("assets.id"))
    first_frame_input_hash: Mapped[str | None] = mapped_column(String(64))
    video_input_hash: Mapped[str | None] = mapped_column(String(64))
    updated_at: Mapped[datetime] = mapped_column(
        UTCDateTime(),
        default=utc_now,
        onupdate=utc_now,
    )
