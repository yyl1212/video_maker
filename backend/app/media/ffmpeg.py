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

    async def encode_av(
        self, result, target: Path, deadline, *, run_process, io=None
    ) -> MediaInfo:
        """原生音画在同一任务截止内编码与核验；执行器由TaskContext提供。"""
        import asyncio

        from app.models.base import utc_now

        io = io or asyncio.to_thread
        async with asyncio.timeout(max(0, (deadline - utc_now()).total_seconds())):
            await io(self.validate_raw_av, result, target)
            p = result.profile
            duration = f"{p.actual_duration:.9f}"
            await run_process(
                [
                    "ffmpeg",
                    "-v",
                    "error",
                    "-nostdin",
                    "-n",
                    "-f",
                    "rawvideo",
                    "-pix_fmt",
                    "rgb24",
                    "-s",
                    f"{p.width}x{p.height}",
                    "-r",
                    "24",
                    "-i",
                    str(result.rgb_path),
                    "-f",
                    "s16le",
                    "-ar",
                    str(result.audio_sample_rate),
                    "-ac",
                    str(result.audio_channels),
                    "-i",
                    str(result.pcm_path),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "libx264",
                    "-threads",
                    "2",
                    "-crf",
                    "20",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "aac",
                    "-b:a",
                    "128k",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    "-af",
                    f"apad,atrim=duration={duration},asetpts=PTS-STARTPTS",
                    "-t",
                    duration,
                    "-frames:v",
                    str(p.num_frames),
                    "-movflags",
                    "+faststart",
                    str(target),
                ]
            )
            probe = await run_process(
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
                    "-show_streams",
                    "-show_format",
                    "-of",
                    "json",
                    str(target),
                ]
            )
            info = await io(self.verify_encoded_av, probe.stdout_path, p)
            # 探测有两条流仍不能证明完整可解码，发布前检查全部帧及音频。
            await run_process(
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
                    str(target),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0",
                    "-f",
                    "null",
                    "-",
                ]
            )
            return info

    @staticmethod
    def validate_raw_av(result, target):
        from app.media.video_profile import validate_audio, video_profile

        p = result.profile
        valid = video_profile(
            p.width,
            p.height,
            max(1, p.actual_duration),
            "fast" if p.turbo else "quality",
        )
        if p != valid or target.exists():
            raise AppError(422, "INVALID_AV_TARGET", "音画规格无效或输出文件已存在")
        try:
            if result.rgb_path.stat().st_size != p.width * p.height * p.num_frames * 3:
                raise ValueError("RGB length")
            validate_audio(
                result.audio_sample_rate,
                result.audio_channels,
                result.pcm_path.stat().st_size,
                p.actual_duration,
            )
        except (OSError, ValueError) as exc:
            raise AppError(422, "INVALID_RAW_AV", "原始视频或原生音轨文件无效") from exc

    @staticmethod
    def verify_encoded_av(probe_path, profile):
        try:
            if probe_path.stat().st_size > 1024 * 1024:
                raise ValueError("probe too large")
            data = json.loads(probe_path.read_text())
            videos = [s for s in data["streams"] if s.get("codec_type") == "video"]
            audios = [s for s in data["streams"] if s.get("codec_type") == "audio"]
            if len(videos) != 1 or len(audios) != 1:
                raise ValueError("streams")
            video, audio = videos[0], audios[0]
            duration, audio_duration = (
                float(video["duration"]),
                float(audio["duration"]),
            )
            if (
                video["codec_name"] != "h264"
                or video["pix_fmt"] != "yuv420p"
                or (video["width"], video["height"]) != (profile.width, profile.height)
                or video["r_frame_rate"] != "24/1"
                or int(video["nb_frames"]) != profile.num_frames
                or not math.isfinite(duration)
                or abs(duration - profile.actual_duration) > 0.05
                or audio["codec_name"] != "aac"
                or int(audio["sample_rate"]) != 48000
                or int(audio["channels"]) != 2
                or not math.isfinite(audio_duration)
                or abs(audio_duration - duration) > 0.1
            ):
                raise ValueError("output specs")
            return MediaInfo(profile.width, profile.height, duration, True, 48000, 2)
        except (OSError, ValueError, KeyError, TypeError) as exc:
            raise AppError(
                422, "INVALID_ENCODED_AV", "输出MP4未通过H.264/AAC音画完整性核验"
            ) from exc
