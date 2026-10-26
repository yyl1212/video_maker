"""冻结文本生成可编辑结果；人工采纳在独立短事务完成。"""

from app.clients.llm import LLMClient, ParseRequest, PromptRequest
from app.errors import AppError
from app.models import ModelSettings, Shot, ShotSelection, Task
from app.models.base import utc_now
from app.prompts.compiler import content_hash
from app.schemas.clients import TextSettings
from app.schemas.episodes import EpisodeRead
from app.schemas.storyboards import DraftRead, PromptDraft, StoryboardDraft
from app.services.projects import check_version
from app.services.shots import ShotService
from app.services.tasks import TaskOutcome, TaskSnapshot, thaw

TEXT_KINDS = ("PARSE_SCRIPT", "OPTIMIZE_PROMPTS")


class StoryboardService:
    def __init__(self, database, storage):
        self.database, self.storage = database, storage
        self.shots = ShotService(database, storage)

    def source(self, session, kind, episode_id, shot_id=None, *, write=False):
        episode, project = self.shots.episode_context(session, episode_id, write)
        project_data = {
            "style_anchor": project.style_anchor,
            "characters": project.characters,
        }
        if kind == "PARSE_SCRIPT":
            value = {
                "project": project_data,
                "episode_version": episode.version,
                "script_text": episode.script_text,
                "existing_shots": [
                    {"id": s.id, "version": s.version}
                    for s in self.shots.active_shots(session, episode.id)
                ],
            }
        else:
            shot, _, _ = self.shots.shot_context(session, shot_id, write)
            if shot.episode_id != episode.id:
                raise AppError(404, "NOT_FOUND", "分镜不属于当前剧集")
            self.shots.validate_characters(project, shot.character_ids)
            selected = {c["id"]: c for c in project.characters}
            project_data["characters"] = [selected[c] for c in shot.character_ids]
            value = {
                "project": project_data,
                "shot_version": shot.version,
                "description": shot.raw_description,
                "dialogue": shot.dialogue,
                "character_ids": shot.character_ids,
                "existing_prompts": {
                    k: getattr(shot, k)
                    for k in ("image_prompt", "video_prompt", "audio_prompt")
                },
            }
        return value, episode, project

    @staticmethod
    def text_task(session, task_id, project_id):
        task = session.get(Task, task_id)
        if task is None or task.project_id != project_id or task.kind not in TEXT_KINDS:
            raise AppError(404, "DRAFT_NOT_FOUND", "当前项目没有此文本草稿")
        if (
            task.status != "DONE"
            or not task.result_json
            or "draft" not in task.result_json
        ):
            raise AppError(409, "DRAFT_NOT_READY", "文本草稿尚未生成成功")
        return task

    def conflict(self, session, task, original):
        try:
            current, _, _ = self.source(
                session, task.kind, task.episode_id, task.shot_id, write=True
            )
        except AppError as exc:
            return exc.message
        if current["project"] != original["project"]:
            return "项目风格或相关角色设定已变化，请保留草稿并重新生成"
        if current != original:
            return "来源剧本、镜头或现有分镜版本已变化，请保留草稿并重新生成"
        return None

    def review(self, task_id, project_id):
        with self.database.session_scope() as session:
            task = self.text_task(session, task_id, project_id)
            source = task.input_snapshot["data"]["source"]
            reason = (
                "此草稿已采纳，不能重复采纳"
                if task.applied_at
                else self.conflict(session, task, source)
            )
            schema = StoryboardDraft if task.kind == "PARSE_SCRIPT" else PromptDraft
            return DraftRead(
                task_id=task.id,
                project_id=task.project_id,
                episode_id=task.episode_id,
                shot_id=task.shot_id,
                kind=task.kind,
                value=schema.model_validate(
                    task.result_json.get("applied_value", task.result_json["draft"])
                ),
                expected_version=source["episode_version"]
                if task.kind == "PARSE_SCRIPT"
                else source["shot_version"],
                characters=source["project"]["characters"],
                existing_shot_ids=[s["id"] for s in source["existing_shots"]]
                if task.kind == "PARSE_SCRIPT"
                else [task.shot_id],
                applied_at=task.applied_at,
                apply_allowed=reason is None,
                conflict_reason=reason,
            )

    def apply(
        self, task_id, edited_value, expected_version, replace_confirm, project_id
    ):
        shot_id = None
        with self.database.session_scope(write=True) as session:
            task = self.text_task(session, task_id, project_id)
            if task.applied_at:
                raise AppError(
                    409, "DRAFT_ALREADY_APPLIED", "草稿已经采纳，不能重复写入"
                )
            original = task.input_snapshot["data"]["source"]
            reason = self.conflict(session, task, original)
            if reason:
                raise AppError(409, "DRAFT_SOURCE_CHANGED", reason)
            current, episode, project = self.source(
                session, task.kind, task.episode_id, task.shot_id, write=True
            )
            if task.kind == "PARSE_SCRIPT":
                if not isinstance(edited_value, StoryboardDraft):
                    raise AppError(422, "DRAFT_TYPE_INVALID", "请提交分镜草稿")
                check_version(episode, expected_version)
                existing = self.shots.active_shots(session, episode.id)
                if existing and not replace_confirm:
                    raise AppError(
                        409,
                        "REPLACE_CONFIRM_REQUIRED",
                        "已有分镜，须明确确认替换；旧镜头与素材将保留",
                    )
                for entry in edited_value.shots:
                    self.shots.validate_characters(project, entry.character_ids)
                # 只软删除旧镜头；选择、引用和生成资产保持历史归属。
                for old in existing:
                    old.deleted_at = utc_now()
                    old.version += 1
                session.flush()
                for entry in edited_value.shots:
                    record = Shot(
                        episode_id=episode.id,
                        **entry.model_dump(),
                        image_prompt="",
                        video_prompt="",
                        status="DRAFT",
                    )
                    session.add(record)
                    session.flush()
                    session.add(ShotSelection(shot_id=record.id))
                episode.version += 1
                session.flush()
                result = EpisodeRead.model_validate(episode)
            else:
                if not isinstance(edited_value, PromptDraft):
                    raise AppError(422, "DRAFT_TYPE_INVALID", "请提交三类Prompt草稿")
                shot, _, _ = self.shots.shot_context(session, task.shot_id, True)
                check_version(shot, expected_version)
                for key, value in edited_value.model_dump().items():
                    setattr(shot, key, value)
                shot.version += 1
                shot.status = "READY"
                shot_id = shot.id
            task.applied_at = utc_now()
            task.result_json = {
                **task.result_json,
                "applied_value": edited_value.model_dump(),
            }
        # 大文件完整核验在写事务之外进行，避免采用文本时持有长SQLite写锁。
        return self.shots.get_shot(shot_id) if shot_id else result


