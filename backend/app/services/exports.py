"""时间线预览、签名确认、冻结导出及受控来源清单下载。"""

import base64
import hashlib
import hmac
import json
import logging
import os
import secrets
import shutil
from dataclasses import asdict
from pathlib import Path
from uuid import uuid4

from sqlalchemy import select

from app.errors import AppError
from app.media.ffmpeg import FFmpegService
from app.models import Asset, ShotSelection, Task
from app.models.base import utc_now
from app.prompts.compiler import content_hash
from app.schemas.assets import AssetRead
from app.schemas.exports import (
    ExportClipInput,
    ExportClipRead,
    ExportPreviewRead,
    ExportProfile,
    export_profile,
    quantized_frames,
)
from app.services.assets import get_asset
from app.services.shots import ShotService
from app.services.tasks import (
    InputAsset,
    PreparedAsset,
    TaskOutcome,
    TaskSnapshot,
    thaw,
)

logger = logging.getLogger(__name__)


class ExportHandler:
    def __init__(self, database, storage):
        self.database, self.storage = database, storage
        self._key = secrets.token_bytes(32)

    def file_facts(self, session, identifiers):
        facts = []
        for identifier in identifiers:
            asset = session.get(Asset, identifier) if identifier else None
            if asset is None:
                facts.append({"id": identifier, "missing": True})
                continue
            fact = {
                "id": identifier,
                "sha256": asset.sha256,
                "deleted": bool(asset.deleted_at),
                "type": asset.type,
                "project_id": asset.project_id,
            }
            try:
                path = self.storage.checked_path(
                    Path(asset.file_path), self.storage.media
                )
                stat = path.stat()
                fact.update(size=stat.st_size, mtime_ns=stat.st_mtime_ns)
            except (OSError, AppError):
                fact["unavailable"] = True
            facts.append(fact)
        return facts

    def row(self, session, shot, episode, project, *, verify_files):
        shots = ShotService(self.database, self.storage)
        dto = shots.read(session, shot, episode, project, verify_files=verify_files)
        selection = session.get(ShotSelection, shot.id)
        asset = None
        available = False
        issues = []
        duration = None
        frames = None
        try:
            if selection.selected_video_asset_id is None:
                raise AppError(422, "MISSING_VIDEO", "尚未选择视频片段")
            asset = get_asset(session, selection.selected_video_asset_id, project.id)
            if asset.type != "video":
                raise AppError(422, "VIDEO_TYPE", "所选素材不是视频")
            shots.asset_file(asset, verify_hash=verify_files)
            duration = asset.duration_seconds
            frames = quantized_frames(duration)
            available = True
        except AppError:
            issues.append("尚未选择可用视频，或片段文件已失效")
        stale = bool(dto.selection.video_stale)
        if stale:
            issues.append(dto.selection.video_issue or "片段对应旧版本输入")
        stamp = content_hash(
            {
                "shot_id": shot.id,
                "shot_version": shot.version,
                "selection_version": selection.version,
                "selected_asset_id": selection.selected_video_asset_id,
                "first_frame_asset_id": selection.first_frame_asset_id,
                "image_input_hash": dto.image_input_hash,
                "video_input_hash": dto.video_input_hash,
                "selected_video_input_hash": selection.video_input_hash,
                "file_facts": self.file_facts(
                    session,
                    [
                        selection.selected_video_asset_id,
                        selection.first_frame_asset_id,
                        *dto.reference_asset_ids,
                    ],
                ),
            }
        )
        return (
            ExportClipRead(
                shot_id=shot.id,
                order_index=shot.order_index,
                description=shot.raw_description,
                shot_version=shot.version,
                selection_version=selection.version,
                asset=AssetRead.model_validate(asset)
                if asset is not None and asset.project_id == project.id
                else None,
                available=available,
                stale=stale,
                issues=issues,
                source_duration=duration,
                frame_count=frames,
                normalized_duration=frames / 24 if frames else None,
                review_token="",
            ),
            stamp,
            dto,
        )

    def sign(self, payload):
        raw = json.dumps(
            payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
        ).encode()
        return (
            base64.urlsafe_b64encode(raw).decode().rstrip("=")
            + "."
            + hmac.new(self._key, raw, hashlib.sha256).hexdigest()
        )

    def open_token(self, token):
        try:
            encoded, signature = token.split(".")
            raw = base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4))
            if not hmac.compare_digest(
                signature, hmac.new(self._key, raw, hashlib.sha256).hexdigest()
            ):
                raise ValueError("signature")
            payload = json.loads(raw)
            if (
                not isinstance(payload, dict)
                or type(payload.get("available")) is not bool
                or type(payload.get("stale")) is not bool
            ):
                raise ValueError("payload")
            return payload
        except (ValueError, TypeError, KeyError) as exc:
            raise AppError(
                409,
                "EXPORT_TIMELINE_CHANGED",
                "来源预览已变化或会话已重启，请显式刷新并重新核对",
            ) from exc

    def preview(self, episode_id):
        with self.database.session_scope() as session:
            episode, project = ShotService.episode_context(session, episode_id)
            rows = []
            for shot in ShotService.active_shots(session, episode_id):
                full, _, _ = self.row(
                    session, shot, episode, project, verify_files=True
                )
                _, stamp, _ = self.row(
                    session, shot, episode, project, verify_files=False
                )
                full.review_token = self.sign(
                    {
                        "stamp": stamp,
                        "available": full.available,
                        "stale": full.stale,
                        "issues": full.issues,
                    }
                )
                rows.append(full)
            return ExportPreviewRead(
                project_id=project.id,
                episode_id=episode.id,
                project_name=project.name,
                episode_title=episode.title,
                project_version=project.version,
                episode_version=episode.version,
                read_only=bool(project.archived_at or episode.archived_at),
                clips=rows,
            )

    def prepare_snapshot(self, session, command):
        profile = export_profile(command.parameters["output_profile"])
        requested = [
            ExportClipInput.model_validate(c) for c in command.parameters["clips"]
        ]
        if not 1 <= len(requested) <= 24 or len({c.shot_id for c in requested}) != len(
            requested
        ):
            raise AppError(422, "EXPORT_NOT_READY", "请勾选1～24个不同镜头")
        episode, project = ShotService.episode_context(
            session, command.episode_id, True
        )
        known = {s.id: s for s in ShotService.active_shots(session, episode.id)}
        clips = []
        inputs = []
        problems = []
        for choice in requested:
            shot = known.get(choice.shot_id)
            if shot is None:
                raise AppError(
                    409,
                    "EXPORT_TIMELINE_CHANGED",
                    "镜头不属于当前剧集或已被删除，请刷新来源",
                )
            row, stamp, dto = self.row(
                session, shot, episode, project, verify_files=False
            )
            reviewed = self.open_token(choice.review_token)
            if reviewed.get("stamp") != stamp:
                raise AppError(
                    409,
                    "EXPORT_TIMELINE_CHANGED",
                    "镜头选择或输入已变化，请显式刷新来源并重新确认",
                )
            if not reviewed["available"] or not row.available:
                problems.append(f"镜头{row.order_index}缺少可用片段")
                continue
            if reviewed["stale"] and not choice.confirm_stale:
                problems.append(f"镜头{row.order_index}须明确确认使用旧片段")
                continue
            asset = row.asset
            clips.append(
                {
                    "shot_id": shot.id,
                    "order_index": shot.order_index,
                    "shot_version": shot.version,
                    "selection_version": row.selection_version,
                    "description": shot.raw_description,
                    "asset_id": asset.id,
                    "sha256": asset.sha256,
                    "name": asset.name,
                    "source_task_id": asset.source_task_id,
                    "source_duration": row.source_duration,
                    "frame_count": row.frame_count,
                    "normalized_duration": row.normalized_duration,
                    "has_audio": asset.has_audio,
                    "audio_sample_rate": asset.audio_sample_rate,
                    "audio_channels": asset.audio_channels,
                    "stale": reviewed["stale"],
                    "stale_reasons": reviewed.get("issues", []),
                    "confirmed_stale": bool(reviewed["stale"] and choice.confirm_stale),
                    "review_stamp": stamp,
                    "image_input_hash": dto.image_input_hash,
                    "video_input_hash": dto.video_input_hash,
                }
            )
            inputs.append(InputAsset(asset.id, asset.file_path, asset.sha256))
        if problems:
            raise AppError(
                422, "EXPORT_NOT_READY", "请检查所选镜头：" + "；".join(problems)
            )
        data = {
            "project_id": project.id,
            "episode_id": episode.id,
            "project_name": project.name,
            "episode_title": episode.title,
            "project_version": project.version,
            "episode_version": episode.version,
            "profile": profile.model_dump(),
            "clips": clips,
        }
        return TaskSnapshot(
            data=data, input_hash=content_hash(data), assets=tuple(inputs)
        )

    async def execute(self, context):
        try:
            return await self.export(context)
        finally:
            await context.service.runner.io(self.cleanup, context.job_dir)

    def cleanup(self, directory):
        for path in [
            *directory.glob("normalized-*.mp4"),
            directory / "final.mp4",
            directory / "thumbnail.png",
        ]:
            try:
                self.storage.checked_path(path, directory).unlink(missing_ok=True)
            except (OSError, AppError):
                logger.warning("导出任务独有临时文件尚未清理")

    async def export(self, context):
        data = thaw(context.snapshot.data)
        profile = ExportProfile.model_validate(data["profile"])
        ffmpeg = FFmpegService()
        normalized = []
        for i, (clip, frozen) in enumerate(
            zip(data["clips"], context.frozen_assets, strict=True)
        ):
            context.ensure_open()
            await context.report(f"统一片段音画规格 {i + 1}/{len(data['clips'])}", None)
            normalized.append(
                await ffmpeg.normalize_clip(
                    Path(frozen.path),
                    context.job_dir / f"normalized-{i:03d}.mp4",
                    profile,
                    context.deadline,
                    source_duration=clip["source_duration"],
                    has_audio=clip["has_audio"],
                    run_process=context.run_process,
                    io=context.service.runner.io,
                )
            )
        context.ensure_open()
        await context.report("拼接时间线并核验成片", None)
        target = context.job_dir / "final.mp4"
        info = await ffmpeg.concat(
            normalized,
            target,
            profile,
            context.deadline,
            run_process=context.run_process,
            io=context.service.runner.io,
        )
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
        await context.report("保存独立成片与来源清单", None)
        prepared = await context.service.runner.io(
            self.publish, context, data, info, target, thumb
        )
        context.ensure_open()
        return TaskOutcome(
            prepared_asset=prepared,
            result_json={"summary": "成片与来源清单已保存，可播放或下载"},
        )

    def publish(self, context, data, info, target, thumb):
        def ensure():
            if context.stop_requested or utc_now() >= context.deadline:
                raise AppError(409, "DEADLINE_EXCEEDED", "导出已停止或超过总截止")

        ensure()
        video_temp, thumb_temp = (
            self.storage.new_temp(".mp4"),
            self.storage.new_temp(".png"),
        )
        manifest_temp = context.job_dir / f".manifest-{uuid4().hex}.tmp"
        try:
            shutil.copyfile(target, video_temp, follow_symlinks=False)
            shutil.copyfile(thumb, thumb_temp, follow_symlinks=False)
            ensure()
            video = self.storage.publish_file(video_temp, data["project_id"])
            thumbnail = self.storage.publish_file(thumb_temp, data["project_id"])
            manifest = {
                **data,
                "task_id": context.task_id,
                "input_hash": context.snapshot.input_hash,
                "created_at": utc_now().isoformat(),
                "output": {
                    "file_path": str(video.path),
                    "sha256": video.sha256,
                    "size_bytes": video.size_bytes,
                    **asdict(info),
                },
                "clips": [
                    {**clip, "position": i, "frozen_path": str(frozen.path)}
                    for i, (clip, frozen) in enumerate(
                        zip(data["clips"], context.frozen_assets, strict=True)
                    )
                ],
            }
            manifest_path = self.storage.checked_path(
                context.job_dir / "manifest.json", context.job_dir
            )
            with manifest_temp.open("x", encoding="utf-8") as output:
                json.dump(
                    manifest, output, ensure_ascii=False, sort_keys=True, indent=2
                )
                output.flush()
                os.fsync(output.fileno())
            ensure()
            os.link(manifest_temp, manifest_path, follow_symlinks=False)
            manifest_path.chmod(0o444)
            return PreparedAsset(
                type="video",
                file_path=video.path,
                thumbnail_path=thumbnail.path,
                sha256=video.sha256,
                size_bytes=video.size_bytes,
                mime_type="video/mp4",
                name=f"成片 · {data['episode_title']} · 任务{context.task_id}"[:200],
                tags=["final_video"],
                **asdict(info),
                metadata_json={
                    "origin": "export",
                    "input_hash": context.snapshot.input_hash,
                    "profile": data["profile"],
                    "manifest_path": str(manifest_path),
                    "manifest_sha256": self.storage.digest(manifest_path),
                    "thumbnail_sha256": thumbnail.sha256,
                    "clip_count": len(data["clips"]),
                    "episode_id": data["episode_id"],
                },
            )
        except OSError as exc:
            raise AppError(
                503, "EXPORT_FILE_FAILED", "成片或来源清单保存失败，请检查本地目录"
            ) from exc
        finally:
            video_temp.unlink(missing_ok=True)
            thumb_temp.unlink(missing_ok=True)
            manifest_temp.unlink(missing_ok=True)

    def history(self, episode_id):
        with self.database.session_scope() as session:
            episode, project = ShotService.episode_context(session, episode_id)
            query = (
                select(Asset)
                .join(Task, Task.id == Asset.source_task_id)
                .where(
                    Task.kind == "EXPORT",
                    Task.status == "DONE",
                    Task.output_asset_id == Asset.id,
                    Task.episode_id == episode.id,
                    Asset.project_id == project.id,
                    Asset.deleted_at.is_(None),
                )
                .order_by(Asset.id.desc())
            )
            return [AssetRead.model_validate(a) for a in session.scalars(query)]

    def manifest(self, asset_id, project_id):
        with self.database.session_scope() as session:
            asset = get_asset(session, asset_id, project_id)
            task = (
                session.get(Task, asset.source_task_id)
                if asset.source_task_id
                else None
            )
            if (
                not task
                or task.kind != "EXPORT"
                or task.status != "DONE"
                or task.output_asset_id != asset.id
                or "final_video" not in asset.tags
            ):
                raise AppError(
                    404, "MANIFEST_NOT_FOUND", "此素材没有可下载的成片来源清单"
                )
            expected = self.storage.root / "jobs" / str(task.id) / "manifest.json"
            sha = asset.metadata_json.get("manifest_sha256")
            if (
                asset.metadata_json.get("manifest_path") != str(expected)
                or not isinstance(sha, str)
                or len(sha) != 64
            ):
                raise AppError(409, "MANIFEST_INVALID", "来源清单记录无效")
            return self.storage.checked_file(
                expected, sha
            ), f"manifest-task-{task.id}.json"
