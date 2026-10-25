"""后续任务层的公共响应类型；M1不开放任务执行接口。"""

from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict

TaskKind = Literal["PARSE_SCRIPT", "OPTIMIZE_PROMPTS", "IMAGE", "VIDEO", "EXPORT"]
TaskStatus = Literal["PENDING", "RUNNING", "DONE", "FAILED"]


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
    created_at: datetime
    updated_at: datetime
