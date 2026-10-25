"""只执行本地参数数组；限制读取协议，超时终止进程组。"""

import json
import math
import os
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from app.errors import AppError


@dataclass(frozen=True)
class MediaInfo:
    width: int
    height: int
    duration_seconds: float
    has_audio: bool
    audio_sample_rate: int | None
    audio_channels: int | None


class FFmpegService:
    def run(self, arguments: list[str], timeout: float = 30) -> bytes:
        try:
            process = subprocess.Popen(
                arguments,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                stdin=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError as exc:
            raise AppError(
                503, "FFMPEG_UNAVAILABLE", "请安装可用的FFmpeg和ffprobe"
            ) from exc
        try:
            stdout, _ = process.communicate(timeout=timeout)
        except (subprocess.TimeoutExpired, BaseException) as exc:
            # 不允许取消后留下孤立的媒体子进程。
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            process.communicate()
            if isinstance(exc, subprocess.TimeoutExpired):
                raise AppError(
                    422, "MEDIA_TIMEOUT", "素材处理超时，请换用较短素材"
                ) from exc
            raise
        if process.returncode:
            raise AppError(422, "INVALID_MEDIA", "素材损坏或格式不受支持")
        return stdout

    def probe(self, path: Path) -> MediaInfo:
        raw = self.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-protocol_whitelist",
                "file",
                "-enable_drefs",
                "0",
                "-f",
                "mov",
                "-show_entries",
                "format=duration,format_name:stream=codec_type,width,height,duration,"
                "sample_rate,channels:stream_disposition=attached_pic",
                "-of",
                "json",
                str(path),
            ]
        )
        try:
            data = json.loads(raw)
            streams = data["streams"]
            video = next(
                s
                for s in streams
                if s["codec_type"] == "video"
                and not s.get("disposition", {}).get("attached_pic")
            )
            audio = next((s for s in streams if s["codec_type"] == "audio"), None)
            duration = float(video.get("duration", data["format"]["duration"]))
            width, height = int(video["width"]), int(video["height"])
            if (
                not math.isfinite(duration)
                or not 0 < duration <= 600
                or not 0 < width <= 8192
                or not 0 < height <= 8192
            ):
                raise ValueError("bounds")
            return MediaInfo(
                width,
                height,
                duration,
                audio is not None,
                int(audio["sample_rate"]) if audio else None,
                int(audio["channels"]) if audio else None,
            )
        except (ValueError, KeyError, TypeError, StopIteration) as exc:
            raise AppError(
                422, "INVALID_MEDIA", "视频信息无效，最长支持10分钟素材"
            ) from exc

    def thumbnail(self, source: Path, target: Path) -> None:
        self.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-protocol_whitelist",
                "file",
                "-enable_drefs",
                "0",
                "-f",
                "mov",
                "-i",
                str(source),
                "-map",
                "0:V:0",
                "-frames:v",
                "1",
                "-vf",
                "scale=320:320:force_original_aspect_ratio=decrease",
                "-update",
                "1",
                "-y",
                str(target),
            ]
        )

    def validate_decoding(self, source: Path) -> None:
        # 元数据和首帧完整不能证明后半段音画可解码，发布前检查完整输入。
        self.run(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-xerror",
                "-err_detect",
                "explode",
                "-protocol_whitelist",
                "file",
                "-enable_drefs",
                "0",
                "-f",
                "mov",
                "-i",
                str(source),
                "-map",
                "0:V:0",
                "-map",
                "0:a?",
                "-f",
                "null",
                "-",
            ],
            timeout=20,
        )
