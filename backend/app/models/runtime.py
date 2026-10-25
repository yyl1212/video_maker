from sqlalchemy import CheckConstraint, ForeignKey, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamps, Versioned


class RuntimeState(Timestamps, Versioned, Base):
    __tablename__ = "runtime_state"
    __table_args__ = (
        CheckConstraint("id = 1", name="singleton"),
        CheckConstraint("state IN ('IDLE','ACTIVE','NEEDS_RESET')", name="state"),
    )

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    state: Mapped[str] = mapped_column(String(12), default="IDLE")
    active_task_id: Mapped[int | None] = mapped_column(ForeignKey("tasks.id"))
    reason: Mapped[str] = mapped_column(Text, default="")
    affected_backend: Mapped[str | None] = mapped_column(String(20))
