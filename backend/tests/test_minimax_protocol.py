import asyncio
import base64
import hashlib
import json
from dataclasses import replace
from datetime import timedelta

import httpx2 as httpx
import pytest
from PIL import Image

from app.errors import BackendError
from app.models.base import utc_now
from app.schemas.clients import VideoSettings

MODEL = "ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit"


def request(tmp_path):
    from app.clients.minimax_h3 import VideoRequest
    from app.media.video_profile import video_profile

    path = tmp_path / "first.png"
    Image.new("RGB", (32, 32), "olive").save(path)
    return VideoRequest(
        prompt="原创机器人招手，声音为木制关节轻响",
        first_frame_path=path,
        first_frame_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
        settings=VideoSettings(preset="fast"),
        profile=video_profile(32, 32, 1, "fast"),
        deadline=utc_now() + timedelta(seconds=2),
        directory=tmp_path,
    )


def complete(**changes):
    frames = 22
    rate = 8000
    return {
        "type": "complete",
        "width": 32,
        "height": 32,
        "frames": frames,
        "fps": 24,
        "format": "rgb8",
        "data": base64.b64encode(bytes([40, 80, 120]) * 32 * 32 * frames).decode(),
        "audio_data": base64.b64encode(b"\x01\x00" * int(frames / 24 * rate)).decode(),
        "audio_sample_rate": rate,
        "audio_channels": 1,
        "audio_format": "pcm_s16le",
        **changes,
    }


def sse(event):
    return b"data: " + json.dumps(event, ensure_ascii=False).encode() + b"\r\n\r\n"


class Stream(httpx.AsyncByteStream):
    def __init__(self, chunks, *, slow=False):
        self.chunks, self.slow, self.closed = chunks, slow, False

    async def __aiter__(self):
        for chunk in self.chunks:
            if self.slow:
                await asyncio.sleep(0.01)
            yield chunk

    async def aclose(self):
        self.closed = True


def backend(stream, calls=None, status=200):
    from app.clients.minimax_h3 import MiniMaxH3Backend

    calls = [] if calls is None else calls

    async def route(req):
        calls.append(req)
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [
                        {"id": MODEL, "state": "unloaded", "capabilities": ["video"]}
                    ]
                },
            )
        return httpx.Response(
            status, headers={"Content-Type": "text/event-stream"}, stream=stream
        )

    return MiniMaxH3Backend(transport=httpx.MockTransport(route))


def test_selected_frame_sha_and_fragmented_crlf_sse_are_preserved(tmp_path):
    value = request(tmp_path)
    calls = []
    events = []
    lifecycle = []
    raw = sse({"type": "progress", "stage": "Sampling", "step": 1, "total": 4}) + sse(
        complete()
    )
    stream = Stream([raw[i : i + 137] for i in range(0, len(raw), 137)])

    async def progress(stage, number):
        events.append((stage, number))

    async def start():
        lifecycle.append("start")

    async def end():
        lifecycle.append("end")

    result = asyncio.run(
        backend(stream, calls).generate(
            value, progress, on_dispatch=start, on_terminal=end
        )
    )
    post = next(c for c in calls if c.method == "POST")
    data = json.loads(post.content)
    assert (
        hashlib.sha256(
            base64.b64decode(data["first_frame_image"], validate=True)
        ).hexdigest()
        == value.first_frame_sha256
    )
    assert (
        data["model"] == MODEL
        and data["num_frames"] == 22
        and data["steps"] == 4
        and data["turbo"] is True
    )
    assert data["stream"] is True and "last_frame_image" not in data
    assert post.url.path == "/v1/video/generations"
    assert [c.url.path for c in calls] == ["/v1/models", "/v1/video/generations"]
    assert len(result.rgb_path.read_bytes()) == 32 * 32 * 22 * 3
    assert result.pcm_path.stat().st_size > 0 and result.audio_sample_rate == 8000
    assert events and lifecycle == ["start", "end"] and stream.closed


@pytest.mark.parametrize(
    "changes",
    [
        {"data": "@@@"},
        {"data": base64.b64encode(b"bad").decode()},
        {"format": "rgba8"},
        {"width": True},
        {"frames": 23},
        {"fps": 25},
        {"audio_data": None},
        {"audio_data": ""},
        {"audio_data": "bad?"},
        {"audio_sample_rate": 7999},
        {"audio_sample_rate": 96001},
        {"audio_channels": 3},
        {"audio_channels": True},
        {"audio_format": "pcm_f32le"},
        {"audio_data": "AQ=="},
        {"audio_data": base64.b64encode(b"\x00\x00" * 100).decode()},
    ],
)
def test_invalid_complete_fails_after_known_remote_terminal(tmp_path, changes):
    ended = []

    async def terminal():
        ended.append(True)

    with pytest.raises(BackendError) as error:
        asyncio.run(
            backend(Stream([sse(complete(**changes))])).generate(
                request(tmp_path), on_terminal=terminal
            )
        )
    assert not error.value.execution_uncertain and ended == [True]
    assert not (tmp_path / "audio.pcm").exists()


