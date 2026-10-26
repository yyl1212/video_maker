from fastapi import APIRouter, Request
from pydantic import BaseModel
from sqlalchemy import text

router = APIRouter(tags=["system"])


class Capabilities(BaseModel):
    projects: bool = True
    assets: bool = True
    shots: bool = True
    generation: bool = False
    jobs: bool = True


class BootstrapRead(BaseModel):
    token: str
    version: str = "0.1.0"
    module: str = "M8"
    capabilities: Capabilities = Capabilities()


class HealthRead(BaseModel):
    status: str = "ok"
    module: str = "M8"


@router.get("/bootstrap", response_model=BootstrapRead)
def bootstrap(request: Request):
    return BootstrapRead(
        token=request.app.state.token,
        capabilities=Capabilities(
            generation="IMAGE" in request.app.state.task_service.handlers
        ),
    )


@router.get("/health", response_model=HealthRead)
def health(request: Request):
    with request.app.state.database.session_scope() as session:
        session.execute(text("SELECT 1"))
    return HealthRead()
