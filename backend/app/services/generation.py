"""首帧处理器：提交时快照，执行时仅使用冻结输入，结果只入历史。"""

from pathlib import Path

from app.clients.drawthings import (
    DrawThingsBackend,
    ImageRequest,
    validate_settings,
)
from app.errors import AppError, BackendError
from app.models import Asset, ModelSettings
from app.models.base import utc_now
from app.schemas.clients import ImageSettings
from app.services.assets import decode_image
from app.services.shots import ShotService
from app.services.tasks import (
    InputAsset,
    PreparedAsset,
    TaskOutcome,
    TaskSnapshot,
    thaw,
)


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
