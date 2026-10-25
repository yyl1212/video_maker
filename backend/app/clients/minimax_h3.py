"""本机H3异步HTTP/SSE；固定截止，有界协议，明确远端终止证据。"""

import asyncio
import base64
import hashlib
import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from urllib.parse import urlsplit

import httpx2 as httpx
from pydantic import BaseModel

from app.errors import BackendError
from app.media.video_profile import (
    RawAVResult,
    VideoProfile,
    validate_audio,
    validate_audio_format,
    video_profile,
)
from app.models.base import utc_now
from app.schemas.clients import VideoSettings

H3_MODEL = "ddalcu/MiniMax-H3-FL2VA-MLX-Serve-8bit"
MAX_EVENT_BYTES = 256 * 1024 * 1024


@dataclass(frozen=True)
class VideoRequest:
    prompt: str
    first_frame_path: Path
    first_frame_sha256: str
    settings: VideoSettings
    profile: VideoProfile
    deadline: datetime
    directory: Path


class VideoConnectionRead(BaseModel):
    model: str
    state: str
    message: str = "已连接本机H3音画模型；未执行生成"


async def noop(*args):
    pass


def strict_json(raw):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ValueError("duplicate key")
            result[key] = value
        return result

    def invalid(value):
        raise ValueError("nonfinite number")

    return json.loads(raw, object_pairs_hook=pairs, parse_constant=invalid)


def endpoint(settings):
    # Pydantic限制回环；localhost也直接连回环，禁用外部DNS/代理/重定向。
    checked = VideoSettings.model_validate(settings.model_dump())
    parts = urlsplit(checked.base_url)
    host = "[::1]" if parts.hostname == "::1" else "127.0.0.1"
    port = f":{parts.port}" if parts.port else ""
    return f"{parts.scheme}://{host}{port}"


