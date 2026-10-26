"""首帧与音画处理器：仅使用冻结输入，结果只入历史。"""

import logging
import shutil
from dataclasses import asdict
from pathlib import Path

from app.clients.drawthings import (
    DrawThingsBackend,
    ImageRequest,
    validate_settings,
)
from app.clients.minimax_h3 import H3_MODEL, MiniMaxH3Backend, VideoRequest
from app.errors import AppError, BackendError
from app.media.ffmpeg import FFmpegService
from app.media.video_profile import VIDEO_ENCODING, VideoProfile, video_profile
from app.models import Asset, ModelSettings
from app.models.base import utc_now
from app.schemas.clients import ImageSettings, VideoSettings
from app.services.assets import decode_image
from app.services.shots import ShotService
from app.services.tasks import (
    InputAsset,
    PreparedAsset,
    TaskOutcome,
    TaskSnapshot,
    thaw,
)

logger = logging.getLogger(__name__)


class ImageGenerationHandler:
    def __init__(self, database, storage, backend=None):
        self.database, self.storage = database, storage
        self.backend = backend or DrawThingsBackend()

    def prepare_snapshot(self, session, command):
        shots = ShotService(self.database, self.storage)
        shot, episode, project = shots.shot_context(session, command.shot_id, True)
        dto = shots.read(session, shot, episode, project)
        if dto.status != "READY":
            raise AppError(
                422, "SHOT_NOT_READY", "请先保存画面描述、Prompt和有效参考图"
            )
        if command.parameters.get("image_input_hash") != dto.image_input_hash:
            raise AppError(
                409, "IMAGE_INPUT_CHANGED", "生图输入已更新，请刷新核对后重新生成"
            )
        settings = ImageSettings.model_validate(
            {**session.get(ModelSettings, 1).image_settings, **shot.image_settings}
        )
        try:
            validate_settings(settings)
        except BackendError as exc:
            raise AppError(422, exc.code, exc.message) from exc
        references, inputs = [], []
        for ref in dto.references:
            asset = session.get(Asset, ref.asset_id)
            references.append(
                {
                    "asset_id": asset.id,
                    "sha256": asset.sha256,
                    "role": ref.role,
                    "position": ref.position,
                }
            )
            inputs.append(InputAsset(asset.id, asset.file_path, asset.sha256))
        return TaskSnapshot(
            data={
                "project_id": project.id,
                "prompt": dto.compiled_image_prompt,
                "negative_prompt": settings.negative_prompt,
                "settings": settings.model_dump(),
                "references": references,
            },
            input_hash=dto.image_input_hash,
            assets=tuple(inputs),
        )

    async def execute(self, context):
        snapshot = thaw(context.snapshot.data)
        context.ensure_open()
        await context.report("参考图已冻结，准备本机生成", None)
        request = ImageRequest(
            prompt=snapshot["prompt"],
            negative_prompt=snapshot["negative_prompt"],
            references=tuple(Path(a.path) for a in context.frozen_assets),
            settings=ImageSettings.model_validate(snapshot["settings"]),
            deadline=context.deadline,
            output_path=context.job_dir / "first-frame.png",
        )
        result = await self.backend.generate(
            request,
            context.report,
            on_dispatch=lambda: context.mark_remote_started("drawthings"),
            on_terminal=context.mark_remote_finished,
            io=context.service.runner.io,
        )
        context.ensure_open()
        await context.report("验证图片并保存历史版本", None)
        prepared = await context.service.runner.io(
            self.publish, context, result, snapshot
        )
        context.ensure_open()
        return TaskOutcome(
            prepared_asset=prepared,
            result_json={"summary": "首帧已保存到历史，请在当前媒体版本中明确采用"},
        )

    def publish(self, context, result, snapshot):
        self.ensure_publish_open(context)
        self.storage.checked_path(result.path, context.job_dir)
        source, thumb = self.storage.new_temp(".png"), self.storage.new_temp(".png")
        try:
            # 不信任模型声称的尺寸；再次解码，规范化为PNG并生成缩略图。
            with decode_image(result.path) as image:
                settings = snapshot["settings"]
                if image.size != (settings["width"], settings["height"]):
                    raise AppError(422, "IMAGE_DIMENSIONS_MISMATCH", "生成图片尺寸不符")
                width, height = image.size
                image.save(source, "PNG")
                image.thumbnail((320, 320))
                image.save(thumb, "PNG")
            self.ensure_publish_open(context)
            published = self.storage.publish_file(source, snapshot["project_id"])
            thumbnail = self.storage.publish_file(thumb, snapshot["project_id"])
            return PreparedAsset(
                type="image",
                file_path=published.path,
                thumbnail_path=thumbnail.path,
                sha256=published.sha256,
                size_bytes=published.size_bytes,
                mime_type="image/png",
                name=f"首帧 · 任务{context.task_id}",
                width=width,
                height=height,
                tags=["first_frame"],
                metadata_json={
                    **result.metadata,
                    "origin": "generation",
                    "backend": "drawthings",
                    "input_hash": context.snapshot.input_hash,
                    "actual_prompt": snapshot["prompt"],
                    "negative_prompt": snapshot["negative_prompt"],
                    "settings": snapshot["settings"],
                    "references": [
                        {**ref, "frozen_path": str(frozen.path)}
                        for ref, frozen in zip(
                            snapshot["references"], context.frozen_assets, strict=True
                        )
                    ],
                    "thumbnail_sha256": thumbnail.sha256,
                },
            )
        except OSError as exc:
            raise AppError(
                503, "IMAGE_FILE_WRITE_FAILED", "首帧保存失败，请检查本地目录"
            ) from exc
        finally:
            source.unlink(missing_ok=True)
            thumb.unlink(missing_ok=True)

    @staticmethod
    def ensure_publish_open(context):
        # 工作线程没有事件循环，按同一固定UTC截止与停止标记检查。
        if context.stop_requested or utc_now() >= context.deadline:
            raise AppError(409, "DEADLINE_EXCEEDED", "任务已停止或超过总截止")


