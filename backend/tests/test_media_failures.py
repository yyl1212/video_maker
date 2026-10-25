import subprocess
import sys

import pytest

from app.errors import AppError
from app.media.ffmpeg import FFmpegService


def test_media_process_timeout_terminates_child(monkeypatch):
    original = subprocess.Popen
    started = []

    def tracked(*args, **kwargs):
        process = original(*args, **kwargs)
        started.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", tracked)
    with pytest.raises(AppError) as error:
        FFmpegService().run(
            [sys.executable, "-c", "import time; time.sleep(30)"], timeout=0.05
        )
    assert error.value.code == "MEDIA_TIMEOUT"
    assert started[0].poll() is not None


def test_invalid_mp4_does_not_probe_as_success(tmp_path):
    invalid = tmp_path / "broken.mp4"
    invalid.write_bytes(b"not a video")
    with pytest.raises(AppError) as error:
        FFmpegService().probe(invalid)
    assert error.value.code == "INVALID_MEDIA"
