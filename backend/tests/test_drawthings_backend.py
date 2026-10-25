import asyncio
from datetime import timedelta

import pytest
from drawthings_py.generated.dt_grpc import image_service as proto
from drawthings_py.generated.dt_grpc.config_generated import GenerationConfiguration
from drawthings_py.image.image_buffer import ImageBuffer
from PIL import Image

from app.clients import drawthings as dt
from app.errors import BackendError
from app.models.base import utc_now
from app.schemas.clients import ImageSettings

MODEL = "flux_2_klein_9b_kv_i8x.ckpt"


@pytest.fixture
def wire(monkeypatch):
    class Channel:
        def __init__(self, *args, **kwargs):
            self.options = kwargs
            self.closed = False
            state.channels.append(self)

        def close(self):
            self.closed = True

    class Stub:
        def __init__(self, channel):
            pass

        async def echo(self, request, **kwargs):
            return proto.EchoReply(files=state.files)

        async def files_exist(self, request, **kwargs):
            state.file_checks.append(request.files)
            return proto.FileExistenceResponse(
                files=request.files, existences=[state.installed]
            )

        async def generate_image(self, request, **kwargs):
            state.requests.append(request)
            if state.error:
                raise state.error
            yield proto.ImageGenerationResponse(
                current_signpost=proto.ImageGenerationSignpostProto(
                    sampling=proto.ImageGenerationSignpostProtoSampling(step=1)
                )
            )
            if not state.empty:
                tensor = ImageBuffer(bytes([70, 90, 40]) * (128 * 192), 128, 192, 3)
                yield proto.ImageGenerationResponse(
                    generated_images=[tensor.to_tensor()]
                )

    class State:
        files = [MODEL]
        file_checks = []
        installed = True
        requests = []
        channels = []
        error = None
        empty = False

    state = State()
    monkeypatch.setattr(dt, "Channel", Channel)
    monkeypatch.setattr(dt, "ImageGenerationServiceStub", Stub)
    return state


def request(tmp_path, **overrides):
    refs = []
    for i, color in enumerate(("red", "blue")):
        path = tmp_path / f"{i}.png"
        Image.new("RGB", (32, 32), color).save(path)
        refs.append(path)
    return dt.ImageRequest(
        prompt="Picture 1, Picture 2 原创双人场景",
        negative_prompt="watermark",
        references=tuple(refs),
        settings=ImageSettings(
            model=MODEL,
            width=128,
            height=192,
            seed=0,
            guidance_scale=0,
            tls=False,
            **overrides,
        ),
        deadline=utc_now() + timedelta(seconds=2),
        output_path=tmp_path / "result.png",
    )


def test_wire_keeps_order_false_zero_and_actual_flatbuffer(tmp_path, wire):
    stages, lifecycle = [], []

    async def progress(stage, value):
        stages.append((stage, value))

    async def dispatched():
        lifecycle.append("start")

    async def terminal():
        lifecycle.append("terminal")

    result = asyncio.run(
        dt.DrawThingsBackend().generate(
            request(tmp_path),
            progress,
            on_dispatch=dispatched,
            on_terminal=terminal,
        )
    )
    message = wire.requests[0]
    config = GenerationConfiguration.GetRootAs(message.configuration, 0)
    assert config.Seed() == 0
    assert config.GuidanceScale() == 0
    assert config.HiresFix() is False
    assert config.ResolutionDependentShift() is False
    assert config.StartWidth() * 64 == 128
    assert config.StartHeight() * 64 == 192
    assert config.BatchSize() == config.BatchCount() == 1
    tensors = [t.tensor for hint in message.hints for t in hint.tensors]
    assert len(tensors) == 2
    first, second = [ImageBuffer.from_tensor(t) for t in tensors]
    assert first.data[0] > first.data[2]
    assert second.data[2] > second.data[0]
    assert message.negative_prompt == "watermark"
    assert stages and lifecycle == ["start", "terminal"]
    assert result.path == tmp_path / "result.png"
    assert (result.width, result.height) == (128, 192)
    assert wire.channels[0].options["ssl"] is False
    assert wire.channels[0].closed


