from typing import Annotated

from fastapi import APIRouter, Query, Request

from app.schemas.tasks import ActiveJobRead, TaskDetail, TaskRead

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
