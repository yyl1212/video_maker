"""任务契约；提交命令供业务处理器使用，不注册通用生成入口。"""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .common import InputModel, Version

TaskKind = Literal["PARSE_SCRIPT", "OPTIMIZE_PROMPTS", "IMAGE", "VIDEO", "EXPORT"]
TaskStatus = Literal["PENDING", "RUNNING", "DONE", "FAILED"]


class TaskSubmitCommand(InputModel):
    project_id: int = Field(gt=0, strict=True)
    episode_id: int = Field(gt=0, strict=True)
    shot_id: int | None = Field(default=None, gt=0, strict=True)
    kind: TaskKind
    idempotency_key: str
    expected_project_version: Version
    expected_episode_version: Version
    expected_shot_version: Version | None = None
    expected_selection_version: Version | None = None
    parameters: dict = Field(default_factory=dict)

    @field_validator("idempotency_key")
    @classmethod
    def uuid_key(cls, value):
        return str(UUID(value))

    @model_validator(mode="after")
    def scope(self):
        needs_shot = self.kind not in ("PARSE_SCRIPT", "EXPORT")
        if needs_shot != (self.shot_id is not None):
            raise ValueError("任务范围与类型不匹配")
        if needs_shot and self.expected_shot_version is None:
            raise ValueError("镜头任务必须核对镜头版本")
        return self


class TaskRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    project_id: int
    episode_id: int | None
    shot_id: int | None
    kind: TaskKind
    status: TaskStatus
    stage: str
    progress: float | None
    output_asset_id: int | None
    error_code: str | None
    error_message: str | None
    result_summary: str | None = None
    heartbeat_at: datetime | None
    deadline_at: datetime | None
    started_at: datetime | None
    finished_at: datetime | None
    created_at: datetime
    updated_at: datetime


class TaskDetail(TaskRead):
    result_json: dict | None


class RuntimeRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    version: int
    state: Literal["IDLE", "ACTIVE", "NEEDS_RESET"]
    active_task_id: int | None
    reason: str
    affected_backend: str | None
    updated_at: datetime


class ActiveJobRead(InputModel):
    task: TaskRead | None
    runtime_state: RuntimeRead


class TaskAccepted(InputModel):
    job_id: int
    status: TaskStatus
    poll_url: str


class RuntimeReset(InputModel):
    expected_version: Version
    backend: Literal["llm", "drawthings", "mlx", "ffmpeg", "local"]
    confirmed_stopped: bool = Field(strict=True)
