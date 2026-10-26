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

    async def timeline_probe(self, path, deadline, *, run_process, io):
        import asyncio

        from app.models.base import utc_now

        async with asyncio.timeout(max(0, (deadline - utc_now()).total_seconds())):
            result = await run_process(
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
                    str(path),
                ]
            )

            def read():
                if result.stdout_path.stat().st_size > 1024 * 1024:
                    raise ValueError("probe too large")
                return json.loads(result.stdout_path.read_text())

            return await io(read)

    async def normalize_clip(
        self,
        source,
        target,
        profile,
        deadline,
        *,
        source_duration,
        has_audio,
        run_process,
        io=None,
    ):
        import asyncio

        from app.models.base import utc_now
        from app.schemas.exports import NormalizedClip, export_profile, quantized_frames

        io = io or asyncio.to_thread
        async with asyncio.timeout(max(0, (deadline - utc_now()).total_seconds())):
            if profile != export_profile(profile.name) or target.exists():
                raise AppError(
                    422, "EXPORT_TARGET_INVALID", "导出规格无效或临时输出已存在"
                )
            data = await self.timeline_probe(
                source, deadline, run_process=run_process, io=io
            )
            try:
                video = next(
                    s
                    for s in data["streams"]
                    if s.get("codec_type") == "video"
                    and not s.get("disposition", {}).get("attached_pic")
                )
                actual = float(video.get("duration", data["format"]["duration"]))
                start = float(video.get("start_time", 0))
                actual_audio = any(
                    s.get("codec_type") == "audio" for s in data["streams"]
                )
                if (
                    not math.isfinite(actual)
                    or not math.isfinite(start)
                    or abs(actual - source_duration) > 0.001
                    or actual_audio != has_audio
                ):
                    raise ValueError("source metadata changed")
            except (ValueError, KeyError, TypeError, StopIteration) as exc:
                raise AppError(
                    422, "EXPORT_SOURCE_INVALID", "片段实际规格与已冻结清单不符"
                ) from exc
            frames = quantized_frames(actual)
            duration = frames / 24
            samples = frames * 2000
            arguments = [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-n",
                "-copyts",
                "-protocol_whitelist",
                "file",
                "-enable_drefs",
                "0",
                "-f",
                "mov",
                "-i",
                str(source),
            ]
            if not has_audio:
                arguments += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
            # 在降帧率之前补一帧尾部，避免单帧60fps片段被fps滤镜取整为空。
            video_filter = (
                f"[0:V:0]setpts=PTS-STARTPTS,tpad=stop_mode=clone:stop_duration=0.041666667,fps=24:round=near,"
                f"scale={profile.width}:{profile.height}:force_original_aspect_ratio=decrease:force_divisible_by=2:reset_sar=1,"
                f"pad={profile.width}:{profile.height}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1,"
                f"trim=end_frame={frames},setpts=N/(24*TB),format=yuv420p[v]"
            )
            # 保留音频相对视频的起点偏移；first_pts=0插入首部静音或丢弃过早采样。
            audio_input = (
                f"[0:a:0]asetpts=PTS-({start:.9f})/TB"
                if has_audio
                else "[1:a:0]asetpts=PTS-STARTPTS"
            )
            audio_filter = (
                audio_input + ",aresample=48000:async=1:first_pts=0,"
                "aformat=sample_rates=48000:channel_layouts=stereo,"
                f"apad,atrim=end_sample={samples},asetpts=N/SR/TB[a]"
            )
            arguments += [
                "-filter_complex",
                video_filter + ";" + audio_filter,
                "-map",
                "[v]",
                "-map",
                "[a]",
            ]
            arguments += self.export_encode_arguments(frames, duration, target)
            await run_process(arguments)
            info = await self.verify_timeline(
                target, profile, frames, deadline, run_process=run_process, io=io
            )
            return NormalizedClip(target, actual, frames, info)

    @staticmethod
    def export_encode_arguments(frames, duration, target):
        return [
            "-c:v",
            "libx264",
            "-threads",
            "2",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-r",
            "24",
            "-c:a",
            "aac",
            "-b:a",
            "128k",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-frames:v",
            str(frames),
            "-t",
            f"{duration:.9f}",
            "-movflags",
            "+faststart",
            str(target),
        ]

    async def verify_timeline(
        self, target, profile, frames, deadline, *, run_process, io
    ):
        from app.schemas.exports import ExportMediaSpec

        result = await self.timeline_probe(
            target, deadline, run_process=run_process, io=io
        )

        # 复用已验证的H.264/AAC/整帧/零外部依赖核验，期望时长来自规范化整帧数。
        def verify():
            check = target.parent / f".verify-{target.stem}.json"
            try:
                with check.open("x") as output:
                    json.dump(result, output)
                info = self.verify_encoded_av(
                    check,
                    ExportMediaSpec(profile.width, profile.height, frames, frames / 24),
                )
                if any(
                    abs(float(s.get("start_time", 0))) > 0.001
                    for s in result["streams"]
                    if s.get("codec_type") in ("audio", "video")
                ):
                    raise AppError(
                        422, "EXPORT_TIMESTAMPS_INVALID", "规范化片段时间戳未从零开始"
                    )
                return info
            finally:
                check.unlink(missing_ok=True)

        return await io(verify)

    async def concat(self, clips, target, profile, deadline, *, run_process, io=None):
        import asyncio

        from app.models.base import utc_now
        from app.schemas.exports import export_profile

        io = io or asyncio.to_thread
        async with asyncio.timeout(max(0, (deadline - utc_now()).total_seconds())):
            if (
                not clips
                or len(clips) > 24
                or target.exists()
                or profile != export_profile(profile.name)
            ):
                raise AppError(422, "EXPORT_TARGET_INVALID", "导出片段、规格或目标无效")
            arguments = ["ffmpeg", "-v", "error", "-nostdin", "-n"]
            filters = []
            labels = []
            for i, clip in enumerate(clips):
                arguments += [
                    "-protocol_whitelist",
                    "file",
                    "-enable_drefs",
                    "0",
                    "-f",
                    "mov",
                    "-i",
                    str(clip.path),
                ]
                filters.append(
                    f"[{i}:v:0]trim=end_frame={clip.frame_count},setpts=N/(24*TB)[v{i}]"
                )
                filters.append(
                    f"[{i}:a:0]apad,atrim=end_sample={clip.frame_count * 2000},"
                    f"asetpts=N/SR/TB[a{i}]"
                )
                labels.append(f"[v{i}][a{i}]")
            filters.append("".join(labels) + f"concat=n={len(clips)}:v=1:a=1[v][a]")
            total = sum(c.frame_count for c in clips)
            arguments += [
                "-filter_complex",
                ";".join(filters),
                "-map",
                "[v]",
                "-map",
                "[a]",
            ]
            arguments += self.export_encode_arguments(total, total / 24, target)
            await run_process(arguments)
            info = await self.verify_timeline(
                target, profile, total, deadline, run_process=run_process, io=io
            )
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
