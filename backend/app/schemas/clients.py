"""本机模型连接与白名单参数；各生成处理器读取提交快照。"""

from typing import Literal
from urllib.parse import urlsplit

from pydantic import Field, field_validator

from .common import InputModel, Version


def local_url(value: str) -> str:
    try:
        parts = urlsplit(value)
        valid = (
            parts.scheme in ("http", "https")
            and parts.hostname in ("localhost", "127.0.0.1", "::1")
            and not parts.username
            and not parts.password
            and not parts.query
            and not parts.fragment
            and parts.path.rstrip("/") in ("", "/v1")
            and (parts.port is None or 1 <= parts.port <= 65535)
        )
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("模型地址必须为本机回环HTTP/HTTPS地址")
    return value.rstrip("/")


class TextSettings(InputModel):
    base_url: str = "http://127.0.0.1:1234/v1"
    model: str = Field(default="", max_length=200)
    _local = field_validator("base_url")(local_url)


class ImageSettings(InputModel):
    host: Literal["127.0.0.1", "localhost", "::1"] = "127.0.0.1"
    port: int = Field(default=7859, ge=1, le=65535, strict=True)
    tls: bool = True
    compression: bool = True
    model: str = Field(default="", max_length=200)
    width: int = Field(default=768, ge=64, le=4096)
    height: int = Field(default=1280, ge=64, le=4096)
    steps: int = Field(default=20, ge=1, le=150)
    guidance_scale: float = Field(default=1, ge=0, le=100)
    seed: int = Field(default=42, ge=0, le=4294967295, strict=True)
    negative_prompt: str = Field(default="", max_length=4000)


class VideoSettings(InputModel):
    base_url: str = "http://127.0.0.1:11234"
    model: str = Field(default="ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit", max_length=200)
    preset: Literal["quality", "fast"] = "quality"
    seed: int = Field(default=42, ge=0, le=4294967295, strict=True)
    _local = field_validator("base_url")(local_url)


class SettingsRead(InputModel):
    version: int
    text: TextSettings
    image: ImageSettings
    video: VideoSettings


class SettingsUpdate(InputModel):
    expected_version: Version
    text: TextSettings
    image: ImageSettings
    video: VideoSettings
