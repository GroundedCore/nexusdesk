import hmac
import json
import secrets
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Header, Query, Request
from fastapi.encoders import jsonable_encoder
from fastapi.responses import HTMLResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field

from agent_platform.platform.identity.access import Admin
from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)

from .enterprise import EmbedInput, ProviderInput, UserInput
from .routes import Identity, PublicRoute, service
from .service import digest

management = APIRouter(prefix="/open-platform", tags=["enterprise-identity"])
public = APIRouter(prefix="/sso", tags=["enterprise-sso"])
tokens = APIRouter(tags=["enterprise-chat"], route_class=PublicRoute)


def ent(request):
    return service(request).enterprise


@management.get("/identity/providers")
async def providers(request: Request, user: Admin):
    async with ent(request).engine.connect() as c:
        return [
            ent(request).public_provider(p)
            for p in await many(
                c,
                "SELECT * FROM enterprise_providers WHERE tenant_id=:t ORDER BY created_at",
                t=user.tenant,
            )
        ]


@management.post("/identity/providers", status_code=201)
async def create_provider(body: ProviderInput, request: Request, user: Admin):
    return await ent(request).save_provider(user.actor, body)


@management.put("/identity/providers/{pid}")
async def update_provider(pid: UUID, body: ProviderInput, request: Request, user: Admin):
    return await ent(request).save_provider(user.actor, body, pid)


@management.get("/identity/users")
async def users(
    request: Request,
    user: Admin,
    q: str = Query(default="", max_length=100),
    page: int = Query(default=1, ge=1),
):
    async with ent(request).engine.connect() as c:
        rows = await many(
            c,
            """SELECT u.*,COALESCE((SELECT jsonb_agg(jsonb_build_object('id',i.id,'source',i.source,'subject',i.subject))
            FROM enterprise_identities i WHERE i.user_id=u.id),'[]') AS identities FROM enterprise_users u
            WHERE u.tenant_id=:t AND (u.name ILIKE :q OR u.id::text ILIKE :q) ORDER BY u.created_at DESC,u.id LIMIT 20 OFFSET :off""",
            t=user.tenant,
            q="%" + q + "%",
            off=(page - 1) * 20,
        )
        total = await one(
            c,
            "SELECT count(*) AS n FROM enterprise_users WHERE tenant_id=:t AND (name ILIKE :q OR id::text ILIKE :q)",
            t=user.tenant,
            q="%" + q + "%",
        )
        return {"items": rows, "total": total["n"]}


@management.put("/identity/users/{uid}")
async def update_user(uid: UUID, body: UserInput, request: Request, user: Admin):
    return await ent(request).update_user(user.actor, uid, body)


class LinkInput(BaseModel):
    user_id: UUID


@management.post("/identity/identities/{iid}/link")
async def link_identity(iid: UUID, body: LinkInput, request: Request, user: Admin):
    return await ent(request).link_identity(user.actor, iid, body.user_id)


@management.get("/applications/{app_id}/embed")
async def embed_config(app_id: UUID, request: Request, user: Admin):
    async with ent(request).engine.connect() as c:
        await service(request).application(c, user.tenant, app_id)
        return await one(c, "SELECT * FROM open_embed_configs WHERE app_id=:a", a=app_id)


@management.put("/applications/{app_id}/embed")
async def save_embed(app_id: UUID, body: EmbedInput, request: Request, user: Admin):
    return await ent(request).save_embed(user.actor, app_id, body)


@public.get("/providers")
async def login_options(request: Request):
    async with ent(request).engine.connect() as c:
        return await many(
            c,
            "SELECT id,name,kind,config->>'platform_origin' AS platform_origin FROM enterprise_providers WHERE tenant_id=:t AND enabled AND workbench ORDER BY name",
            t=ent(request).tenant,
        )


@public.get("/embed/{app_id}")
async def embed_options(
    app_id: UUID, request: Request, parent_origin: str = Query(max_length=2000)
):
    e = ent(request)
    async with e.engine.connect() as c:
        cfg = await e.embed(c, app_id, parent_origin)
        providers = await many(
            c,
            "SELECT id,name,kind,config->>'platform_origin' AS platform_origin FROM enterprise_providers WHERE tenant_id=:t AND enabled AND id=ANY(:ids) ORDER BY name",
            t=e.tenant,
            ids=cfg["provider_ids"],
        )
        return {"title": cfg["title"], "color": cfg["color"], "providers": providers}


