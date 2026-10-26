from fastapi import APIRouter, Request

from app.schemas.projects import ProjectCreate, ProjectPatch, ProjectRead
from app.services.projects import ProjectService

router = APIRouter(prefix="/projects", tags=["projects"])


def service(request: Request) -> ProjectService:
    return ProjectService(request.app.state.database)


@router.get("", response_model=list[ProjectRead])
def list_projects(request: Request):
    return service(request).list_projects()


@router.post("", response_model=ProjectRead, status_code=201)
def create_project(data: ProjectCreate, request: Request):
    return service(request).create_project(data)


@router.get("/{project_id}", response_model=ProjectRead)
def get_project(project_id: int, request: Request):
    return service(request).get_project(project_id)


@router.patch("/{project_id}", response_model=ProjectRead)
def update_project(project_id: int, data: ProjectPatch, request: Request):
    return service(request).update_project(project_id, data)
