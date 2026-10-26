"""仅测试使用的推理夹具；真实业务、文件和媒体封装保持生产实现。"""

import array
import asyncio
import math

from PIL import Image, ImageDraw

from app.clients.drawthings import ImageResult
from app.media.video_profile import RawAVResult
from app.models import ModelSettings
from app.schemas.storyboards import PromptDraft, StoryboardDraft


class TextFixture:
    def __init__(self, delay):
        self.delay = delay
        self.requests = []

    async def infer(self, request, kwargs):
        self.requests.append(request)
        await kwargs["on_dispatch"]()
        await asyncio.sleep(self.delay)
        await kwargs["on_terminal"]()

    async def parse_script(self, request, **kwargs):
        await self.infer(request, kwargs)
        return StoryboardDraft.model_validate(
            {
                "shots": [
                    {
                        "order_index": 1,
                        "raw_description": "小木在门前轻轻挥手。",
                        "dialogue": "你好。",
                        "character_ids": [request.characters[0]["id"]]
                        if request.characters
                        else [],
                        "audio_prompt": "Quiet room ambience.",
                        "duration_seconds": 1.0,
                    }
                ]
            }
        )

    async def optimize_prompts(self, request, **kwargs):
        await self.infer(request, kwargs)
        return PromptDraft(
            image_prompt=(
                "An original olive wooden robot with an orange scarf beside a door."
            ),
            video_prompt="The robot gently waves one hand in a continuous static shot.",
            audio_prompt="Quiet room ambience with soft wooden clicks.",
        )


class ImageFixture:
    def __init__(self, delay):
        self.delay = delay
        self.requests = []

    async def generate(self, request, on_progress, *, on_dispatch, on_terminal, io):
        await on_dispatch()
        self.requests.append(request)
        await on_progress("本机测试夹具正在绘制原创首帧", None)
        await asyncio.sleep(self.delay)
        await on_terminal()

        def draw():
            image = Image.new("RGB", (128, 192), "#f0ead8")
            canvas = ImageDraw.Draw(image)
            canvas.rectangle((42, 38, 86, 81), fill="#697f4f")
            canvas.rectangle((36, 85, 92, 141), fill="#697f4f")
            canvas.rectangle((38, 85, 92, 93), fill="#d8894c")
            canvas.ellipse((51, 52, 56, 57), fill="black")
            canvas.ellipse((72, 52, 77, 57), fill="black")
            image.save(request.output_path, "PNG")

        await io(draw)
        return ImageResult(request.output_path, 128, 192, {"fixture": True})


class VideoFixture:
    def __init__(self, delay):
        self.delay = delay
        self.requests = []

    async def generate(self, request, on_progress, *, on_dispatch, on_terminal, io):
        await on_dispatch()
        self.requests.append(request)
        await on_progress("本机测试夹具正在生成原创音画", None)
        await asyncio.sleep(self.delay)
        await on_terminal()
        profile = request.profile
        raw = request.directory / "frames.rgb"
        pcm = request.directory / "audio.pcm"
        rate = 16000
        values = array.array(
            "h",
            (
                round(5000 * math.sin(2 * math.pi * 440 * n / rate))
                for n in range(round(profile.actual_duration * rate))
            ),
        )
        await io(
            raw.write_bytes,
            bytes([70, 100, 55]) * profile.width * profile.height * profile.num_frames,
        )
        await io(pcm.write_bytes, values.tobytes())
        return RawAVResult(raw, pcm, profile, rate, 1, len(values) / rate)


class FixtureModels:
    def __init__(self, *, image_delay=0, video_delay=0, text_delay=0):
        self.text = TextFixture(text_delay)
        self.image = ImageFixture(image_delay)
        self.video = VideoFixture(video_delay)


def install_models(app, models):
    service = app.state.task_service
    for kind in ("PARSE_SCRIPT", "OPTIMIZE_PROMPTS"):
        service.handlers[kind].client = models.text
    service.handlers["IMAGE"].backend = models.image
    service.handlers["VIDEO"].backend = models.video
    with service.database.session_scope(write=True) as session:
        settings = session.get(ModelSettings, 1)
        settings.text_settings = {**settings.text_settings, "model": "fixture-text"}
        settings.image_settings = {
            **settings.image_settings,
            "model": "flux_2_klein_9b_kv_i8x.ckpt",
            "width": 128,
            "height": 192,
            "seed": 0,
        }
        settings.video_settings = {
            **settings.video_settings,
            "preset": "fast",
            "seed": 0,
        }