@public.get("/start/{pid}")
async def start(
    pid: UUID,
    request: Request,
    channel: str = Query(min_length=32, max_length=100, pattern=r"^[a-zA-Z0-9_-]+$"),
    app_id: UUID | None = None,
    parent_origin: str = Query(default="", max_length=2000),
):
    e = ent(request)
    async with e.engine.begin() as c:
        p = await e.provider(c, pid)
        if not p["enabled"] or not app_id and not p["workbench"]:
            raise DomainError("identity_provider_disabled", 403)
        if app_id:
            cfg = await e.embed(c, app_id, parent_origin)
            if pid not in cfg["provider_ids"]:
                raise DomainError("identity_provider_not_authorized", 403)
        await execute(
            c, "SELECT pg_advisory_xact_lock(hashtextextended(:t,0))", t=e.tenant + ":sso-start"
        )
        await execute(c, "DELETE FROM enterprise_login_states WHERE expires_at<now()")
        await execute(c, "DELETE FROM enterprise_sessions WHERE expires_at<now()")
        count = await one(
            c, "SELECT count(*) AS n FROM enterprise_login_states WHERE tenant_id=:t", t=e.tenant
        )
        if count["n"] >= 1000:
            raise DomainError("sso_login_rate_limit", 429)
        state, browser, nonce, verifier = [secrets.token_urlsafe(32) for _ in range(4)]
        callback = p["config"]["platform_origin"] + "/api/v1/sso/callback/" + str(pid)
        url = await e.idp.authorize(p, state, nonce, verifier, callback)
        encrypted = e.vault.encrypt(
            verifier, e.tenant, digest(state), {"base_url": str(pid), "protocol": "sso-state"}
        )
        payload = {
            "channel": channel,
            "app_id": str(app_id) if app_id else None,
            "parent_origin": parent_origin,
            "nonce": nonce,
            "verifier": encrypted,
        }
        await execute(
            c,
            """INSERT INTO enterprise_login_states(state_hash,tenant_id,provider_id,provider_revision,browser_hash,payload)
            VALUES(:s,:t,:p,:rev,:b,CAST(:payload AS jsonb))""",
            s=digest(state),
            t=e.tenant,
            p=pid,
            rev=p["revision"],
            b=digest(browser),
            payload=json.dumps(payload),
        )
    response = RedirectResponse(
        url,
        status_code=302,
        headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"},
    )
    response.set_cookie(
        "enterprise_sso",
        browser,
        max_age=300,
        httponly=True,
        secure=p["config"]["platform_origin"].startswith("https:"),
        samesite="lax",
        path="/api/v1/sso",
    )
    return response


def callback_html(data, target):
    nonce = secrets.token_urlsafe(24)
    payload = json.dumps(jsonable_encoder(data), ensure_ascii=True).replace("<", "\\u003c")
    target_json = json.dumps(target).replace("<", "\\u003c")
    response = HTMLResponse(
        f'''<!doctype html><html><head><meta charset="utf-8"><title>企业登录</title></head>
      <body><p>登录处理完成，请返回原窗口。Login processed. Return to the original window.</p>
      <script nonce="{nonce}">if(window.opener){{window.opener.postMessage({payload},{target_json});window.close();}}</script></body></html>''',
        headers={
            "Cache-Control": "no-store",
            "Pragma": "no-cache",
            "Referrer-Policy": "no-referrer",
            "Content-Security-Policy": f"default-src 'none'; script-src 'nonce-{nonce}'; frame-ancestors 'none'; base-uri 'none'",
            "X-Content-Type-Options": "nosniff",
        },
    )
    response.delete_cookie("enterprise_sso", path="/api/v1/sso")
    return response


