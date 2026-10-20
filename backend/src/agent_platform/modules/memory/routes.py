"""Console memory management API (FR-4).

Mounted at ``/api/v1/memory``. Authorisation follows the platform staff-role
precedent: any authenticated principal reads (``Reader``), writes require
``admin`` or ``operator`` (``Operator``); viewers get 403 on mutations.
"""

from typing import Literal
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


class FlagUpsert(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    content: str = Field(min_length=1, max_length=500)


class CustomerMerge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_customer_id: UUID


@router.get("/customers")
async def customers(
    request: Request,
    user: Reader,
    q: str = Query(default="", max_length=200),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
    with_stats: bool = Query(default=False),
    sort: Literal["updated", "pending", "total"] = Query(default="updated"),
    flag: str | None = Query(default=None, max_length=30),
):
    # Phase 6 (FR-2): with_stats attaches the batched memory stats columns;
    # sort orders by pending/total memory counts; flag filters by one active
    # service flag.
    return await services(request).list_customers(
        user.tenant, q, page, page_size, with_stats=with_stats, sort=sort, flag=flag
    )


@router.get("/customers/{cid}")
async def customer(cid: UUID, request: Request, user: Reader):
    return await services(request).customer_detail(user.tenant, cid)


@router.get("/customers/{cid}/injections")
async def customer_injections(
    cid: UUID,
    request: Request,
    user: Reader,
    limit: int = Query(default=50, ge=1, le=200),
):
    # Phase 6 (FR-4): run-dimension injection history for one customer.
    return await services(request).list_injections(user.tenant, cid, limit)


@router.get("/customers/{cid}/audit")
async def customer_audit(
    cid: UUID,
    request: Request,
    user: Reader,
    limit: int = Query(default=100, ge=1, le=500),
):
    # Phase 6 (FR-3): the customer's memory.* audit timeline (content-free
    # payloads, Phase 4 C4).
    return await services(request).customer_audit_timeline(user.tenant, cid, limit)


@router.get("/runs/{run_id}/injections")
async def run_injections(run_id: UUID, request: Request, user: Reader):
    # Phase 6 (FR-4): one run's item-level injection detail; historical runs
    # without detail rows return an empty list (AC-8).
    return await services(request).injection_detail(user.tenant, run_id)


@router.get("/items/{iid}/injections")
async def item_injections(
    iid: UUID,
    request: Request,
    user: Reader,
    limit: int = Query(default=20, ge=1, le=100),
):
    # Phase 6 (FR-4): reverse lookup of the runs one item was injected into.
    return await services(request).item_injections(user.tenant, iid, limit)


@router.get("/items/{iid}/timeline")
async def item_timeline(
    iid: UUID,
    request: Request,
    user: Reader,
    limit: int = Query(default=100, ge=1, le=500),
):
    # Phase 6 (FR-5): single-item lifecycle — full current fields (including
    # prev_content and the live effective confidence), audit timeline and
    # injection summary.
    return await services(request).item_timeline(user.tenant, iid, limit)


@router.delete("/customers/{cid}")
async def delete_customer(cid: UUID, request: Request, user: Operator):
    # FR-4. The probed store rides along for the (currently no-op)
    # out-of-band index cleanup hook (R16).
    store = request.app.state.runtime.platform.memory_store
    return await services(request).delete_customer(user.tenant, user.actor, cid, store=store)


@router.post("/customers/{cid}/merge")
async def merge_customer(cid: UUID, body: CustomerMerge, request: Request, user: Operator):
    # FR-5: source (cid) merges into the target; source == target is a 400.
    return await services(request).merge_customers(
        user.tenant, user.actor, cid, body.target_customer_id
    )


@router.put("/customers/{cid}/flags/{key}")
async def upsert_flag(cid: UUID, key: str, body: FlagUpsert, request: Request, user: Operator):
    # FR-2: key must be one of vip/complaint_risk/blacklist (400 otherwise).
    return await services(request).upsert_flag(user.tenant, user.actor, cid, key, body.content)


@router.delete("/customers/{cid}/flags/{key}")
async def remove_flag(cid: UUID, key: str, request: Request, user: Operator):
    return await services(request).remove_flag(user.tenant, user.actor, cid, key)


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


@router.post("/items/{iid}/release")
async def release_item(iid: UUID, request: Request, user: Operator):
    # FR-1: return a human-protected item to automatic management.
    return await services(request).release_item(user.tenant, user.actor, iid)


@router.post("/items/{iid}/confirm")
async def confirm_item(iid: UUID, request: Request, user: Operator):
    return await services(request).confirm_item(user.tenant, user.actor, iid)


@router.post("/items/{iid}/reject")
async def reject_item(iid: UUID, request: Request, user: Operator):
    return await services(request).reject_item(user.tenant, user.actor, iid)
