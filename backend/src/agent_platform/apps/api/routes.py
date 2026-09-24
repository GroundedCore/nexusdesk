from fastapi import APIRouter, Request
from pydantic import BaseModel

from agent_platform.modules.catalog import MODULES

router = APIRouter()


class HealthResponse(BaseModel):
    status: str
    version: str


class ModuleResponse(BaseModel):
    id: str
    name: str
    description: str
    status: str


@router.get("/health", response_model=HealthResponse, tags=["system"])
def health() -> HealthResponse:
    return HealthResponse(status="ok", version="0.1.0")


@router.get("/modules", response_model=list[ModuleResponse], tags=["system"])
def list_modules() -> list[ModuleResponse]:
    return [ModuleResponse(**module) for module in MODULES]


@router.get("/deployment", tags=["system"])
def deployment(request: Request):
    return {"quickstart": request.app.state.settings.quickstart_mode}
