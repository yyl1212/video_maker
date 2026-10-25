"""分镜及显式采用；版本检查、引用替换、重排均在同一写事务内。"""

from pathlib import Path

from sqlalchemy import delete, select

from app.db import Database
from app.errors import AppError
from app.models import (
    Episode,
    ModelSettings,
    Project,
    Shot,
    ShotReferenceAsset,
    ShotSelection,
    Task,
)
from app.models.base import utc_now
from app.prompts.compiler import actual_video_frames, compile_image, compile_video
from app.schemas.clients import ImageSettings, VideoSettings
from app.schemas.shots import (
    ReferenceRead,
    SelectionRead,
    ShotCreate,
    ShotListRead,
    ShotPatch,
    ShotRead,
)
from app.services.assets import get_asset
from app.services.projects import check_version, check_writable, get_record
from app.services.storage import StorageService


class ShotService:
    def __init__(self, database: Database, storage: StorageService):
        self.database, self.storage = database, storage

    @staticmethod
    def episode_context(session, episode_id: int, write: bool = False):
        episode = get_record(session, Episode, episode_id)
        project = get_record(session, Project, episode.project_id)
        if write:
            check_writable(project)
            check_writable(episode)
        return episode, project

    def shot_context(self, session, shot_id: int, write: bool = False):
        shot = session.get(Shot, shot_id)
        if shot is None or shot.deleted_at:
            raise AppError(404, "SHOT_NOT_FOUND", "分镜不存在或已删除")
        episode, project = self.episode_context(session, shot.episode_id, write)
        return shot, episode, project

    @staticmethod
    def active_shots(session, episode_id):
        return list(
            session.scalars(
                select(Shot)
                .where(Shot.episode_id == episode_id, Shot.deleted_at.is_(None))
                .order_by(Shot.order_index)
            )
        )

    @staticmethod
    def validate_characters(project, character_ids):
        known = {c["id"] for c in project.characters}
        if any(identifier not in known for identifier in character_ids):
            raise AppError(422, "UNKNOWN_CHARACTER", "所选角色不在当前项目，请重新选择")

    def asset_file(self, asset):
        path = Path(asset.file_path)
        self.storage.checked_path(path, self.storage.media)
        return self.storage.checked_file(path, asset.sha256)

    def read(self, session, shot: Shot, episode: Episode, project: Project) -> ShotRead:
        references = list(
            session.scalars(
                select(ShotReferenceAsset)
                .where(ShotReferenceAsset.shot_id == shot.id)
                .order_by(ShotReferenceAsset.position)
            )
        )
        input_errors = []
        characters = {c["id"]: c for c in project.characters}
        if any(c not in characters for c in shot.character_ids):
            input_errors.append("所选角色已不存在，请重新配置")
        refs = []
        for ref in references:
            try:
                asset = get_asset(session, ref.asset_id, project.id)
                if asset.type != "image":
                    raise AppError(422, "REFERENCE_TYPE", "参考素材必须为图片")
                self.asset_file(asset)
                refs.append({"sha256": asset.sha256, "role": ref.role})
            except AppError:
                input_errors.append(f"参考图片 #{ref.asset_id} 不可用，请重新绑定")
        settings = session.get(ModelSettings, 1)
        image_settings = ImageSettings.model_validate(
            {**settings.image_settings, **shot.image_settings}
        ).model_dump(exclude={"host", "port", "tls", "compression"})
        video_settings = VideoSettings.model_validate(
            {
                **settings.video_settings,
                **{
                    k: v
                    for k, v in shot.video_settings.items()
                    if k in ("preset", "seed")
                },
            }
        ).model_dump(exclude={"base_url"})
        video_settings.update(
            width=shot.video_settings.get("width", 384),
            height=shot.video_settings.get("height", 672),
        )
        video_settings.update(
            steps=4 if video_settings["preset"] == "fast" else 20,
            turbo=video_settings["preset"] == "fast",
        )
        snapshot = {
            "style_anchor": project.style_anchor,
            "characters": [
                characters[c] for c in shot.character_ids if c in characters
            ],
            "image_prompt": shot.image_prompt,
            "references": refs,
            "image_settings": image_settings,
            "video_settings": video_settings,
            "video_prompt": shot.video_prompt,
            "audio_prompt": shot.audio_prompt,
            "dialogue": shot.dialogue,
            "duration_seconds": shot.duration_seconds,
        }
        image = compile_image(snapshot)
        selection = session.get(ShotSelection, shot.id)
        first_frame_issue, video_issue = None, None
        frame = None
        if selection.first_frame_asset_id is not None:
            try:
                frame = get_asset(session, selection.first_frame_asset_id, project.id)
                self.asset_file(frame)
                if frame.type != "image":
                    raise AppError(422, "ASSET_TYPE", "首帧须为图片")
                if input_errors or selection.first_frame_input_hash != image.input_hash:
                    first_frame_issue = (
                        "首帧对应旧版本输入，请重新生成或明确采用适用的上传素材"
                    )
            except AppError:
                first_frame_issue = "首帧文件不可用，请重新导入或生成"
        snapshot["first_frame_sha256"] = frame.sha256 if frame else None
        video = compile_video(snapshot)
        if selection.selected_video_asset_id is not None:
            try:
                clip = get_asset(session, selection.selected_video_asset_id, project.id)
                self.asset_file(clip)
                if clip.type != "video":
                    raise AppError(422, "ASSET_TYPE", "片段须为视频")
                if (
                    not frame
                    or first_frame_issue
                    or selection.video_input_hash != video.input_hash
                ):
                    video_issue = "视频对应旧版本输入或首帧，请重新生成或重新核对采用"
            except AppError:
                video_issue = "视频文件不可用，请重新导入或生成"
        errors = [*input_errors]
        if not shot.raw_description.strip():
            errors.append("请填写画面描述")
        if not shot.image_prompt.strip():
            errors.append("请填写生图Prompt")
        return ShotRead(
            **{
                column.name: getattr(shot, column.name)
                for column in Shot.__table__.columns
                if column.name != "status"
            },
            status="READY" if not errors else "DRAFT",
            actual_duration_seconds=actual_video_frames(shot.duration_seconds) / 24,
            references=[ReferenceRead.model_validate(ref) for ref in references],
            reference_asset_ids=[ref.asset_id for ref in references],
            selection=SelectionRead(
                version=selection.version,
                first_frame_asset_id=selection.first_frame_asset_id,
                selected_video_asset_id=selection.selected_video_asset_id,
                first_frame_stale=bool(first_frame_issue),
                video_stale=bool(video_issue),
                first_frame_issue=first_frame_issue,
                video_issue=video_issue,
            ),
            image_input_hash=image.input_hash,
            video_input_hash=video.input_hash if frame else None,
            video_ready=bool(
                shot.video_prompt.strip() and frame and not first_frame_issue
            ),
            configuration_errors=errors,
            compiled_image_prompt=image.prompt,
            compiled_video_prompt=video.prompt,
            read_only=bool(project.archived_at or episode.archived_at),
        )

    def list_shots(self, episode_id: int) -> ShotListRead:
        with self.database.session_scope() as session:
            episode, project = self.episode_context(session, episode_id)
            return ShotListRead(
                episode_version=episode.version,
                shots=[
                    self.read(session, s, episode, project)
                    for s in self.active_shots(session, episode_id)
                ],
            )

    def get_shot(self, shot_id: int) -> ShotRead:
        with self.database.session_scope() as session:
            return self.read(session, *self.shot_context(session, shot_id))

    def create(self, episode_id: int, data: ShotCreate) -> ShotRead:
        with self.database.session_scope(write=True) as session:
            episode, project = self.episode_context(session, episode_id, True)
            shots = self.active_shots(session, episode_id)
            if len(shots) >= 24:
                raise AppError(409, "SHOT_LIMIT", "单集最多24个分镜")
            self.validate_characters(project, data.character_ids)
            record = Shot(
                episode_id=episode_id,
                order_index=len(shots) + 1,
                **data.model_dump(exclude_none=True),
            )
            session.add(record)
            session.flush()
            session.add(ShotSelection(shot_id=record.id))
            episode.version += 1
            session.flush()
            result = self.read(session, record, episode, project)
            record.status = result.status
            return result

    def update(self, shot_id: int, data: ShotPatch) -> ShotRead:
        with self.database.session_scope(write=True) as session:
            shot, episode, project = self.shot_context(session, shot_id, True)
            check_version(shot, data.expected_version)
            changes = data.model_dump(exclude_unset=True, exclude={"expected_version"})
            self.validate_characters(
                project, changes.get("character_ids", shot.character_ids)
            )
            for key, value in changes.items():
                setattr(shot, key, value)
            shot.version += 1
            session.flush()
            result = self.read(session, shot, episode, project)
            shot.status = result.status
            return result

    @staticmethod
    def apply_order(session, shots):
        # 先移到不冲突的正序号，避免SQLite逐行更新导致中间态唯一键冲突。
        offset = max((s.order_index for s in shots), default=0) + len(shots) + 1
        for index, shot in enumerate(shots):
            shot.order_index = offset + index
        session.flush()
        for index, shot in enumerate(shots, 1):
            shot.order_index = index
            shot.version += 1
        session.flush()

    def reorder(self, episode_id: int, shot_ids: list[int], expected_version: int):
        with self.database.session_scope(write=True) as session:
            episode, project = self.episode_context(session, episode_id, True)
            check_version(episode, expected_version)
            shots = {s.id: s for s in self.active_shots(session, episode_id)}
            if len(set(shot_ids)) != len(shot_ids) or set(shot_ids) != set(shots):
                raise AppError(422, "INVALID_ORDER", "请提交当前剧集全部活动分镜的顺序")
            ordered = [shots[identifier] for identifier in shot_ids]
            self.apply_order(session, ordered)
            episode.version += 1
            return [self.read(session, shot, episode, project) for shot in ordered]

    def delete(self, shot_id: int, expected_version: int) -> ShotRead:
        with self.database.session_scope(write=True) as session:
            shot, episode, project = self.shot_context(session, shot_id, True)
            check_version(shot, expected_version)
            shot.deleted_at = utc_now()
            shot.version += 1
            session.flush()
            self.apply_order(session, self.active_shots(session, episode.id))
            episode.version += 1
            return self.read(session, shot, episode, project)

    def replace_references(self, shot_id, refs, expected_version) -> ShotRead:
        with self.database.session_scope(write=True) as session:
            shot, episode, project = self.shot_context(session, shot_id, True)
            check_version(shot, expected_version)
            if len(refs) > 8 or len({r.asset_id for r in refs}) != len(refs):
                raise AppError(422, "INVALID_REFERENCES", "最多绑定8张不重复的参考图片")
            for ref in refs:
                asset = get_asset(session, ref.asset_id, project.id)
                if asset.type != "image":
                    raise AppError(422, "REFERENCE_TYPE", "参考素材必须为图片")
                self.asset_file(asset)
            session.execute(
                delete(ShotReferenceAsset).where(ShotReferenceAsset.shot_id == shot_id)
            )
            session.flush()
            session.add_all(
                [
                    ShotReferenceAsset(
                        shot_id=shot_id,
                        asset_id=ref.asset_id,
                        position=i,
                        role=ref.role,
                    )
                    for i, ref in enumerate(refs)
                ]
            )
            shot.version += 1
            session.flush()
            result = self.read(session, shot, episode, project)
            shot.status = result.status
            return result

    def select_assets(
        self, shot_id, selection, shot_version, selection_version
    ) -> ShotRead:
        with self.database.session_scope(write=True) as session:
            shot, episode, project = self.shot_context(session, shot_id, True)
            current = session.get(ShotSelection, shot_id)
            check_version(shot, shot_version)
            check_version(current, selection_version)
            # 先采用首帧，再以同一事务内的新首帧计算视频指纹。
            for field, kind in (
                ("first_frame_asset_id", "image"),
                ("selected_video_asset_id", "video"),
            ):
                if field not in selection.model_fields_set:
                    continue
                identifier = getattr(selection, field)
                hash_field = (
                    "first_frame_input_hash" if kind == "image" else "video_input_hash"
                )
                if identifier is None:
                    setattr(current, field, None)
                    setattr(current, hash_field, None)
                    continue
                state = self.read(session, shot, episode, project)
                if kind == "image" and state.status != "READY":
                    raise AppError(422, "SHOT_NOT_READY", "请先完善生图配置再采用首帧")
                if kind == "video" and not state.video_ready:
                    raise AppError(
                        422, "FIRST_FRAME_REQUIRED", "请先配置视频Prompt并采用有效首帧"
                    )
                fingerprint = (
                    state.image_input_hash
                    if kind == "image"
                    else state.video_input_hash
                )
                asset = get_asset(session, identifier, project.id)
                if asset.type != kind:
                    raise AppError(422, "ASSET_TYPE", "所选素材类型不符合用途")
                self.asset_file(asset)
                if asset.source_task_id is None:
                    if not selection.confirm_uploaded:
                        raise AppError(
                            422,
                            "CONFIRM_UPLOAD",
                            "请明确确认上传素材适用于当前镜头输入",
                        )
                else:
                    task = session.get(Task, asset.source_task_id)
                    if (
                        asset.shot_id != shot_id
                        or task is None
                        or task.shot_id != shot_id
                        or task.project_id != project.id
                        or task.kind != kind.upper()
                        or task.status != "DONE"
                        or task.output_asset_id != asset.id
                    ):
                        raise AppError(
                            422, "ASSET_SOURCE", "生成素材须来自本镜头的成功任务"
                        )
                    if task.input_hash != fingerprint:
                        raise AppError(
                            409, "STALE_ASSET", "该生成版本对应旧输入，不能采用"
                        )
                setattr(current, field, identifier)
                setattr(current, hash_field, fingerprint)
                session.flush()
            current.version += 1
            session.flush()
            return self.read(session, shot, episode, project)
