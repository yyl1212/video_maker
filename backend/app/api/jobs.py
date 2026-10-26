from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Request

from app.errors import AppError
from app.models import Episode, Shot
from app.schemas.episodes import EpisodeRead
from app.schemas.shots import ShotRead
from app.schemas.storyboards import DraftApply, DraftRead, TextJobSubmit
from app.schemas.tasks import (
    ActiveJobRead,
    TaskAccepted,
    TaskDetail,
    TaskRead,
    TaskSubmitCommand,
)
from app.services.projects import get_record
from app.services.tasks import register_submission

router = APIRouter(tags=["jobs"])


@router.get("/jobs/active", response_model=ActiveJobRead)
def active(request: Request):
    return request.app.state.task_service.active()


@router.get("/jobs/{task_id}", response_model=TaskDetail)
def get_job(request: Request, task_id: int):
    return request.app.state.task_service.get(task_id)


@router.get("/projects/{project_id}/jobs", response_model=list[TaskRead])
def project_jobs(
    request: Request, project_id: int, limit: Annotated[int, Query(ge=1, le=100)] = 50
):
    return request.app.state.task_service.list_project(project_id, limit)


@router.post(
    "/episodes/{episode_id}/parse-jobs", response_model=TaskAccepted, status_code=202
)
def parse_script_job(
    request: Request, episode_id: int, data: TextJobSubmit, background: BackgroundTasks
):
    with request.app.state.database.session_scope() as session:
        episode = get_record(session, Episode, episode_id)
        project_id = episode.project_id
    if data.expected_shot_version is not None:
        raise AppError(422, "INVALID_SCOPE", "解析任务不能指定镜头版本")
    command = TaskSubmitCommand(
        project_id=project_id,
        episode_id=episode_id,
        kind="PARSE_SCRIPT",
        idempotency_key=str(data.idempotency_key),
        expected_project_version=data.expected_project_version,
        expected_episode_version=data.expected_episode_version,
    )
    result = request.app.state.task_service.submit(command)
    register_submission(background, result)
    return TaskAccepted(
        job_id=result.task.id,
        status=result.task.status,
        poll_url=f"/api/v1/jobs/{result.task.id}",
    )


@router.post(
    "/shots/{shot_id}/prompt-jobs", response_model=TaskAccepted, status_code=202
)
def prompt_job(
    request: Request, shot_id: int, data: TextJobSubmit, background: BackgroundTasks
):
    if data.expected_shot_version is None:
        raise AppError(422, "INVALID_SCOPE", "扩写任务须提供镜头版本")
    with request.app.state.database.session_scope() as session:
        shot = get_record(session, Shot, shot_id)
        episode = get_record(session, Episode, shot.episode_id)
        project_id, episode_id = episode.project_id, episode.id
    command = TaskSubmitCommand(
        project_id=project_id,
        episode_id=episode_id,
        shot_id=shot_id,
        kind="OPTIMIZE_PROMPTS",
        idempotency_key=str(data.idempotency_key),
        expected_project_version=data.expected_project_version,
        expected_episode_version=data.expected_episode_version,
        expected_shot_version=data.expected_shot_version,
    )
    result = request.app.state.task_service.submit(command)
    register_submission(background, result)
    return TaskAccepted(
        job_id=result.task.id,
        status=result.task.status,
        poll_url=f"/api/v1/jobs/{result.task.id}",
    )


@router.get("/jobs/{task_id}/draft", response_model=DraftRead)
def draft(request: Request, task_id: int, project_id: Annotated[int, Query(ge=1)]):
    return request.app.state.storyboards.review(task_id, project_id)


@router.post("/jobs/{task_id}/apply", response_model=EpisodeRead | ShotRead)
def apply_draft(request: Request, task_id: int, data: DraftApply):
    return request.app.state.storyboards.apply(
        task_id,
        data.edited_value,
        data.expected_version,
        data.replace_confirm,
        data.project_id,
    )
