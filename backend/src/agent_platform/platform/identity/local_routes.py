from typing import Annotated

from fastapi import APIRouter, Header, Request, Response
from pydantic import BaseModel, Field, SecretStr

from agent_platform.platform.identity.local_admin import LocalAdmin
from agent_platform.platform.persistence.store import DomainError, one

router = APIRouter(prefix="/auth/local", tags=["local-admin"])


def service(request):
    return LocalAdmin(
        request.app.state.runtime.platform.engine, request.app.state.settings.tenant_id
    )


def bearer(value):
    if not value or not value.startswith("Bearer local_"):
        raise DomainError("invalid_local_session", 401)
    return value[7:]


class LoginInput(BaseModel):
    username: str = Field(min_length=1, max_length=64)
    password: SecretStr = Field(min_length=1, max_length=128)


class PasswordInput(BaseModel):
    old_password: SecretStr = Field(min_length=1, max_length=128)
    new_password: SecretStr = Field(min_length=12, max_length=128)


@router.get("/status")
async def status(request: Request):
    settings = request.app.state.settings
    local = service(request)
    async with local.engine.connect() as c:
        initialized = bool(
            await one(
                c, "SELECT user_id FROM local_admin_credentials WHERE tenant_id=:t", t=local.tenant
            )
        )
    return {
        "initialized": initialized,
        "development_access": not initialized
        and not settings.api_token
        and settings.environment == "development",
    }


@router.post("/login")
async def login(body: LoginInput, request: Request):
    return await service(request).login(
        body.username,
        body.password.get_secret_value(),
        request.client.host if request.client else "unknown",
    )


@router.post("/logout", status_code=204)
async def logout(request: Request, authorization: Annotated[str | None, Header()] = None):
    await service(request).logout(bearer(authorization))
    return Response(status_code=204)


@router.post("/password", status_code=204)
async def password(
    body: PasswordInput, request: Request, authorization: Annotated[str | None, Header()] = None
):
    await service(request).change_password(
        bearer(authorization),
        body.old_password.get_secret_value(),
        body.new_password.get_secret_value(),
    )
    return Response(status_code=204)
