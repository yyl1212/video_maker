from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import InputModel, RecordRead, Version

PositiveId = Annotated[int, Field(ge=1, strict=True)]


class NoNullInput(InputModel):
    @model_validator(mode="after")
    def no_null(self):
        if any(getattr(self, key) is None for key in self.model_fields_set):
            raise ValueError("修改字段不能为null")
        return self


class ShotImageSettings(NoNullInput):
    model: str | None = Field(default=None, max_length=200)
    width: int | None = Field(default=None, ge=64, le=4096, strict=True)
    height: int | None = Field(default=None, ge=64, le=4096, strict=True)
    steps: int | None = Field(default=None, ge=1, le=150, strict=True)
    guidance_scale: float | None = Field(default=None, ge=0, le=100)
    seed: int | None = Field(default=None, ge=0, le=4294967295, strict=True)
    negative_prompt: str | None = Field(default=None, max_length=4000)


class ShotVideoSettings(NoNullInput):
    width: int = Field(default=384, ge=32, le=672, strict=True)
    height: int = Field(default=672, ge=32, le=672, strict=True)
    preset: Literal["quality", "fast"] | None = None
    seed: int | None = Field(default=None, ge=0, le=4294967295, strict=True)

    @model_validator(mode="after")
    def dimensions(self):
        if self.width % 32 or self.height % 32 or min(self.width, self.height) > 384:
            raise ValueError("视频尺寸须为32的倍数，短边不超过384")
        return self


class ShotFields(NoNullInput):
    raw_description: str = Field(default="", max_length=4000)
    dialogue: str = Field(default="", max_length=4000)
    character_ids: list[str] = Field(default_factory=list, max_length=100)
    image_prompt: str = Field(default="", max_length=8000)
    video_prompt: str = Field(default="", max_length=8000)
    audio_prompt: str = Field(default="", max_length=4000)
    duration_seconds: float = Field(default=5, ge=1, le=8, allow_inf_nan=False)
    image_settings: ShotImageSettings = Field(default_factory=ShotImageSettings)
    video_settings: ShotVideoSettings = Field(default_factory=ShotVideoSettings)

    @field_validator("character_ids")
    @classmethod
    def unique_characters(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("角色不能重复")
        return value


class ShotCreate(ShotFields):
    pass


class ShotPatch(ShotFields):
    expected_version: Version


class ShotDelete(InputModel):
    expected_version: Version


class ShotReorder(InputModel):
    expected_version: Version
    shot_ids: list[PositiveId] = Field(max_length=24)

    @field_validator("shot_ids")
    @classmethod
    def unique_shots(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("分镜编号不能重复")
        return value


class ReferenceInput(InputModel):
    asset_id: PositiveId
    role: Literal["character", "scene", "other"] = "other"


class ReferencesUpdate(InputModel):
    expected_version: Version
    references: list[ReferenceInput] = Field(max_length=8)

    @field_validator("references")
    @classmethod
    def unique_assets(cls, value):
        if len({ref.asset_id for ref in value}) != len(value):
            raise ValueError("参考图片不能重复")
        return value


class ReferenceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    asset_id: int
    role: str
    position: int


class SelectionUpdate(InputModel):
    shot_version: Version
    selection_version: Version
    first_frame_asset_id: PositiveId | None = None
    selected_video_asset_id: PositiveId | None = None
    confirm_uploaded: bool = Field(default=False, strict=True)
    expected_image_input_hash: str | None = Field(
        default=None, pattern="^[0-9a-f]{64}$"
    )
    expected_video_input_hash: str | None = Field(
        default=None, pattern="^[0-9a-f]{64}$"
    )

    @model_validator(mode="after")
    def has_selection(self):
        if not (
            self.model_fields_set & {"first_frame_asset_id", "selected_video_asset_id"}
        ):
            raise ValueError("请选择要采用或取消的版本")
        return self


class SelectionRead(BaseModel):
    version: int
    first_frame_asset_id: int | None
    selected_video_asset_id: int | None
    first_frame_stale: bool
    video_stale: bool
    first_frame_issue: str | None
    video_issue: str | None


class ShotRead(RecordRead):
    episode_id: int
    order_index: int
    raw_description: str
    dialogue: str
    character_ids: list[str]
    image_prompt: str
    video_prompt: str
    audio_prompt: str
    duration_seconds: float
    actual_duration_seconds: float
    image_settings: dict
    video_settings: dict
    status: Literal["DRAFT", "READY"]
    deleted_at: datetime | None
    references: list[ReferenceRead]
    reference_asset_ids: list[int]
    selection: SelectionRead
    image_input_hash: str
    video_input_hash: str | None
    video_ready: bool
    configuration_errors: list[str]
    compiled_image_prompt: str
    compiled_video_prompt: str
    read_only: bool


class ShotListRead(InputModel):
    episode_version: int
    shots: list[ShotRead]