def test_truncated_stream_is_uncertain(tmp_path):
    with pytest.raises(BackendError) as error:
        asyncio.run(
            backend(
                Stream([sse({"type": "progress", "step": 1, "total": 4})])
            ).generate(request(tmp_path))
        )
    assert (
        error.value.execution_uncertain and error.value.code == "H3_STREAM_INTERRUPTED"
    )


def test_fixed_deadline_does_not_reset_on_progress(tmp_path):
    value = replace(request(tmp_path), deadline=utc_now() + timedelta(milliseconds=100))
    stream = Stream([sse({"type": "progress", "step": 1, "total": 4})] * 100, slow=True)
    with pytest.raises(BackendError) as error:
        asyncio.run(backend(stream).generate(value))
    assert error.value.code == "H3_TIMEOUT" and error.value.execution_uncertain
    assert stream.closed


@pytest.mark.parametrize(
    "status,uncertain",
    [(400, False), (409, False), (422, False), (500, True), (307, True)],
)
def test_http_errors_are_sanitized_and_redirects_never_followed(
    tmp_path, status, uncertain
):
    calls = []
    with pytest.raises(BackendError) as error:
        asyncio.run(
            backend(Stream([b"private stack"]), calls, status).generate(
                request(tmp_path)
            )
        )
    assert error.value.execution_uncertain is uncertain
    assert len([c for c in calls if c.method == "POST"]) == 1
    assert "private" not in error.value.message


def test_invalid_json_and_protocol_event_limit_fail_closed(tmp_path, monkeypatch):
    from app.clients import minimax_h3

    monkeypatch.setattr(minimax_h3, "MAX_EVENT_BYTES", 1024)
    for raw in (
        b'data: {"type":"progress","step":NaN}\n\n',
        b'data: {"type":"progress","type":"complete"}\n\n',
        b"data: " + b"x" * 1100,
    ):
        with pytest.raises(BackendError) as error:
            asyncio.run(backend(Stream([raw])).generate(request(tmp_path)))
        assert error.value.execution_uncertain


def test_changed_first_frame_rejected_without_network(tmp_path):
    value = request(tmp_path)
    value.first_frame_path.write_bytes(b"changed")
    calls = []
    with pytest.raises(BackendError) as error:
        asyncio.run(backend(Stream([]), calls).generate(value))
    assert (
        error.value.code == "H3_FIRST_FRAME_INVALID"
        and not error.value.execution_uncertain
    )
    assert not calls


def test_server_error_event_is_terminal_and_sanitized(tmp_path):
    ended = []

    async def terminal():
        ended.append(True)

    with pytest.raises(BackendError) as error:
        asyncio.run(
            backend(
                Stream([sse({"type": "error", "message": "private path"})])
            ).generate(request(tmp_path), on_terminal=terminal)
        )
    assert (
        ended == [True]
        and not error.value.execution_uncertain
        and "private" not in error.value.message
    )


def test_video_profile_and_native_prompt_match_saved_configuration():
    from app.media.video_profile import video_profile
    from app.prompts.compiler import compile_video

    profile = video_profile(384, 672, 5, "quality")
    assert (profile.num_frames, profile.steps, profile.turbo) == (124, 20, False)
    compiled = compile_video(
        {
            "duration_seconds": 5,
            "video_prompt": "A robot waves.",
            "audio_prompt": "Soft wood clicks.",
            "dialogue": "早上好",
            "first_frame_sha256": "abc",
            "video_settings": {},
        }
    )
    assert "<Picture 1>" in compiled.prompt
    assert all(
        part in compiled.prompt
        for part in (
            "integrated_multimodal_description",
            "overall_soundscape",
            "non_diegetic_music",
            "早上好",
        )
    )


def test_inference_silence_before_task_deadline_is_not_cut_by_short_read_timeout(
    tmp_path,
):
    value = replace(request(tmp_path), deadline=utc_now() + timedelta(seconds=60))

    async def route(req):
        if req.method == "GET":
            return httpx.Response(
                200,
                json={
                    "data": [{"id": MODEL, "state": "ready", "capabilities": ["video"]}]
                },
            )
        # 模拟合法推理阶段35秒无事件；传输层读取截止不能先于任务总截止。
        read_timeout = req.extensions["timeout"]["read"]
        if read_timeout is not None and read_timeout < 35:
            raise httpx.ReadTimeout("legitimate model computation is still running")
        return httpx.Response(
            200,
            headers={"Content-Type": "text/event-stream"},
            stream=Stream([sse(complete())]),
        )

    from app.clients.minimax_h3 import MiniMaxH3Backend

    result = asyncio.run(
        MiniMaxH3Backend(transport=httpx.MockTransport(route)).generate(value)
    )
    assert result.pcm_path.stat().st_size > 0
