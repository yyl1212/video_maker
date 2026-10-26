"""文本草稿与人工采纳契约；模型不能指定数据库ID或文件路径。"""

from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.schemas.clients import TextSettings
from app.schemas.common import InputModel, Version
from app.schemas.projects import Character


class DraftShot(InputModel):
    order_index: int = Field(ge=1, le=24, strict=True)
    raw_description: str = Field(min_length=1, max_length=4000)
    dialogue: str = Field(max_length=4000)
    character_ids: list[str] = Field(max_length=100)
    audio_prompt: str = Field(max_length=4000)
    duration_seconds: float = Field(ge=1, le=8, strict=True, allow_inf_nan=False)

    @field_validator("raw_description")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("画面不能为空")
        return value

    @field_validator("character_ids")
    @classmethod
    def unique(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("角色不能重复")
        return value


class StoryboardDraft(InputModel):
    shots: list[DraftShot] = Field(min_length=1, max_length=24)

    @field_validator("shots")
    @classmethod
    def continuous_order(cls, value):
        if [s.order_index for s in value] != list(range(1, len(value) + 1)):
            raise ValueError("镜头编号须从1连续排列")
        return value


class PromptDraft(InputModel):
    image_prompt: str = Field(min_length=1, max_length=8000)
    video_prompt: str = Field(min_length=1, max_length=8000)
    audio_prompt: str = Field(min_length=1, max_length=4000)

    @field_validator("image_prompt", "video_prompt", "audio_prompt")
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError("Prompt不能为空")
        return value


class TextJobSubmit(InputModel):
    idempotency_key: UUID
    expected_project_version: Version
    expected_episode_version: Version
    expected_shot_version: Version | None = None


class DraftApply(InputModel):
    project_id: Annotated[int, Field(gt=0, strict=True)]
    expected_version: Version
    edited_value: StoryboardDraft | PromptDraft
    replace_confirm: bool = Field(default=False, strict=True)


class DraftRead(InputModel):
    task_id: int
    project_id: int
    episode_id: int
    shot_id: int | None
    kind: Literal["PARSE_SCRIPT", "OPTIMIZE_PROMPTS"]
    value: StoryboardDraft | PromptDraft
    expected_version: int
    characters: list[Character]
    existing_shot_ids: list[int]
    applied_at: datetime | None
    apply_allowed: bool
    conflict_reason: str | None


class TextConnectionTest(InputModel):
    text: TextSettings
