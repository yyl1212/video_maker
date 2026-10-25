"""提交与发布只持有短事务；同一幂等请求优先返回已有任务。"""

import math
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import MappingProxyType
from typing import TYPE_CHECKING, Protocol
from uuid import uuid4

from fastapi import BackgroundTasks
from pydantic import Field
from sqlalchemy import select

from app.db import Database
from app.errors import AppError
from app.models import Asset, Episode, Project, RuntimeState, Shot, ShotSelection, Task
from app.models.base import utc_now
from app.prompts.compiler import content_hash
from app.schemas.common import InputModel
from app.schemas.tasks import (
    ActiveJobRead,
    RuntimeRead,
    TaskDetail,
    TaskRead,
    TaskSubmitCommand,
)
from app.services.assets import get_asset
from app.services.projects import check_version, check_writable, get_record
from app.services.storage import StorageService

if TYPE_CHECKING:
    from .runner import TaskContext


def freeze(value):
    if isinstance(value, dict):
        return MappingProxyType({key: freeze(item) for key, item in value.items()})
    if isinstance(value, list):
        return tuple(freeze(item) for item in value)
    return value


def thaw(value):
    if isinstance(value, dict | MappingProxyType):
        return {key: thaw(item) for key, item in value.items()}
    if isinstance(value, list | tuple):
        return [thaw(item) for item in value]
    return value


@dataclass(frozen=True)
class InputAsset:
    asset_id: int
    file_path: str
    sha256: str

    @property
    def id(self):
        return self.asset_id


@dataclass(frozen=True)
class TaskSnapshot:
    data: dict
    input_hash: str
    assets: tuple[InputAsset, ...] = ()
    compiler_version: str = "1"

    def __post_init__(self):
        object.__setattr__(self, "data", freeze(thaw(self.data)))
        object.__setattr__(self, "assets", tuple(self.assets))

    def as_json(self):
        return {
            "data": thaw(self.data),
            "assets": [asdict(a) for a in self.assets],
            "compiler_version": self.compiler_version,
        }


class PreparedAsset(InputModel):
    type: str
    file_path: Path
    thumbnail_path: Path | None = None
    sha256: str = Field(pattern="^[0-9a-f]{64}$")
    size_bytes: int = Field(gt=0)
    mime_type: str
    name: str = Field(min_length=1, max_length=200)
    width: int = Field(gt=0)
    height: int = Field(gt=0)
    tags: list[str] = Field(default_factory=list)
    duration_seconds: float | None = None
    has_audio: bool = False
    audio_sample_rate: int | None = None
    audio_channels: int | None = None
    metadata_json: dict = Field(default_factory=dict)


@dataclass(frozen=True)
class TaskOutcome:
    prepared_asset: PreparedAsset | None = None
    result_json: dict | None = None


class TaskHandler(Protocol):
    def prepare_snapshot(self, session, command: TaskSubmitCommand) -> TaskSnapshot: ...
    async def execute(self, context: "TaskContext") -> TaskOutcome: ...


@dataclass
class TaskPolicy:
    heartbeat_seconds: float = 5


@dataclass
class ExecutionGate:
    lock: threading.RLock = field(default_factory=threading.RLock)
    running: set[int] = field(default_factory=set)
    poisoned: bool = False


@dataclass
class TaskSubmission:
    task: TaskRead
    created: bool
    service: "TaskService" = field(repr=False)
    registered: bool = False


def task_read(task, detail=False):
    result = (
        TaskDetail.model_validate(task) if detail else TaskRead.model_validate(task)
    )
    summary = (task.result_json or {}).get("summary")
    result.result_summary = summary[:200] if isinstance(summary, str) else None
    return result


def register_submission(
    background_tasks: BackgroundTasks, submission: TaskSubmission
) -> None:
    if not submission.created or submission.registered:
        return
    try:
        background_tasks.add_task(submission.service.runner.run, submission.task.id)
        submission.registered = True
    except Exception as exc:
        submission.service.fail_pending(
            submission.task.id,
            "BACKGROUND_REGISTRATION_FAILED",
            "任务未能启动，请重新提交",
        )
        raise AppError(
            503, "BACKGROUND_REGISTRATION_FAILED", "任务未能启动，请重新提交"
        ) from exc