class StoryboardHandler:
    def __init__(self, kind, database, storage, client=None):
        self.kind, self.database, self.storage = kind, database, storage
        self.client = client or LLMClient()
        self.storyboards = StoryboardService(database, storage)

    def prepare_snapshot(self, session, command):
        source, _, _ = self.storyboards.source(
            session, self.kind, command.episode_id, command.shot_id, write=True
        )
        settings = TextSettings.model_validate(
            session.get(ModelSettings, 1).text_settings
        )
        if not settings.model.strip():
            raise AppError(422, "TEXT_MODEL_REQUIRED", "请先配置本机文本模型")
        text = source.get("script_text", source.get("description", ""))
        if not text.strip() or len(text) > (
            12000 if self.kind == "PARSE_SCRIPT" else 4000
        ):
            raise AppError(422, "TEXT_INPUT_INVALID", "请先保存有效剧本或画面描述")
        data = {"source": source, "settings": settings.model_dump()}
        return TaskSnapshot(data=data, input_hash=content_hash(data))

    async def execute(self, context):
        data = thaw(context.snapshot.data)
        source = data["source"]
        settings = TextSettings.model_validate(data["settings"])
        await context.report("本机文本模型正在生成可编辑草稿", None)
        common = {
            "settings": settings,
            "deadline": context.deadline,
            "characters": source["project"]["characters"],
        }
        lifecycle = {
            "on_dispatch": lambda: context.mark_remote_started("llm"),
            "on_terminal": context.mark_remote_finished,
            "io": context.service.runner.io,
        }
        if self.kind == "PARSE_SCRIPT":
            value = await self.client.parse_script(
                ParseRequest(script_text=source["script_text"], **common), **lifecycle
            )
        else:
            value = await self.client.optimize_prompts(
                PromptRequest(
                    description=source["description"],
                    dialogue=source["dialogue"],
                    existing_prompts=source["existing_prompts"],
                    **common,
                ),
                **lifecycle,
            )
        context.ensure_open()
        return TaskOutcome(
            result_json={
                "summary": "文本草稿已生成，请打开审阅后明确采纳",
                "draft": value.model_dump(),
            }
        )
