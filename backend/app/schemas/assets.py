from datetime import datetime
from typing import Literal

from pydantic import Field, field_validator, model_validator

from .common import InputModel, RecordRead, Version

AssetTag = Literal[
    "reference", "character", "scene", "first_frame", "video_clip", "final_video"
]


class AssetPatch(InputModel):
    expected_version: Version
    name: str | None = Field(default=None, min_length=1, max_length=200)
    tags: list[AssetTag] | None = Field(default=None, max_length=6)

    @model_validator(mode="after")
    def no_null(self):
        if any(
            getattr(self, k) is None
            for k in self.model_fields_set - {"expected_version"}
        ):
            raise ValueError("字段不能为null")
        return self

    @field_validator("tags")
    @classmethod
    def unique(cls, value):
        if value is not None and len(set(value)) != len(value):
            raise ValueError("标签不能重复")
        return value


class DeleteAsset(InputModel):
    expected_version: Version


class AssetRead(RecordRead):
    project_id: int
    shot_id: int | None
    source_task_id: int | None
    type: Literal["image", "video"]
    tags: list[str]
    name: str
    file_path: str
    thumbnail_path: str | None
    sha256: str
    size_bytes: int
    mime_type: str
    width: int
    height: int
    duration_seconds: float | None
    has_audio: bool
    audio_sample_rate: int | None
    audio_channels: int | None
    metadata_json: dict
    deleted_at: datetime | None
