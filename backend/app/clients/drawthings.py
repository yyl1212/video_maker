"""Draw Things 0.4.0 协议适配，区分流已结束与远端状态未知。"""

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from drawthings_py import Configs, RequestBuilder
from drawthings_py.configs.config_prop import load_props
from drawthings_py.generated.dt_grpc.image_service import (
    EchoRequest,
    FileListRequest,
    ImageGenerationServiceStub,
)
from drawthings_py.grpc.grpc_service import ssl_context
from drawthings_py.image.image_buffer import ImageBuffer
from drawthings_py.request_builder import build_grpc_message
from grpclib.client import Channel
from pydantic import BaseModel

from app.errors import BackendError
from app.image_profile import IMAGE_PROFILE, SUPPORTED_MODEL
from app.models.base import utc_now
from app.schemas.clients import ImageSettings


@dataclass(frozen=True)
class ImageRequest:
    prompt: str
    negative_prompt: str
    references: tuple[Path, ...]
    settings: ImageSettings
    deadline: datetime
    output_path: Path


@dataclass(frozen=True)
class ImageResult:
    path: Path
    width: int
    height: int
    metadata: dict


class ImageModelRead(BaseModel):
    file: str
    name: str
    supported: bool


class ImageConnectionRead(BaseModel):
    models: list[ImageModelRead]
    message: str = "已连接本机 Draw Things；未执行生成"


def validate_settings(settings: ImageSettings):
    if settings.model != SUPPORTED_MODEL:
        raise BackendError(
            "IMAGE_MODEL_UNSUPPORTED",
            f"当前仅支持已验证模型 {SUPPORTED_MODEL}，请在本机安装并选择",
            False,
        )
    if settings.width % 64 or settings.height % 64:
        raise BackendError(
            "IMAGE_DIMENSIONS", "生图宽高须为64的倍数，请保存分镜参数后重试", False
        )


def effective_config(settings: ImageSettings):
    return {
        **IMAGE_PROFILE,
        "model": settings.model,
        "width": settings.width,
        "height": settings.height,
        "steps": settings.steps,
        "guidanceScale": settings.guidance_scale,
        "seed": settings.seed,
    }


def build_request(request: ImageRequest):
    effective = effective_config(request.settings)
    # 不使用SDK from_json：它会丢掉显式false和0。
    converted = {
        prop.name: value
        for prop in load_props().values()
        if (value := prop.from_json(effective)) is not None
    }
    builder = RequestBuilder(
        Configs.create(converted), request.prompt, request.negative_prompt
    )
    for reference in request.references:
        builder.add_moodboard_image(str(reference))
    message, _ = build_grpc_message(builder)
    # SDK便捷构建把seed=0当作随机；直接用已冻结的种子序列化。
    message.configuration = builder.config.to_fbs(request.settings.seed)
    message.user = "video-maker-local"
    return message


async def noop(*args):
    pass


