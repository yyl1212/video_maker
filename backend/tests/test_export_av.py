import array
import asyncio
import json
import math
import subprocess
from datetime import timedelta

import pytest
from PIL import Image
from test_av_encoding import execute

from app.errors import AppError
from app.media.ffmpeg import FFmpegService
from app.models.base import utc_now


@pytest.fixture
def original_clips(tmp_path):
    specs = [
        ("red", 64, 96, 25, 0.64, 44100, 1, "3/2"),
        ("blue", 96, 64, 30, 0.7, None, None, "1"),
        ("green", 80, 80, 7, 0.8, 32000, 2, "1"),
    ]
    paths = []
    for i, (color, w, h, fps, duration, rate, channels, sar) in enumerate(specs):
        path = tmp_path / f"original-{i}.mp4"
        args = [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            f"color=c={color}:s={w}x{h}:r={fps}",
        ]
        if rate:
            args += [
                "-f",
                "lavfi",
                "-i",
                f"sine=frequency={440 + i * 220}:sample_rate={rate}",
                "-c:a",
                "aac",
                "-ac",
                str(channels),
            ]
        args += [
            "-t",
            str(duration),
            "-vf",
            f"setsar={sar}",
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(path),
        ]
        subprocess.run(args, check=True, capture_output=True, timeout=10)
        paths.append(path)
    return paths


def pcm(path):
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-i",
            str(path),
            "-map",
            "0:a:0",
            "-ac",
            "1",
            "-ar",
            "48000",
            "-f",
            "s16le",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout
    values = array.array("h")
    values.frombytes(raw)
    return values


def rms(values):
    return math.sqrt(sum(v * v for v in values) / max(1, len(values)))


def first_frame(path, width, height, seconds=0):
    raw = subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-ss",
            str(seconds),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-",
        ],
        capture_output=True,
        check=True,
        timeout=10,
    ).stdout
    return Image.frombytes("RGB", (width, height), raw)


def test_mixed_aspect_audio_and_frame_rates_normalize_and_concat_without_drift(
    tmp_path, original_clips
):
    from app.schemas.exports import export_profile

    profile = export_profile("portrait")
    ffmpeg = FFmpegService()
    original = [ffmpeg.probe(p) for p in original_clips]

    async def run():
        clips = []
        for i, (source, info) in enumerate(zip(original_clips, original, strict=True)):
            clips.append(
                await ffmpeg.normalize_clip(
                    source,
                    tmp_path / f"normalized-{i}.mp4",
                    profile,
                    utc_now() + timedelta(seconds=20),
                    source_duration=info.duration_seconds,
                    has_audio=info.has_audio,
                    run_process=execute(tmp_path),
                )
            )
        target = tmp_path / "final.mp4"
        result = await ffmpeg.concat(
            clips,
            target,
            profile,
            utc_now() + timedelta(seconds=20),
            run_process=execute(tmp_path),
        )
        return clips, target, result

    normalized, target, info = asyncio.run(run())
    expected = sum(c.frame_count for c in normalized) / 24
    assert abs(info.duration_seconds - expected) <= 0.15
    assert (
        info.has_audio and info.audio_sample_rate == 48000 and info.audio_channels == 2
    )
    assert (info.width, info.height) == (720, 1280)
    for source, clip in zip(original, normalized, strict=True):
        assert (
            abs(clip.info.duration_seconds - source.duration_seconds) <= 1 / 24 + 1e-6
        )
        probe = json.loads(
            subprocess.run(
                [
                    "ffprobe",
                    "-v",
                    "error",
                    "-show_streams",
                    "-of",
                    "json",
                    str(clip.path),
                ],
                capture_output=True,
                check=True,
                timeout=10,
            ).stdout
        )
        assert all(abs(float(s.get("start_time", 0))) < 0.001 for s in probe["streams"])
        assert {s["codec_name"] for s in probe["streams"]} == {"h264", "aac"}
    values = pcm(target)
    start = 0
    for i, clip in enumerate(normalized):
        end = start + clip.frame_count * 2000
        segment = values[start + 2400 : end - 2400]
        assert rms(segment) < 15 if i == 1 else rms(segment) > 200
        start = end
    # 原始红片SAR=3/2，显示比例为1:1；720竖屏内应为720x720且上下补边。
    with first_frame(normalized[0].path, 720, 1280) as image:
        pixels = image.load()
        ys = [
            y for y in range(1280) if pixels[360, y][0] > 80 and pixels[360, y][1] < 40
        ]
        assert abs(len(ys) - 720) < 5 and min(ys) > 270
        assert image.getpixel((10, 10)) == pytest.approx((0, 0, 0), abs=5)
    ffmpeg.validate_decoding(target)


