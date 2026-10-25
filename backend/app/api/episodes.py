from fastapi import APIRouter, Request

from app.api.projects import service
from app.schemas.episodes import EpisodeCreate, EpisodePatch, EpisodeRead

router = APIRouter(tags=["episodes"])


@router.get("/projects/{project_id}/episodes", response_model=list[EpisodeRead])
def list_episodes(project_id: int, request: Request):
    return service(request).list_episodes(project_id)


@router.post(
    "/projects/{project_id}/episodes", response_model=EpisodeRead, status_code=201
)
def create_episode(project_id: int, data: EpisodeCreate, request: Request):
    return service(request).create_episode(project_id, data)


@router.get("/episodes/{episode_id}", response_model=EpisodeRead)
def get_episode(episode_id: int, request: Request):
    return service(request).get_episode(episode_id)


@router.patch("/episodes/{episode_id}", response_model=EpisodeRead)
def update_episode(episode_id: int, data: EpisodePatch, request: Request):
    return service(request).update_episode(episode_id, data)
