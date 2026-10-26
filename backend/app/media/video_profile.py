"""H3已验证采样规格及原始音画契约；不做内存检测。"""

import math
from dataclasses import dataclass
from pathlib import Path

from app.errors import BackendError

VIDEO_ENCODING = {
    "version": "h3-av-v1",
    "video_codec": "h264",
    "pixel_format": "yuv420p",
    "crf": 20,
    "audio_codec": "aac",
    "audio_sample_rate": 48000,
    "audio_channels": 2,
    "audio_bitrate": "128k",
    "max_source_av_delta": 0.5,
}


@dataclass(frozen=True)
class VideoProfile:
    width: int
    height: int
    num_frames: int
    fps: int
    steps: int
    turbo: bool
    actual_duration: float


@dataclass(frozen=True)
class RawAVResult:
    rgb_path: Path
    pcm_path: Path
    profile: VideoProfile
    audio_sample_rate: int
    audio_channels: int
    audio_duration: float


def video_profile(width, height, duration, preset="quality") -> VideoProfile:
    if (
        any(type(v) is not int for v in (width, height))
        or min(width, height) < 32
        or min(width, height) > 384
        or max(width, height) > 672
        or width % 32
        or height % 32
        or type(duration) not in (int, float)
        or not math.isfinite(duration)
        or not 1 <= duration <= 8
        or preset not in ("quality", "fast")
    ):
        raise BackendError(
            "H3_PROFILE_INVALID", "视频规格无效，请检查尺寸、时长和档位", False
        )
    frames = min(
        (17 * k + 5 for k in range(1, 12)), key=lambda n: abs(n - duration * 24)
    )
    return VideoProfile(
        width,
        height,
        frames,
        24,
        4 if preset == "fast" else 20,
        preset == "fast",
        frames / 24,
    )


def validate_audio_format(rate, channels):
    if (
        type(rate) is not int
        or not 8000 <= rate <= 96000
        or type(channels) is not int
        or channels not in (1, 2)
    ):
        raise BackendError("H3_AUDIO_INVALID", "H3原生PCM音频参数无效", False)


def validate_audio(rate, channels, size: int, duration: float):
    validate_audio_format(rate, channels)
    if size <= 0 or size % (channels * 2):
        raise BackendError("H3_AUDIO_INVALID", "H3原生音频采样长度无效", False)
    actual = size / (rate * channels * 2)
    if abs(actual - duration) > 0.5 + 1e-9:
        raise BackendError(
            "H3_AUDIO_DURATION", "H3音画时长差超过0.5秒，未生成正常有声片段", False
        )
    return actual
