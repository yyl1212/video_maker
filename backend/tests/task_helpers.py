import asyncio
import threading
from uuid import uuid4

from app.prompts.compiler import content_hash
from app.schemas.tasks import TaskSubmitCommand
from app.services.tasks import TaskOutcome, TaskSnapshot


class SampleHandler:
    def __init__(self, *, error=None, hold=None):
        self.error = error
        self.hold = hold
        self.started = threading.Event()
        self.calls = []

    def prepare_snapshot(self, session, command):
        data = {"prompt": command.parameters.get("prompt", "原创镜头")}
        return TaskSnapshot(data=data, input_hash=content_hash(data))

    async def execute(self, context):
        self.calls.append(context.task_id)
        self.started.set()
        if self.hold:
            while not self.hold.is_set():
                await asyncio.sleep(0.005)
        if self.error:
            raise self.error
        await context.report("原创测试已完成", 1)
        return TaskOutcome(result_json={"summary": "原创测试结果", "draft": "原创内容"})


def command(
    project_id, episode_id, shot_id, kind="OPTIMIZE_PROMPTS", key=None, **options
):
    return TaskSubmitCommand(
        project_id=project_id,
        episode_id=episode_id,
        shot_id=None if kind in ("PARSE_SCRIPT", "EXPORT") else shot_id,
        kind=kind,
        idempotency_key=key or str(uuid4()),
        expected_project_version=1,
        expected_episode_version=1,
        expected_shot_version=1 if kind not in ("PARSE_SCRIPT", "EXPORT") else None,
        parameters=options,
    )


def records(database):
    from app.models import Episode, Project, Shot, ShotSelection

    with database.session_scope(write=True) as session:
        project = Project(name="原创任务工程")
        session.add(project)
        session.flush()
        episode = Episode(project_id=project.id, title="原创剧集")
        session.add(episode)
        session.flush()
        shot = Shot(episode_id=episode.id, order_index=1)
        session.add(shot)
        session.flush()
        session.add(ShotSelection(shot_id=shot.id))
        return project.id, episode.id, shot.id
