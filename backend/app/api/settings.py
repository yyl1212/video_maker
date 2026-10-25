from fastapi import APIRouter, Request

from app.models import ModelSettings
from app.schemas.clients import SettingsRead, SettingsUpdate
from app.services.projects import check_version

router = APIRouter(tags=["settings"])


def read_settings(record) -> SettingsRead:
    return SettingsRead(
        version=record.version,
        text=record.text_settings,
        image=record.image_settings,
        video=record.video_settings,
    )


@router.get("/settings", response_model=SettingsRead)
def get_settings(request: Request):
    with request.app.state.database.session_scope() as session:
        return read_settings(session.get(ModelSettings, 1))


@router.put("/settings", response_model=SettingsRead)
def update_settings(data: SettingsUpdate, request: Request):
    with request.app.state.database.session_scope(write=True) as session:
        record = session.get(ModelSettings, 1)
        check_version(record, data.expected_version)
        record.text_settings = data.text.model_dump()
        record.image_settings = data.image.model_dump()
        record.video_settings = data.video.model_dump()
        record.version += 1
        session.flush()
        return read_settings(record)
