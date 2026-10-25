from fastapi import APIRouter, Request

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
from app.services.shots import ShotService

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
