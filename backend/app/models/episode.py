from datetime import datetime

from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamps, UTCDateTime, Versioned


class Episode(Timestamps, Versioned, Base):
    __tablename__ = "episodes"
    __table_args__ = (
        CheckConstraint("length(trim(title)) BETWEEN 1 AND 200", name="title_length"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    project_id: Mapped[int] = mapped_column(ForeignKey("projects.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    script_text: Mapped[str] = mapped_column(Text, default="")
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