@public.get("/callback/{pid}")
async def callback(
    pid: UUID,
    request: Request,
    state: str = Query(default="", max_length=200),
    code: str = Query(default="", max_length=4000),
    authCode: str = Query(default="", max_length=4000),
    error: str = Query(default="", max_length=500),
):
    e = ent(request)
    async with e.engine.begin() as c:
        # Atomic one-time consumption precedes token exchange, including failed attempts.
        login = await one(
            c,
            """DELETE FROM enterprise_login_states WHERE state_hash=:s AND tenant_id=:t
            AND provider_id=:p AND expires_at>now() RETURNING *""",
            s=digest(state),
            t=e.tenant,
            p=pid,
        )
    if not login or not hmac.compare_digest(
        login["browser_hash"], digest(request.cookies.get("enterprise_sso", ""))
    ):
        raise DomainError("invalid_sso_state", 401)
    payload = login["payload"]
    async with e.engine.connect() as c:
        p = await e.provider(c, pid)
    result = {"type": "nexusdesk.sso", "channel": payload["channel"]}
    try:
        if error or not (code or authCode):
            raise DomainError("sso_authorization_denied", 401)
        if not p["enabled"] or p["revision"] != login["provider_revision"]:
            raise DomainError("identity_provider_configuration_changed", 409)
        secret = e.vault.decrypt(
            p["encrypted_secret"],
            e.tenant,
            pid,
            {"base_url": str(pid), "protocol": "enterprise-sso"},
        )
        payload["verifier"] = e.vault.decrypt(
            payload["verifier"],
            e.tenant,
            digest(state),
            {"base_url": str(pid), "protocol": "sso-state"},
        )
        sub, name = await e.idp.exchange(
            p,
            secret,
            code or authCode,
            payload,
            p["config"]["platform_origin"] + "/api/v1/sso/callback/" + str(pid),
        )
        async with e.engine.begin() as c:
            user = await e.resolve(c, "provider:" + str(pid), sub, name)
        # Persist pending first-login users even when workbench access has not yet been granted.
        async with e.engine.begin() as c:
            current = await e.provider(c, pid, True)
            if not current["enabled"] or current["revision"] != p["revision"]:
                raise DomainError("identity_provider_configuration_changed", 409)
            app_id = UUID(payload["app_id"]) if payload["app_id"] else None
            cfg = await e.embed(c, app_id, payload["parent_origin"]) if app_id else None
            if app_id and pid not in cfg["provider_ids"] or not app_id and not current["workbench"]:
                raise DomainError("identity_provider_not_authorized", 403)
            user = await e.resolve(c, "provider:" + str(pid), sub, name)
            user = required(
                await one(c, "SELECT * FROM enterprise_users WHERE id=:u FOR UPDATE", u=user["id"])
            )
            result["session"] = await e.issue(
                c, user, app_id, current, embed_revision=cfg["revision"] if cfg else None
            )
            await audit(
                c,
                e.tenant,
                "enterprise:" + str(user["id"]),
                "enterprise_login.succeeded",
                pid,
                app_id=str(app_id) if app_id else None,
            )
    except DomainError as exc:
        result["error"] = exc.code
    except (KeyError, ValueError, TypeError):
        result["error"] = "invalid_identity_provider_response"
    return callback_html(result, p["config"]["platform_origin"])


@public.post("/logout")
async def logout(request: Request, authorization: Annotated[str | None, Header()] = None):
    if authorization and authorization.startswith(("Bearer chat_", "Bearer ssow_")):
        async with ent(request).engine.begin() as c:
            await execute(
                c,
                "UPDATE enterprise_sessions SET revoked=true WHERE tenant_id=:t AND token_hash=:hash",
                t=ent(request).tenant,
                hash=digest(authorization[7:]),
            )
    return {"logged_out": True}


class ChatTokenInput(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    external_user_id: str = Field(min_length=1, max_length=128)
    name: str = Field(default="", max_length=100)
    parent_origin: str = Field(max_length=2000)


@tokens.post("/chat/token")
async def chat_token(body: ChatTokenInput, request: Request, identity: Identity):
    if identity.external_user_id:
        raise DomainError("application_key_required", 403)
    e = ent(request)
    async with e.engine.begin() as c:
        await service(request).check(c, identity, True)
        cfg = await e.embed(c, identity.app_id, body.parent_origin)
        user = await e.resolve(
            c,
            "app:" + str(identity.app_id),
            body.external_user_id,
            body.name or body.external_user_id,
        )
        return await e.issue(
            c, user, identity.app_id, key_id=identity.key_id, embed_revision=cfg["revision"]
        )