class VideoGenerationHandler:
    def __init__(self, database, storage, backend=None):
        self.database, self.storage = database, storage
        self.backend = backend or MiniMaxH3Backend()

    def prepare_snapshot(self, session, command):
        shots = ShotService(self.database, self.storage)
        shot, episode, project = shots.shot_context(session, command.shot_id, True)
        dto = shots.read(session, shot, episode, project)
        if not dto.video_ready:
            raise AppError(
                422, "VIDEO_NOT_READY", "请保存视频Prompt并采用有效且未过期的首帧"
            )
        if command.parameters.get("video_input_hash") != dto.video_input_hash:
            raise AppError(
                409, "VIDEO_INPUT_CHANGED", "视频输入或首帧已变化，请刷新核对后重新生成"
            )
        settings = VideoSettings.model_validate(
            {
                **session.get(ModelSettings, 1).video_settings,
                **{
                    k: v
                    for k, v in shot.video_settings.items()
                    if k in ("seed", "preset")
                },
            }
        )
        if settings.model != H3_MODEL:
            raise AppError(
                422, "H3_MODEL_UNSUPPORTED", "当前仅支持已验证的MiniMax-H3音画模型"
            )
        try:
            profile = video_profile(
                shot.video_settings.get("width", 384),
                shot.video_settings.get("height", 672),
                shot.duration_seconds,
                settings.preset,
            )
        except BackendError as exc:
            raise AppError(422, exc.code, exc.message) from exc
        frame = session.get(Asset, dto.selection.first_frame_asset_id)
        return TaskSnapshot(
            data={
                "project_id": project.id,
                "prompt": dto.compiled_video_prompt,
                "settings": settings.model_dump(),
                "profile": asdict(profile),
                "first_frame": {"asset_id": frame.id, "sha256": frame.sha256},
            },
            input_hash=dto.video_input_hash,
            assets=(InputAsset(frame.id, frame.file_path, frame.sha256),),
        )

    async def execute(self, context):
        try:
            return await self.generate(context)
        finally:
            # 仅清理任务目录固定文件；绝不处理模型返回的任意路径或已登记资产。
            await context.service.runner.io(self.cleanup_temporary, context.job_dir)

    def cleanup_temporary(self, directory):
        for name in ("frames.rgb", "audio.pcm", "clip.mp4", "thumbnail.png"):
            try:
                path = self.storage.checked_path(directory / name, directory)
                path.unlink(missing_ok=True)
            except (OSError, AppError):
                logger.warning("H3任务独有临时文件尚未清理")

    async def generate(self, context):
        snapshot = thaw(context.snapshot.data)
        context.ensure_open()
        frame = context.frozen_assets[0]
        request = VideoRequest(
            prompt=snapshot["prompt"],
            first_frame_path=Path(frame.path),
            first_frame_sha256=frame.sha256,
            settings=VideoSettings.model_validate(snapshot["settings"]),
            profile=VideoProfile(**snapshot["profile"]),
            deadline=context.deadline,
            directory=context.job_dir,
        )
        await context.report("首帧已冻结，连接本机H3音画模型", None)
        raw = await self.backend.generate(
            request,
            context.report,
            on_dispatch=lambda: context.mark_remote_started("mlx"),
            on_terminal=context.mark_remote_finished,
            io=context.service.runner.io,
        )
        for path in (raw.rgb_path, raw.pcm_path):
            self.storage.checked_path(path, context.job_dir)
        context.ensure_open()
        await context.report("封装视频与原生声音", None)
        target = context.job_dir / "clip.mp4"
        info = await FFmpegService().encode_av(
            raw,
            target,
            context.deadline,
            run_process=context.run_process,
            io=context.service.runner.io,
        )
        context.ensure_open()
        await context.report("生成缩略图并保存音画历史", None)
        thumb = context.job_dir / "thumbnail.png"
        await context.run_process(
            [
                "ffmpeg",
                "-v",
                "error",
                "-nostdin",
                "-n",
                "-protocol_whitelist",
                "file",
                "-enable_drefs",
                "0",
                "-f",
                "mov",
                "-i",
                str(target),
                "-map",
                "0:V:0",
                "-frames:v",
                "1",
                "-vf",
                "scale=320:320:force_original_aspect_ratio=decrease",
                "-update",
                "1",
                str(thumb),
            ]
        )
        prepared = await context.service.runner.io(
            self.publish, context, snapshot, raw, info, target, thumb
        )
        context.ensure_open()
        return TaskOutcome(
            prepared_asset=prepared,
            result_json={
                "summary": "有声视频已保存到历史，请播放检查并明确采用",
            },
        )

    def publish(self, context, snapshot, raw, info, target, thumb):
        ImageGenerationHandler.ensure_publish_open(context)
        video_temp, thumb_temp = (
            self.storage.new_temp(".mp4"),
            self.storage.new_temp(".png"),
        )
        try:
            shutil.copyfile(target, video_temp, follow_symlinks=False)
            shutil.copyfile(thumb, thumb_temp, follow_symlinks=False)
            ImageGenerationHandler.ensure_publish_open(context)
            video = self.storage.publish_file(video_temp, snapshot["project_id"])
            thumbnail = self.storage.publish_file(thumb_temp, snapshot["project_id"])
            return PreparedAsset(
                type="video",
                file_path=video.path,
                thumbnail_path=thumbnail.path,
                sha256=video.sha256,
                size_bytes=video.size_bytes,
                mime_type="video/mp4",
                name=f"有声片段 · 任务{context.task_id}",
                tags=["video_clip"],
                **asdict(info),
                metadata_json={
                    "origin": "generation",
                    "backend": "mlx",
                    "model": snapshot["settings"]["model"],
                    "input_hash": context.snapshot.input_hash,
                    "actual_prompt": snapshot["prompt"],
                    "settings": snapshot["settings"],
                    "actual_profile": snapshot["profile"],
                    "first_frame": {
                        **snapshot["first_frame"],
                        "frozen_path": str(context.frozen_assets[0].path),
                    },
                    "source_audio": {
                        "format": "pcm_s16le",
                        "sample_rate": raw.audio_sample_rate,
                        "channels": raw.audio_channels,
                        "duration_seconds": raw.audio_duration,
                    },
                    "encoding": VIDEO_ENCODING,
                    "thumbnail_sha256": thumbnail.sha256,
                },
            )
        except OSError as exc:
            raise AppError(
                503, "VIDEO_FILE_WRITE_FAILED", "有声视频发布失败，请检查本地目录"
            ) from exc
        finally:
            video_temp.unlink(missing_ok=True)
            thumb_temp.unlink(missing_ok=True)