@pytest.mark.parametrize(
    "preset,dimensions", [("landscape", (1280, 720)), ("square", (720, 720))]
)
def test_output_profiles_pad_without_stretch_and_keep_audio(
    tmp_path, original_clips, preset, dimensions
):
    from app.schemas.exports import export_profile

    source = original_clips[1]
    ffmpeg = FFmpegService()
    info = ffmpeg.probe(source)
    result = asyncio.run(
        ffmpeg.normalize_clip(
            source,
            tmp_path / "normalized.mp4",
            export_profile(preset),
            utc_now() + timedelta(seconds=20),
            source_duration=info.duration_seconds,
            has_audio=False,
            run_process=execute(tmp_path),
        )
    )
    assert (result.info.width, result.info.height) == dimensions
    assert rms(pcm(result.path)) < 1


def test_audio_delay_relative_to_video_is_preserved_after_timestamp_reset(tmp_path):
    from app.schemas.exports import export_profile

    source = tmp_path / "offset.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:r=24",
            "-itsoffset",
            "0.25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:sample_rate=48000",
            "-t",
            "1",
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            "-output_ts_offset",
            "2",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    info = FFmpegService().probe(source)
    result = asyncio.run(
        FFmpegService().normalize_clip(
            source,
            tmp_path / "normalized.mp4",
            export_profile("square"),
            utc_now() + timedelta(seconds=20),
            source_duration=info.duration_seconds,
            has_audio=True,
            run_process=execute(tmp_path),
        )
    )
    values = pcm(result.path)
    assert rms(values[2000:6000]) < 20 and rms(values[20000:30000]) > 200


def test_no_overwrite_and_deadline_apply_to_normalization(tmp_path, original_clips):
    from app.schemas.exports import export_profile

    target = tmp_path / "existing.mp4"
    target.write_bytes(b"original")
    kwargs = {
        "source_duration": 0.64,
        "has_audio": True,
        "run_process": execute(tmp_path),
    }
    with pytest.raises(AppError):
        asyncio.run(
            FFmpegService().normalize_clip(
                original_clips[0],
                target,
                export_profile("portrait"),
                utc_now() + timedelta(seconds=10),
                **kwargs,
            )
        )
    assert target.read_bytes() == b"original"

    async def slow(*args):
        await asyncio.sleep(5)

    with pytest.raises((TimeoutError, AppError)):
        asyncio.run(
            FFmpegService().normalize_clip(
                original_clips[0],
                tmp_path / "late.mp4",
                export_profile("portrait"),
                utc_now() + timedelta(milliseconds=30),
                source_duration=0.64,
                has_audio=True,
                run_process=slow,
            )
        )


def test_single_frame_high_fps_clip_keeps_at_least_one_frame(tmp_path):
    from app.schemas.exports import export_profile

    source = tmp_path / "one-frame.mp4"
    subprocess.run(
        [
            "ffmpeg",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=red:s=64x64:r=60",
            "-frames:v",
            "1",
            "-c:v",
            "libx264",
            str(source),
        ],
        check=True,
        capture_output=True,
        timeout=10,
    )
    info = FFmpegService().probe(source)
    result = asyncio.run(
        FFmpegService().normalize_clip(
            source,
            tmp_path / "normalized.mp4",
            export_profile("square"),
            utc_now() + timedelta(seconds=20),
            source_duration=info.duration_seconds,
            has_audio=False,
            run_process=execute(tmp_path),
        )
    )
    assert result.frame_count == 1
    assert result.info.duration_seconds == pytest.approx(1 / 24, abs=0.001)
    assert first_frame(result.path, 720, 720).getpixel((360, 360))[0] > 100