class DrawThingsBackend:
    async def test_connection(self, settings: ImageSettings) -> ImageConnectionRead:
        channel = Channel(
            settings.host, settings.port, ssl=ssl_context if settings.tls else False
        )
        try:
            async with asyncio.timeout(8):
                reply = await ImageGenerationServiceStub(channel).echo(
                    EchoRequest(name="video-maker-local")
                )
                return await self.discover_models(
                    ImageGenerationServiceStub(channel), reply
                )
        except BackendError:
            raise
        except Exception as exc:
            raise BackendError(
                "DRAWTHINGS_UNAVAILABLE",
                "无法连接本机 Draw Things，请核对服务和TLS设置",
                False,
            ) from exc
        finally:
            channel.close()

    @staticmethod
    async def discover_models(service, reply):
        if reply.shared_secret_missing:
            raise BackendError(
                "DRAWTHINGS_AUTH_REQUIRED",
                "Draw Things要求共享密钥；当前接入暂不支持",
                False,
            )
        # 只返回服务端实际列出的本地文件，不以模型目录或云预设替代安装状态。
        files = list(reply.files)
        if SUPPORTED_MODEL not in files:
            # 部分本机版本Echo仅返回HELLO；文件存在性RPC才是安装证据。
            result = await service.files_exist(FileListRequest(files=[SUPPORTED_MODEL]))
            if result.files == [SUPPORTED_MODEL] and result.existences == [True]:
                files.append(SUPPORTED_MODEL)
        return ImageConnectionRead(
            models=[
                ImageModelRead(file=f, name=f, supported=f == SUPPORTED_MODEL)
                for f in files
                if f.endswith(".ckpt")
            ]
        )

    async def generate(
        self,
        request: ImageRequest,
        on_progress: Callable[[str, float | None], Awaitable[None]] = noop,
        *,
        on_dispatch=noop,
        on_terminal=noop,
        io=asyncio.to_thread,
    ) -> ImageResult:
        validate_settings(request.settings)
        if len(request.references) > 8:
            raise BackendError("IMAGE_REFERENCE_LIMIT", "参考图最多8张", False)
        dispatched, terminal, channel = False, False, None
        try:
            remaining = (request.deadline - utc_now()).total_seconds()
            async with asyncio.timeout(max(0, remaining)):
                # 编码与磁盘工作通过runner追踪；取消不能遗留未结束写线程。
                message = await io(build_request, request)
                channel = Channel(
                    request.settings.host,
                    request.settings.port,
                    ssl=ssl_context if request.settings.tls else False,
                )
                service = ImageGenerationServiceStub(channel)
                async with asyncio.timeout(min(8, max(0, remaining))):
                    reply = await service.echo(EchoRequest(name="video-maker-local"))
                installed = await self.discover_models(service, reply)
                if not any(m.file == request.settings.model for m in installed.models):
                    raise BackendError(
                        "IMAGE_MODEL_MISSING",
                        "本机Draw Things未安装所选模型，请先安装",
                        False,
                    )
                await on_dispatch()
                dispatched = True
                tensors = []
                await on_progress("Draw Things生成中", None)
                async for response in service.generate_image(message):
                    tensors.extend(response.generated_images)
                    # 只显示真实采样步数；阶段占比不是模型百分比。
                    signpost = response.current_signpost
                    if signpost and signpost.is_set("sampling"):
                        step = signpost.sampling.step
                        await on_progress(
                            f"Draw Things采样 {step}/{request.settings.steps}", None
                        )
                terminal = True
                await on_terminal()
                if len(tensors) != 1:
                    raise BackendError(
                        "IMAGE_EMPTY_RESULT",
                        "Draw Things未返回唯一图片，请检查模型和生成配置",
                        False,
                    )
                return await io(self.save_result, request, tensors[0])
        except BackendError:
            raise
        except asyncio.CancelledError:
            raise
        except TimeoutError as exc:
            raise BackendError(
                "DRAWTHINGS_TIMEOUT",
                "Draw Things请求超时，请检查本机服务",
                dispatched and not terminal,
            ) from exc
        except OSError as exc:
            raise BackendError(
                "IMAGE_FILE_WRITE_FAILED" if terminal else "DRAWTHINGS_UNAVAILABLE",
                "图片读写失败，请检查本地目录"
                if terminal
                else "Draw Things连接中断，请检查本机服务",
                dispatched and not terminal,
            ) from exc
        except Exception as exc:
            raise BackendError(
                "IMAGE_INVALID_RESULT" if terminal else "DRAWTHINGS_FAILED",
                "Draw Things返回图片无效"
                if terminal
                else "Draw Things调用未完成，请检查本机服务",
                dispatched and not terminal,
            ) from exc
        finally:
            if channel:
                channel.close()

    @staticmethod
    def save_result(request: ImageRequest, tensor: bytes):
        image = ImageBuffer.from_tensor(tensor)
        if (image.width, image.height) != (
            request.settings.width,
            request.settings.height,
        ):
            raise BackendError(
                "IMAGE_DIMENSIONS_MISMATCH",
                "返回图片尺寸与请求不一致，未发布为资产",
                False,
            )
        # 只写任务独有临时路径，不允许覆写任何已有文件。
        with request.output_path.open("xb"):
            pass
        image.to_file(str(request.output_path))
        from app.services.assets import decode_image

        with decode_image(request.output_path) as checked:
            if checked.size != (image.width, image.height):
                raise ValueError("invalid image")
        return ImageResult(
            request.output_path,
            image.width,
            image.height,
            {"actual_config": effective_config(request.settings)},
        )
