from typing import Annotated, Literal

from fastapi import APIRouter, File, Query, Request, UploadFile
from fastapi.responses import FileResponse

from app.schemas.assets import AssetPatch, AssetRead, DeleteAsset
from app.services.assets import AssetService

router = APIRouter(tags=["assets"])


def service(request: Request) -> AssetService:
    return AssetService(request.app.state.database, request.app.state.storage)


@router.get("/projects/{project_id}/assets", response_model=list[AssetRead])
def list_assets(
    request: Request,
    project_id: int,
    type: Literal["image", "video"] | None = None,
    tag: str | None = None,
    shot_id: int | None = None,
):
    return service(request).list_assets(project_id, type, tag, shot_id)


@router.post("/projects/{project_id}/assets", response_model=AssetRead, status_code=201)
def upload_asset(
    request: Request, project_id: int, file: Annotated[UploadFile, File()]
):
    return service(request).upload(project_id, file)


@router.patch("/assets/{asset_id}", response_model=AssetRead)
def update_asset(request: Request, asset_id: int, data: AssetPatch):
    return service(request).update(asset_id, data)


@router.delete("/assets/{asset_id}", response_model=AssetRead)
def delete_asset(request: Request, asset_id: int, data: DeleteAsset):
    return service(request).delete(asset_id, data.expected_version)


@router.get("/assets/{asset_id}/content", response_class=FileResponse)
def content(
    request: Request,
    asset_id: int,
    project_id: Annotated[int, Query(ge=1)],
    download: bool = False,
):
    path, mime, name = service(request).content(asset_id, project_id)
    return FileResponse(path, media_type=mime, filename=name if download else None)


@router.get("/assets/{asset_id}/thumbnail", response_class=FileResponse)
def thumbnail(request: Request, asset_id: int, project_id: Annotated[int, Query(ge=1)]):
    path, mime, _ = service(request).content(asset_id, project_id, True)
    return FileResponse(path, media_type=mime)
