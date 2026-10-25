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

from .base import Base, Timestamps, UTCDateTime


class Task(Timestamps, Base):
    __tablename__ = "tasks"
    __table_args__ = (
        UniqueConstraint("project_id", "idempotency_key"),
        CheckConstraint("active_slot = 1", name="single_slot"),
        CheckConstraint(
            "status IN ('PENDING','RUNNING','DONE','FAILED')", name="status"
        ),
        CheckConstraint(
            "kind IN ('PARSE_SCRIPT','OPTIMIZE_PROMPTS','IMAGE','VIDEO','EXPORT')",
            name="kind",
        ),
        CheckConstraint(
            "progress IS NULL OR progress BETWEEN 0 AND 1", name="progress"
        ),
        CheckConstraint(
            "episode_id IS NOT NULL AND ((kind IN ('PARSE_SCRIPT','EXPORT') "
            "AND shot_id IS NULL) OR (kind IN ('OPTIMIZE_PROMPTS','IMAGE','VIDEO') "
            "AND shot_id IS NOT NULL))",
            name="scope",
        ),
        Index(
            "uq_tasks_active_slot",
            "active_slot",
            unique=True,
            sqlite_where=text("status IN ('PENDING','RUNNING')"),
        ),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    episode_id: Mapped[int | None] = mapped_column(ForeignKey("episodes.id"))
    shot_id: Mapped[int | None] = mapped_column(ForeignKey("shots.id"))
    kind: Mapped[str] = mapped_column(String(24))
    status: Mapped[str] = mapped_column(String(12), default="PENDING")
    active_slot: Mapped[int] = mapped_column(default=1)
    idempotency_key: Mapped[str] = mapped_column(String(36))
    request_hash: Mapped[str] = mapped_column(String(64))
    input_snapshot: Mapped[dict] = mapped_column(JSON)
    input_hash: Mapped[str] = mapped_column(String(64))
    result_json: Mapped[dict | None] = mapped_column(JSON)
    stage: Mapped[str] = mapped_column(String(200), default="等待开始")
    progress: Mapped[float | None] = mapped_column(Float)
    # 仅作为索引；真实归属由 Asset.source_task_id 反向验证，避免外键环。
    output_asset_id: Mapped[int | None]
    error_code: Mapped[str | None] = mapped_column(String(80))
    error_message: Mapped[str | None] = mapped_column(Text)
    execution_owner: Mapped[str | None] = mapped_column(String(36))
    execution_json: Mapped[dict] = mapped_column(JSON, default=dict)
    heartbeat_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    deadline_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
    applied_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