def test_normal_stream_without_image_is_known_failure(tmp_path, wire):
    wire.empty = True
    ended = []

    async def terminal():
        ended.append(True)

    with pytest.raises(BackendError) as error:
        asyncio.run(
            dt.DrawThingsBackend().generate(
                request(tmp_path),
                on_terminal=terminal,
            )
        )
    assert error.value.code == "IMAGE_EMPTY_RESULT"
    assert not error.value.execution_uncertain
    assert ended == [True]


def test_broken_stream_is_uncertain_and_does_not_claim_stopped(tmp_path, wire):
    wire.error = TimeoutError("private upstream path")
    ended = []

    async def terminal():
        ended.append(True)

    with pytest.raises(BackendError) as error:
        asyncio.run(
            dt.DrawThingsBackend().generate(request(tmp_path), on_terminal=terminal)
        )
    assert error.value.execution_uncertain
    assert not ended and not (tmp_path / "result.png").exists()
    assert "private" not in error.value.message


def test_write_failure_is_safe_after_terminal(tmp_path, wire, monkeypatch):
    def broken(*args, **kwargs):
        raise OSError("private disk")

    monkeypatch.setattr(ImageBuffer, "to_file", broken)
    with pytest.raises(BackendError) as error:
        asyncio.run(dt.DrawThingsBackend().generate(request(tmp_path)))
    assert error.value.code == "IMAGE_FILE_WRITE_FAILED"
    assert not error.value.execution_uncertain


def test_probe_cannot_generate_and_connection_error_is_not_uncertain(wire):
    value = asyncio.run(dt.DrawThingsBackend().test_connection(ImageSettings()))
    assert value.models[0].file == MODEL
    assert not wire.requests


@pytest.mark.parametrize("changes", [{"width": 130}, {"model": "unknown.ckpt"}])
def test_unsupported_configuration_rejected_before_dispatch(tmp_path, wire, changes):
    value = request(tmp_path)
    from dataclasses import replace

    value = replace(value, settings=value.settings.model_copy(update=changes))
    with pytest.raises(BackendError) as error:
        asyncio.run(dt.DrawThingsBackend().generate(value))
    assert not error.value.execution_uncertain
    assert not wire.requests


def test_echo_without_catalog_checks_known_model_file(tmp_path, wire):
    wire.files = []
    connection = asyncio.run(dt.DrawThingsBackend().test_connection(ImageSettings()))
    assert [m.file for m in connection.models] == [MODEL]
    assert wire.file_checks == [[MODEL]]
    asyncio.run(dt.DrawThingsBackend().generate(request(tmp_path)))
    assert len(wire.requests) == 1


def test_missing_local_model_is_rejected_without_remote_execution(tmp_path, wire):
    wire.files, wire.installed = [], False
    started = []

    async def dispatch():
        started.append(True)

    with pytest.raises(BackendError) as error:
        asyncio.run(
            dt.DrawThingsBackend().generate(request(tmp_path), on_dispatch=dispatch)
        )
    assert error.value.code == "IMAGE_MODEL_MISSING"
    assert not started and not wire.requests and not error.value.execution_uncertain


def test_fixed_deadline_interrupts_stream_without_publishing(tmp_path, wire):
    from dataclasses import replace

    async def hanging(self, request, **kwargs):
        await asyncio.sleep(1)
        yield proto.ImageGenerationResponse()

    original = dt.ImageGenerationServiceStub.generate_image
    dt.ImageGenerationServiceStub.generate_image = hanging
    value = replace(request(tmp_path), deadline=utc_now() + timedelta(milliseconds=30))
    try:
        with pytest.raises(BackendError) as error:
            asyncio.run(dt.DrawThingsBackend().generate(value))
        assert error.value.code == "DRAWTHINGS_TIMEOUT"
        assert error.value.execution_uncertain
        assert not value.output_path.exists()
    finally:
        dt.ImageGenerationServiceStub.generate_image = original


def test_connection_refusal_before_dispatch_is_safe(tmp_path, wire, monkeypatch):
    async def refused(*args, **kwargs):
        raise ConnectionRefusedError("private endpoint")

    monkeypatch.setattr(dt.ImageGenerationServiceStub, "echo", refused)
    with pytest.raises(BackendError) as error:
        asyncio.run(dt.DrawThingsBackend().generate(request(tmp_path)))
    assert not error.value.execution_uncertain and not wire.requests
