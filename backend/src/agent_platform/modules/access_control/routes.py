"""P0 organization and access-control APIs."""

from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_platform.platform.identity.access import (
    Principal,
    Reader,
    require_permission,
)
from agent_platform.platform.identity.permissions import PERMISSIONS, role_catalog
from agent_platform.platform.persistence.store import DomainError

router = APIRouter(tags=["access-control"])
MemberReader = Annotated[Principal, Depends(require_permission("member.read"))]
MemberManager = Annotated[Principal, Depends(require_permission("member.manage"))]
RoleReader = Annotated[Principal, Depends(require_permission("role.read"))]


def service(request: Request):
    return request.app.state.runtime.platform.members


class StrictInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class InviteInput(StrictInput):
    username: str = Field(min_length=3, max_length=64)
    name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "operator", "viewer"]


class MemberUpdateInput(StrictInput):
    name: str = Field(min_length=1, max_length=100)
    role: Literal["admin", "operator", "viewer"] | None
    enabled: bool
    revision: int = Field(ge=1)


class EnabledInput(StrictInput):
    enabled: bool
    revision: int = Field(ge=1)


class RevokeInput(StrictInput):
    session_id: UUID | None = None
    all: bool = False

    @model_validator(mode="after")
    def target(self):
        if not self.session_id and not self.all:
            raise ValueError("session_target_required")
        return self


@router.get("/members")
async def members(
    request: Request,
    user: MemberReader,
    q: str = Query(default="", max_length=100),
    role: Literal["admin", "operator", "viewer", "unassigned"] | None = None,
    status: Literal["all", "enabled", "disabled", "pending"] = "all",
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=10, ge=1, le=100),
):
    return await service(request).list(
        user.tenant,
        q=q,
        role=role,
        status=status,
        page=page,
        page_size=page_size,
    )


@router.post("/members/invite", status_code=201)
async def invite_member(body: InviteInput, request: Request, user: MemberManager):
    return await service(request).create_local(
        user.tenant,
        user.actor,
        body.name,
        body.username,
        body.role,
    )


@router.get("/members/{member_id}")
async def member_detail(member_id: UUID, request: Request, user: MemberReader):
    return await service(request).get(user.tenant, member_id)


@router.patch("/members/{member_id}")
async def update_member(
    member_id: UUID,
    body: MemberUpdateInput,
    request: Request,
    user: MemberManager,
):
    return await service(request).update(
        user.tenant,
        user.actor,
        user.user_id,
        member_id,
        body.name,
        body.role,
        body.enabled,
        body.revision,
    )


@router.post("/members/{member_id}/enabled")
async def set_member_enabled(
    member_id: UUID,
    body: EnabledInput,
    request: Request,
    user: MemberManager,
):
    return await service(request).set_enabled(
        user.tenant,
        user.actor,
        user.user_id,
        member_id,
        body.enabled,
        body.revision,
    )


@router.post("/members/{member_id}/password/reset")
async def reset_member_password(
    member_id: UUID,
    request: Request,
    user: MemberManager,
):
    return await service(request).reset_password(
        user.tenant,
        user.actor,
        user.user_id,
        member_id,
    )


@router.get("/members/{member_id}/sessions")
async def member_sessions(member_id: UUID, request: Request, user: Reader):
    if user.user_id != str(member_id) and not user.can("member.read"):
        raise DomainError("insufficient_permission", 403)
    return await service(request).sessions(
        user.tenant,
        member_id,
        current_session_id=user.session_id,
    )


@router.post("/members/{member_id}/sessions/revoke")
async def revoke_member_sessions(
    member_id: UUID,
    body: RevokeInput,
    request: Request,
    user: Reader,
):
    if user.user_id != str(member_id) and not user.can("member.manage"):
        raise DomainError("insufficient_permission", 403)
    return await service(request).revoke_sessions(
        user.tenant,
        user.actor,
        member_id,
        session_id=body.session_id,
        all_sessions=body.all,
    )


@router.get("/sessions")
async def current_sessions(request: Request, user: Reader):
    if not user.user_id:
        return {"items": []}
    return await service(request).sessions(
        user.tenant,
        UUID(user.user_id),
        current_session_id=user.session_id,
    )


@router.post("/sessions/revoke")
async def revoke_current_sessions(body: RevokeInput, request: Request, user: Reader):
    if not user.user_id:
        raise DomainError("identity_session_required", 409)
    return await service(request).revoke_sessions(
        user.tenant,
        user.actor,
        UUID(user.user_id),
        session_id=body.session_id,
        all_sessions=body.all,
    )


@router.get("/roles")
async def roles(user: RoleReader):
    return {"items": role_catalog()}


@router.get("/permissions")
async def permissions(user: RoleReader):
    return {
        "items": [
            {"code": code, "description": description}
            for code, description in PERMISSIONS.items()
        ]
    }