class MiniMaxH3Backend:
    def __init__(self, *, transport=None):
        self.transport = transport

    def client(self):
        return httpx.AsyncClient(
            transport=self.transport,
            trust_env=False,
            follow_redirects=False,
            timeout=httpx.Timeout(None, connect=8, pool=8),
        )

    async def models(self, client, settings):
        if settings.model != H3_MODEL:
            raise BackendError(
                "H3_MODEL_UNSUPPORTED", "当前仅支持已验证的MiniMax-H3音画模型", False
            )
        async with client.stream("GET", endpoint(settings) + "/v1/models") as response:
            if response.status_code != 200:
                raise BackendError("H3_UNAVAILABLE", "无法读取本机H3模型列表", False)
            raw = bytearray()
            async for chunk in response.aiter_bytes():
                if len(raw) + len(chunk) > 1024 * 1024:
                    raise BackendError(
                        "H3_MODELS_INVALID", "本机模型列表超过协议限制", False
                    )
                raw.extend(chunk)
        try:
            payload = strict_json(raw)
            model = next(
                m
                for m in payload["data"]
                if isinstance(m, dict)
                and m.get("id") == H3_MODEL
                and "video" in m.get("capabilities", [])
            )
            if model.get("state") not in ("ready", "unloaded"):
                raise ValueError("model not ready")
            return VideoConnectionRead(model=H3_MODEL, state=model["state"])
        except (ValueError, TypeError, KeyError, StopIteration) as exc:
            raise BackendError(
                "H3_MODEL_UNAVAILABLE", "H3未安装或正在加载，请检查本机MLX-Serve", False
            ) from exc

    async def test_connection(self, settings):
        try:
            async with asyncio.timeout(8), self.client() as client:
                return await self.models(client, settings)
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(
                "H3_UNAVAILABLE", "无法连接本机MLX-Serve，请检查地址与服务", False
            ) from exc

    @staticmethod
    def payload(request):
        profile = request.profile
        expected = video_profile(
            profile.width,
            profile.height,
            max(1, profile.actual_duration),
            request.settings.preset,
        )
        if profile != expected or not request.prompt.strip():
            raise BackendError(
                "H3_PROFILE_INVALID", "H3请求参数与已验证规格不符", False
            )
        try:
            data = request.first_frame_path.read_bytes()
            if hashlib.sha256(data).hexdigest() != request.first_frame_sha256:
                raise ValueError("changed frame")
            from app.services.assets import decode_image

            with decode_image(request.first_frame_path):
                pass
        except Exception as exc:
            raise BackendError(
                "H3_FIRST_FRAME_INVALID", "冻结首帧不可用或内容已变化", False
            ) from exc
        return {
            "model": request.settings.model,
            "prompt": request.prompt,
            "width": profile.width,
            "height": profile.height,
            "num_frames": profile.num_frames,
            "fps": 24,
            "steps": profile.steps,
            "turbo": profile.turbo,
            "seed": request.settings.seed,
            "first_frame_image": base64.b64encode(data).decode("ascii"),
            "stream": True,
        }

    async def events(self, response, io):
        buffer = bytearray()
        search_from = 0
        block = []
        event_size = 0
        async for chunk in response.aiter_bytes():
            buffer.extend(chunk)
            while (end := buffer.find(b"\n", search_from)) >= 0:
                line = bytes(buffer[:end]).rstrip(b"\r")
                del buffer[: end + 1]
                event_size += end + 1
                search_from = 0
                if event_size > MAX_EVENT_BYTES:
                    raise BackendError("H3_EVENT_TOO_LARGE", "H3事件超过协议上限", True)
                if line.startswith(b"data:"):
                    block.append(line[5:].lstrip(b" "))
                elif not line:
                    if block:
                        event = await io(strict_json, b"\n".join(block))
                        if not isinstance(event, dict):
                            raise ValueError("event is not object")
                        yield event
                    block = []
                    event_size = 0
            if event_size + len(buffer) > MAX_EVENT_BYTES:
                raise BackendError("H3_EVENT_TOO_LARGE", "H3事件超过协议上限", True)
            # 已扫描过的长行不反复从头搜索，避免大RGB事件的二次方开销。
            search_from = len(buffer)
        raise BackendError(
            "H3_STREAM_INTERRUPTED", "H3流未返回完整终止事件，请检查服务", True
        )

    async def generate(
        self,
        request,
        on_progress=noop,
        *,
        on_dispatch=noop,
        on_terminal=noop,
        io=asyncio.to_thread,
    ):
        dispatched, terminal = False, False
        try:
            async with asyncio.timeout(
                max(0, (request.deadline - utc_now()).total_seconds())
            ):
                payload = await io(self.payload, request)
                async with self.client() as client:
                    async with asyncio.timeout(8):
                        await self.models(client, request.settings)
                    await on_dispatch()
                    dispatched = True
                    async with client.stream(
                        "POST",
                        endpoint(request.settings) + "/v1/video/generations",
                        json=payload,
                        headers={
                            "Accept": "text/event-stream",
                            "Accept-Encoding": "identity",
                        },
                    ) as response:
                        if response.status_code != 200:
                            if response.status_code in (
                                400,
                                401,
                                403,
                                404,
                                409,
                                422,
                                429,
                            ):
                                terminal = True
                                await on_terminal()
                            raise BackendError(
                                "H3_HTTP_ERROR",
                                "H3请求未完成，请检查本机服务、模型和参数",
                                not terminal,
                            )
                        if (
                            response.headers.get("content-type", "")
                            .split(";")[0]
                            .strip()
                            != "text/event-stream"
                        ):
                            raise BackendError(
                                "H3_PROTOCOL_INVALID", "H3未返回预期音画事件流", True
                            )
                        async for event in self.events(response, io):
                            kind = event.get("type")
                            if kind in ("complete", "error"):
                                terminal = True
                                await on_terminal()
                                if kind == "error":
                                    raise BackendError(
                                        "H3_REMOTE_ERROR",
                                        "H3生成失败，请检查本机模型服务",
                                        False,
                                    )
                                return await io(self.decode_result, event, request)
                            if kind != "progress":
                                raise ValueError("unknown event")
                            step, total = event.get("step", 0), event.get("total", 0)
                            if any(
                                type(n) is not int or not 0 <= n <= 1000000
                                for n in (step, total)
                            ) or (total and step > total):
                                raise ValueError("invalid progress")
                            await on_progress(
                                f"H3音画采样 {step}/{total}"
                                if total
                                else "H3正在生成视频与声音",
                                None,
                            )
        except BackendError:
            raise
        except asyncio.CancelledError:
            raise
        except (TimeoutError, httpx.TimeoutException) as exc:
            raise BackendError(
                "H3_TIMEOUT",
                "H3音画生成达到固定截止或请求超时",
                dispatched and not terminal,
            ) from exc
        except OSError as exc:
            raise BackendError(
                "H3_FILE_ERROR" if terminal else "H3_UNAVAILABLE",
                "H3音画临时文件读写失败" if terminal else "本机H3连接中断，请检查服务",
                dispatched and not terminal,
            ) from exc
        except Exception as exc:
            raise BackendError(
                "H3_RESULT_INVALID" if terminal else "H3_PROTOCOL_INVALID",
                "H3返回的音画数据无效" if terminal else "H3事件流无效或连接中断",
                dispatched and not terminal,
            ) from exc

    @staticmethod
    def decode_result(event, request):
        p = request.profile
        if (
            any(type(event.get(k)) is not int for k in ("width", "height", "frames"))
            or (event.get("width"), event.get("height"), event.get("frames"))
            != (p.width, p.height, p.num_frames)
            or type(event.get("fps")) not in (int, float)
            or event["fps"] != 24
            or event.get("format") != "rgb8"
        ):
            raise BackendError("H3_RGB_INVALID", "H3画面规格与请求不一致", False)

        def decode(value, maximum):
            if not isinstance(value, str) or len(value) > 4 * ((maximum + 2) // 3):
                raise ValueError("base64 length")
            result = base64.b64decode(value, validate=True)
            if len(result) > maximum:
                raise ValueError("decoded length")
            return result

        rgb = decode(event.get("data"), p.width * p.height * p.num_frames * 3)
        if len(rgb) != p.width * p.height * p.num_frames * 3:
            raise ValueError("rgb byte count")
        if event.get("audio_format") != "pcm_s16le":
            raise ValueError("audio format")
        rate, channels = event.get("audio_sample_rate"), event.get("audio_channels")
        validate_audio_format(rate, channels)
        pcm = decode(
            event.get("audio_data"), math_ceil_audio(p.actual_duration, rate, channels)
        )
        duration = validate_audio(rate, channels, len(pcm), p.actual_duration)
        rgb_path, pcm_path = (
            request.directory / "frames.rgb",
            request.directory / "audio.pcm",
        )
        with rgb_path.open("xb") as output:
            output.write(rgb)
        with pcm_path.open("xb") as output:
            output.write(pcm)
        return RawAVResult(rgb_path, pcm_path, p, rate, channels, duration)


def math_ceil_audio(duration, rate, channels):
    import math

    return math.ceil((duration + 0.5) * rate) * channels * 2