class TaskService:
    def __init__(
        self,
        database: Database,
        storage: StorageService,
        handlers: dict[str, TaskHandler] | None = None,
        policy: TaskPolicy | None = None,
    ):
        from .runner import TaskRunner

        self.database, self.storage = database, storage
        self.handlers = dict(handlers or {})
        self.policy = policy or TaskPolicy()
        self.owner = str(uuid4())
        self.gate = ExecutionGate()
        self.runner = TaskRunner(self)

    def submit(self, command: TaskSubmitCommand) -> TaskSubmission:
        request_hash = content_hash(command.model_dump(exclude={"idempotency_key"}))
        with self.gate.lock, self.database.session_scope(write=True) as session:
            existing = session.scalar(
                select(Task).where(
                    Task.project_id == command.project_id,
                    Task.idempotency_key == command.idempotency_key,
                )
            )
            if existing:
                if existing.request_hash != request_hash:
                    raise AppError(
                        409, "IDEMPOTENCY_CONFLICT", "同一请求标识不能用于不同内容"
                    )
                return TaskSubmission(task_read(existing), False, self)
            runtime = session.get(RuntimeState, 1)
            if runtime.state == "NEEDS_RESET" or self.gate.poisoned:
                raise AppError(
                    503, "EXECUTION_STATE_UNKNOWN", "执行状态尚未确认，请先恢复执行状态"
                )
            active = session.scalar(
                select(Task.id).where(Task.status.in_(("PENDING", "RUNNING")))
            )
            if active is not None or runtime.state == "ACTIVE" or self.gate.running:
                raise AppError(
                    429,
                    "TASK_BUSY",
                    "当前有任务执行中",
                    active_job_id=active or runtime.active_task_id,
                )
            project = get_record(session, Project, command.project_id)
            episode = get_record(session, Episode, command.episode_id)
            if episode.project_id != project.id:
                raise AppError(404, "NOT_FOUND", "剧集不属于当前项目")
            check_writable(project)
            check_writable(episode)
            check_version(project, command.expected_project_version)
            check_version(episode, command.expected_episode_version)
            if command.shot_id is not None:
                shot = session.get(Shot, command.shot_id)
                if not shot or shot.deleted_at or shot.episode_id != episode.id:
                    raise AppError(404, "SHOT_NOT_FOUND", "分镜不属于当前剧集")
                check_version(shot, command.expected_shot_version)
                if command.expected_selection_version is not None:
                    check_version(
                        session.get(ShotSelection, shot.id),
                        command.expected_selection_version,
                    )
            handler = self.handlers.get(command.kind)
            if handler is None:
                raise AppError(503, "HANDLER_UNAVAILABLE", "当前制作能力尚未接入")
            snapshot = handler.prepare_snapshot(session, command)
            for item in snapshot.assets:
                asset = get_asset(session, item.asset_id, project.id)
                if asset.file_path != item.file_path or asset.sha256 != item.sha256:
                    raise AppError(409, "ASSET_CHANGED", "任务素材已变化，请重新核对")
            task = Task(
                project_id=project.id,
                episode_id=episode.id,
                shot_id=command.shot_id,
                kind=command.kind,
                status="PENDING",
                idempotency_key=command.idempotency_key,
                request_hash=request_hash,
                input_snapshot=snapshot.as_json(),
                input_hash=snapshot.input_hash,
            )
            session.add(task)
            session.flush()
            runtime.state, runtime.active_task_id = "ACTIVE", task.id
            runtime.reason, runtime.affected_backend = "", None
            runtime.version += 1
            session.flush()
            return TaskSubmission(task_read(task), True, self)

    def get(self, task_id: int) -> TaskDetail:
        with self.database.session_scope() as session:
            task = session.get(Task, task_id)
            if task is None:
                raise AppError(404, "TASK_NOT_FOUND", "任务不存在")
            return task_read(task, True)

    def active(self) -> ActiveJobRead:
        with self.database.session_scope() as session:
            task = session.scalar(
                select(Task).where(Task.status.in_(("PENDING", "RUNNING")))
            )
            return ActiveJobRead(
                task=task_read(task) if task else None,
                runtime_state=RuntimeRead.model_validate(session.get(RuntimeState, 1)),
            )

    def list_project(self, project_id: int, limit: int = 50):
        with self.database.session_scope() as session:
            get_record(session, Project, project_id)
            return [
                task_read(t)
                for t in session.scalars(
                    select(Task)
                    .where(Task.project_id == project_id)
                    .order_by(Task.id.desc())
                    .limit(limit)
                )
            ]

    def claim(self, task_id: int):
        with self.gate.lock:
            if self.gate.running or self.gate.poisoned:
                return None
            with self.database.session_scope(write=True) as session:
                task = session.get(Task, task_id)
                runtime = session.get(RuntimeState, 1)
                if (
                    not task
                    or task.status != "PENDING"
                    or runtime.state != "ACTIVE"
                    or runtime.active_task_id != task_id
                ):
                    return None
                task.status, task.execution_owner = "RUNNING", self.owner
                task.started_at = task.heartbeat_at = utc_now()
                task.stage = "准备任务输入"
                session.flush()
                record = (task.kind, task.input_hash, task.input_snapshot)
            self.gate.running.add(task_id)
            return record

    def running_update(self, task_id, *, stage=None, progress=None, execution=None):
        with self.database.session_scope(write=True) as session:
            task = session.get(Task, task_id)
            if (
                not task
                or task.status != "RUNNING"
                or task.execution_owner != self.owner
            ):
                raise AppError(409, "TASK_STOPPED", "该任务已停止")
            task.heartbeat_at = utc_now()
            if stage is not None:
                if (
                    not stage.strip()
                    or len(stage) > 200
                    or progress is not None
                    and (not math.isfinite(progress) or not 0 <= progress <= 1)
                ):
                    raise AppError(422, "INVALID_PROGRESS", "任务进度不合法")
                task.stage, task.progress = stage, progress
            if execution is not None:
                task.execution_json = {**task.execution_json, **execution}

    @staticmethod
    def release(runtime, task_id, uncertain=False, backend=None):
        if runtime.active_task_id == task_id:
            runtime.state = "NEEDS_RESET" if uncertain else "IDLE"
            runtime.active_task_id = task_id if uncertain else None
            runtime.reason = (
                "无法确认上游已停止，请先停止或重启对应本机服务，再明确恢复"
                if uncertain
                else ""
            )
            runtime.affected_backend = backend if uncertain else None
            runtime.version += 1

    def finish(
        self,
        task_id,
        *,
        outcome=None,
        error_code=None,
        error_message=None,
        uncertain=False,
        keep_lease=False,
    ):
        with self.gate.lock:
            try:
                with self.database.session_scope(write=True) as session:
                    task = session.get(Task, task_id)
                    if (
                        not task
                        or task.status != "RUNNING"
                        or task.execution_owner != self.owner
                    ):
                        return
                    remote = task.execution_json.get("remote", {})
                    uncertain = uncertain or bool(
                        remote.get("started") and not remote.get("finished")
                    )
                    backend = remote.get("backend", "local")
                    if outcome is not None:
                        prepared = outcome.prepared_asset
                        if task.kind in ("IMAGE", "VIDEO", "EXPORT"):
                            if prepared is None or prepared.type != (
                                "image" if task.kind == "IMAGE" else "video"
                            ):
                                raise AppError(
                                    422, "INVALID_OUTCOME", "任务输出素材类型不合法"
                                )
                            self.storage.checked_path(
                                prepared.file_path, self.storage.media
                            )
                            self.storage.checked_file(
                                prepared.file_path, prepared.sha256
                            )
                            asset = Asset(
                                **prepared.model_dump(mode="json"),
                                project_id=task.project_id,
                                shot_id=task.shot_id,
                                source_task_id=task.id,
                            )
                            session.add(asset)
                            session.flush()
                            task.output_asset_id = asset.id
                        elif prepared is not None or outcome.result_json is None:
                            raise AppError(422, "INVALID_OUTCOME", "文本任务应返回草稿")
                        if uncertain:
                            raise AppError(
                                409,
                                "EXECUTION_STATE_UNKNOWN",
                                "输出返回但上游结束尚未确认",
                            )
                        task.result_json, task.status = outcome.result_json, "DONE"
                        task.stage, task.progress = "已完成", 1
                    else:
                        task.status, task.stage = "FAILED", "未完成"
                        task.error_code, task.error_message = error_code, error_message
                    task.finished_at = utc_now()
                    self.release(
                        session.get(RuntimeState, 1), task_id, uncertain, backend
                    )
                if not keep_lease:
                    self.gate.running.discard(task_id)
                self.gate.poisoned = False
            except AppError as exc:
                if exc.status == 503:
                    self.gate.poisoned = True
                raise

    def fail_pending(self, task_id, code, message):
        with self.database.session_scope(write=True) as session:
            task = session.get(Task, task_id)
            if task and task.status == "PENDING":
                task.status, task.stage = "FAILED", "未启动"
                task.error_code, task.error_message, task.finished_at = (
                    code,
                    message,
                    utc_now(),
                )
                self.release(session.get(RuntimeState, 1), task_id)
