"""Console memory management API (FR-4).

Mounted at ``/api/v1/memory``. Authorisation follows the platform staff-role
precedent: any authenticated principal reads (``Reader``), writes require
``admin`` or ``operator`` (``Operator``); viewers get 403 on mutations.
"""

from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import BaseModel, ConfigDict, Field

from agent_platform.platform.identity.access import Operator, Reader

router = APIRouter(prefix="/memory", tags=["memory"])


def services(request):
    return request.app.state.runtime.platform.memory


class ItemEdit(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=1000)


@router.get("/customers")
async def customers(
    request: Request,
    user: Reader,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    return await services(request).list_customers(user.tenant, q, page, page_size)


@router.get("/customers/{cid}")
async def customer(cid: UUID, request: Request, user: Reader):
    return await services(request).customer_detail(user.tenant, cid)


@router.get("/pending")
async def pending(
    request: Request,
    user: Reader,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
):
    return await services(request).pending_items(user.tenant, page, page_size)


@router.patch("/items/{iid}")
async def edit_item(iid: UUID, body: ItemEdit, request: Request, user: Operator):
    return await services(request).edit_item(user.tenant, user.actor, iid, body.content)


@router.delete("/items/{iid}")
async def delete_item(iid: UUID, request: Request, user: Operator):
    return await services(request).delete_item(user.tenant, user.actor, iid)


@router.post("/items/{iid}/restore")
async def restore_item(iid: UUID, request: Request, user: Operator):
    return await services(request).restore_item(user.tenant, user.actor, iid)


@router.post("/items/{iid}/confirm")
async def confirm_item(iid: UUID, request: Request, user: Operator):
    return await services(request).confirm_item(user.tenant, user.actor, iid)


@router.post("/items/{iid}/reject")
async def reject_item(iid: UUID, request: Request, user: Operator):
    return await services(request).reject_item(user.tenant, user.actor, iid)
