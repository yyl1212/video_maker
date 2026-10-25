import asyncio
import math
import os
import signal
import struct
from datetime import timedelta

import pytest

from app.errors import AppError, BackendError
from app.media.ffmpeg import FFmpegService
from app.models.base import utc_now
from app.services.runner import ProcessResult


def raw_av(tmp_path, *, rate=32000, channels=2, delta=0):
    from app.media.video_profile import RawAVResult, video_profile

    profile = video_profile(32, 32, 1, "fast")
    rgb, pcm = tmp_path / "raw.rgb", tmp_path / "raw.pcm"
    rgb.write_bytes(bytes([35, 100, 60]) * 32 * 32 * profile.num_frames)
    samples = round((profile.actual_duration + delta) * rate)
    pcm.write_bytes(
        b"".join(
            struct.pack("<h", int(5000 * math.sin(2 * math.pi * 440 * i / rate)))
            * channels
            for i in range(samples)
        )
    )
    return RawAVResult(rgb, pcm, profile, rate, channels, samples / rate)


def execute(tmp_path):
    async def run(arguments):
        process = await asyncio.create_subprocess_exec(
            *arguments,
            start_new_session=True,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        try:
            stdout, stderr = await process.communicate()
            if process.returncode:
                raise AppError(422, "MEDIA_PROCESS_FAILED", "媒体处理失败")
            path = tmp_path / f"{process.pid}.stdout"
            error = tmp_path / f"{process.pid}.stderr"
            path.write_bytes(stdout)
            error.write_bytes(stderr)
            return ProcessResult(path, error, process.returncode)
        except BaseException:
            if process.returncode is None:
                os.killpg(process.pid, signal.SIGKILL)
                await process.wait()
            raise

    return run


@pytest.mark.parametrize(
    "rate,channels,delta", [(8000, 1, -0.25), (32000, 2, 0), (96000, 2, 0.25)]
)
def test_raw_frames_and_native_audio_become_valid_aligned_h264_aac(
    tmp_path, rate, channels, delta
):
    raw = raw_av(tmp_path, rate=rate, channels=channels, delta=delta)
    target = tmp_path / "clip.mp4"
    info = asyncio.run(
        FFmpegService().encode_av(
            raw, target, utc_now() + timedelta(seconds=5), run_process=execute(tmp_path)
        )
    )
    assert (
        info.has_audio and info.audio_sample_rate == 48000 and info.audio_channels == 2
    )
    assert (info.width, info.height) == (32, 32)
    assert abs(info.duration_seconds - raw.profile.actual_duration) < 0.05
    assert target.is_file() and raw.pcm_path.exists()
    FFmpegService().validate_decoding(target)


@pytest.mark.parametrize(
    "field,value",
    [
        ("audio_sample_rate", 7999),
        ("audio_sample_rate", 96001),
        ("audio_sample_rate", True),
        ("audio_channels", 0),
        ("audio_channels", 3),
        ("audio_channels", True),
    ],
)
def test_invalid_pcm_parameters_fail_before_encoding(tmp_path, field, value):
    from dataclasses import replace

    raw = replace(raw_av(tmp_path), **{field: value})
    with pytest.raises((AppError, BackendError)):
        asyncio.run(
            FFmpegService().encode_av(
                raw,
                tmp_path / "clip.mp4",
                utc_now() + timedelta(seconds=5),
                run_process=execute(tmp_path),
            )
        )
    assert not (tmp_path / "clip.mp4").exists()


@pytest.mark.parametrize(
    "corruption", ["missing", "empty", "odd", "too_short", "too_long", "rgb"]
)
def test_missing_or_misaligned_audio_and_rgb_fail(tmp_path, corruption):
    raw = raw_av(tmp_path)
    if corruption == "missing":
        raw.pcm_path.unlink()
    elif corruption == "empty":
        raw.pcm_path.write_bytes(b"")
    elif corruption == "odd":
        raw.pcm_path.write_bytes(raw.pcm_path.read_bytes() + b"x")
    elif corruption == "too_short":
        raw.pcm_path.write_bytes(b"\x00" * 32)
    elif corruption == "too_long":
        raw.pcm_path.write_bytes(b"\x00" * (32000 * 2 * 2 * 2))
    else:
        raw.rgb_path.write_bytes(b"bad")
    with pytest.raises((AppError, BackendError)):
        asyncio.run(
            FFmpegService().encode_av(
                raw,
                tmp_path / "clip.mp4",
                utc_now() + timedelta(seconds=5),
                run_process=execute(tmp_path),
            )
        )


def test_encoder_preserves_existing_target_and_fixed_deadline(tmp_path):
    target = tmp_path / "clip.mp4"
    target.write_bytes(b"original")
    with pytest.raises((AppError, BackendError)):
        asyncio.run(
            FFmpegService().encode_av(
                raw_av(tmp_path),
                target,
                utc_now() + timedelta(seconds=5),
                run_process=execute(tmp_path),
            )
        )
    assert target.read_bytes() == b"original"

    async def slow(_):
        await asyncio.sleep(5)

    with pytest.raises((AppError, BackendError, TimeoutError)):
        asyncio.run(
            FFmpegService().encode_av(
                raw_av(tmp_path),
                tmp_path / "new.mp4",
                utc_now() + timedelta(milliseconds=30),
                run_process=slow,
            )
        )
