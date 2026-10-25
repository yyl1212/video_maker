from typing import Annotated

from fastapi import APIRouter, BackgroundTasks, Query, Request
from fastapi.responses import FileResponse

from app.models import Episode
from app.schemas.assets import AssetRead
from app.schemas.exports import ExportPreviewRead, ExportSubmit
from app.schemas.tasks import TaskAccepted, TaskSubmitCommand
from app.services.projects import get_record
from app.services.tasks import register_submission

router = APIRouter(tags=["exports"])


@router.get("/episodes/{episode_id}/export-preview", response_model=ExportPreviewRead)
def preview(request: Request, episode_id: int):
    return request.app.state.export_service.preview(episode_id)


@router.post(
    "/episodes/{episode_id}/export-jobs", response_model=TaskAccepted, status_code=202
)
def submit(
    request: Request, episode_id: int, data: ExportSubmit, background: BackgroundTasks
):
    with request.app.state.database.session_scope() as session:
        episode = get_record(session, Episode, episode_id)
        project_id = episode.project_id
    command = TaskSubmitCommand(
        project_id=project_id,
        episode_id=episode_id,
        kind="EXPORT",
        idempotency_key=str(data.idempotency_key),
        expected_project_version=data.expected_project_version,
        expected_episode_version=data.expected_episode_version,
        parameters={
            "output_profile": data.output_profile,
            "clips": [c.model_dump() for c in data.clips],
        },
    )
    result = request.app.state.task_service.submit(command)
    register_submission(background, result)
    return TaskAccepted(
        job_id=result.task.id,
        status=result.task.status,
        poll_url=f"/api/v1/jobs/{result.task.id}",
    )


@router.get("/episodes/{episode_id}/exports", response_model=list[AssetRead])
def history(request: Request, episode_id: int):
    return request.app.state.export_service.history(episode_id)


@router.get("/assets/{asset_id}/manifest", response_class=FileResponse)
def manifest(request: Request, asset_id: int, project_id: Annotated[int, Query(ge=1)]):
    path, name = request.app.state.export_service.manifest(asset_id, project_id)
    return FileResponse(path, media_type="application/json", filename=name)
