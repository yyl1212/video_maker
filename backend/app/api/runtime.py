from fastapi import APIRouter, Request

from app.errors import AppError
from app.schemas.tasks import RuntimeRead, RuntimeReset

router = APIRouter(prefix="/runtime", tags=["runtime"])


@router.get("", response_model=RuntimeRead)
def get_runtime(request: Request):
    return request.app.state.task_service.active().runtime_state


@router.post("/reset", response_model=RuntimeRead)
def reset_runtime(request: Request, data: RuntimeReset):
    if not data.confirmed_stopped:
        raise AppError(
            422, "STOP_CONFIRMATION_REQUIRED", "请先停止或重启受影响服务，并明确确认"
        )
    return request.app.state.recovery.confirm_reset(data.expected_version, data.backend)
