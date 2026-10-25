from sqlalchemy import JSON, CheckConstraint
from sqlalchemy.orm import Mapped, mapped_column

from .base import Base, Timestamps, Versioned


class ModelSettings(Timestamps, Versioned, Base):
    __tablename__ = "model_settings"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: Mapped[int] = mapped_column(primary_key=True, default=1)
    text_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    image_settings: Mapped[dict] = mapped_column(JSON, default=dict)
    video_settings: Mapped[dict] = mapped_column(JSON, default=dict)
