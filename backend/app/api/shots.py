from fastapi import APIRouter, BackgroundTasks, Request

from app.models import Shot
from app.schemas.generation import ImageJobSubmit
from app.schemas.shots import (
    ReferencesUpdate,
    SelectionUpdate,
    ShotCreate,
    ShotDelete,
    ShotListRead,
    ShotPatch,
    ShotRead,
    ShotReorder,
)
from app.schemas.tasks import TaskAccepted, TaskSubmitCommand
from app.services.projects import get_record
from app.services.shots import ShotService
from app.services.tasks import register_submission

router = APIRouter(tags=["shots"])


def service(request: Request) -> ShotService:
    return ShotService(request.app.state.database, request.app.state.storage)


@router.get("/episodes/{episode_id}/shots", response_model=ShotListRead)
def list_shots(request: Request, episode_id: int):
    return service(request).list_shots(episode_id)


@router.post("/episodes/{episode_id}/shots", response_model=ShotRead, status_code=201)
def create_shot(request: Request, episode_id: int, data: ShotCreate):
    return service(request).create(episode_id, data)


@router.get("/shots/{shot_id}", response_model=ShotRead)
def get_shot(request: Request, shot_id: int):
    return service(request).get_shot(shot_id)


@router.patch("/shots/{shot_id}", response_model=ShotRead)
def update_shot(request: Request, shot_id: int, data: ShotPatch):
    return service(request).update(shot_id, data)


@router.delete("/shots/{shot_id}", response_model=ShotRead)
def delete_shot(request: Request, shot_id: int, data: ShotDelete):
    return service(request).delete(shot_id, data.expected_version)


@router.post("/episodes/{episode_id}/shots/reorder", response_model=list[ShotRead])
def reorder_shots(request: Request, episode_id: int, data: ShotReorder):
    return service(request).reorder(episode_id, data.shot_ids, data.expected_version)


@router.put("/shots/{shot_id}/references", response_model=ShotRead)
def references(request: Request, shot_id: int, data: ReferencesUpdate):
    return service(request).replace_references(
        shot_id, data.references, data.expected_version
    )


@router.put("/shots/{shot_id}/selection", response_model=ShotRead)
def selection(request: Request, shot_id: int, data: SelectionUpdate):
    return service(request).select_assets(
        shot_id,
        data,
        data.shot_version,
        data.selection_version,
    )


@router.post(
    "/shots/{shot_id}/image-jobs", response_model=TaskAccepted, status_code=202
)
def generate_image(
    request: Request, shot_id: int, data: ImageJobSubmit, background: BackgroundTasks
):
    # 这里只解析稳定归属；幂等重放、可写性、全部版本及全局槽由TaskService原子处理。
    with request.app.state.database.session_scope() as session:
        shot = get_record(session, Shot, shot_id)
        from app.models import Episode

        episode = get_record(session, Episode, shot.episode_id)
        project_id, episode_id = episode.project_id, episode.id
    command = TaskSubmitCommand(
        project_id=project_id,
        episode_id=episode_id,
        shot_id=shot_id,
        kind="IMAGE",
        idempotency_key=str(data.idempotency_key),
        expected_project_version=data.expected_project_version,
        expected_episode_version=data.expected_episode_version,
        expected_shot_version=data.expected_shot_version,
        parameters={"image_input_hash": data.expected_image_input_hash},
    )
    submission = request.app.state.task_service.submit(command)
    register_submission(background, submission)
    return TaskAccepted(
        job_id=submission.task.id,
        status=submission.task.status,
        poll_url=f"/api/v1/jobs/{submission.task.id}",
    )
