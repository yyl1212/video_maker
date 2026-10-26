from datetime import datetime

from sqlalchemy import JSON, CheckConstraint, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamps, UTCDateTime, Versioned


class Project(Timestamps, Versioned, Base):
    __tablename__ = "projects"
    __table_args__ = (
        CheckConstraint("length(trim(name)) BETWEEN 1 AND 120", name="name_length"),
        {"sqlite_autoincrement": True},
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    style_anchor: Mapped[str] = mapped_column(Text, default="")
    characters: Mapped[list] = mapped_column(JSON, default=list)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime())
