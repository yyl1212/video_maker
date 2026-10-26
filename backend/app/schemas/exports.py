"""导出范围与服务端来源令牌；不接受浏览器文件路径。"""

import math
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Literal
from uuid import UUID

from pydantic import Field, field_validator

from app.errors import AppError
from app.schemas.assets import AssetRead
from app.schemas.common import InputModel, Version

ExportPreset = Literal["portrait", "landscape", "square"]

if TYPE_CHECKING:
    from app.media.ffmpeg import MediaInfo


class ExportProfile(InputModel):
    name: ExportPreset
    width: int
    height: int
    fps: Literal[24] = 24
    audio_sample_rate: Literal[48000] = 48000
    audio_channels: Literal[2] = 2
    video_codec: Literal["h264"] = "h264"
    audio_codec: Literal["aac"] = "aac"
    pixel_format: Literal["yuv420p"] = "yuv420p"
    version: Literal["export-av-v1"] = "export-av-v1"


def export_profile(name: ExportPreset):
    dimensions = {
        "portrait": (720, 1280),
        "landscape": (1280, 720),
        "square": (720, 720),
    }
    if name not in dimensions:
        raise AppError(422, "EXPORT_PROFILE_INVALID", "请选择可用的成片规格")
    width, height = dimensions[name]
    return ExportProfile(name=name, width=width, height=height)


def quantized_frames(duration):
    if (
        not isinstance(duration, (float, int))
        or isinstance(duration, bool)
        or not math.isfinite(duration)
        or duration <= 0
    ):
        raise AppError(422, "EXPORT_DURATION_INVALID", "片段实际时长无效")
    return max(1, math.floor(duration * 24 + 0.5))


@dataclass(frozen=True)
class NormalizedClip:
    path: Path
    source_duration: float
    frame_count: int
    info: "MediaInfo"


@dataclass(frozen=True)
class ExportMediaSpec:
    width: int
    height: int
    num_frames: int
    actual_duration: float


class ExportClipInput(InputModel):
    shot_id: int = Field(gt=0, strict=True)
    review_token: str = Field(min_length=64, max_length=4096)
    confirm_stale: bool = Field(default=False, strict=True)


class ExportSubmit(InputModel):
    idempotency_key: UUID
    expected_project_version: Version
    expected_episode_version: Version
    output_profile: ExportPreset = "portrait"
    clips: list[ExportClipInput] = Field(min_length=1, max_length=24)

    @field_validator("clips")
    @classmethod
    def unique_shots(cls, value):
        if len({c.shot_id for c in value}) != len(value):
            raise ValueError("镜头不能重复")
        return value


class ExportClipRead(InputModel):
    shot_id: int
    order_index: int
    description: str
    shot_version: int
    selection_version: int
    asset: AssetRead | None
    available: bool
    stale: bool
    issues: list[str]
    source_duration: float | None
    frame_count: int | None
    normalized_duration: float | None
    review_token: str


class ExportPreviewRead(InputModel):
    project_id: int
    episode_id: int
    project_name: str
    episode_title: str
    project_version: int
    episode_version: int
    read_only: bool
    clips: list[ExportClipRead]
