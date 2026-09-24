"""Collection management; execution always uses immutable per-API snapshots."""

import asyncio
import hashlib
import json
from typing import Literal
from urllib.parse import unquote, urlsplit
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, SecretStr

from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)

from .service import HttpTool, ToolInput


class CollectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=64)
    description: str = Field(default="", max_length=200)
    icon: Literal["api", "orders", "support", "inventory", "policy"] = "api"
    base_url: str
    auth_kind: Literal["none", "bearer", "api_key", "custom"] = "custom"
    headers: dict[str, str] = Field(default_factory=dict)
    headers_from_env: dict[str, str] = Field(default_factory=dict)
    header_secrets: dict[str, SecretStr | None] | None = Field(default=None, repr=False)
    revision: int | None = Field(default=None, ge=1)

    def definition(self):
        return ToolInput(
            auth_kind=self.auth_kind,
            name="collection",
            description="Collection",
            url=self.base_url.rstrip("/"),
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
            headers=self.headers,
            headers_from_env=self.headers_from_env,
            header_secrets=self.header_secrets,
        )


class ApiInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    display_name: str = Field(min_length=1, max_length=64)
    relative_path: str = Field(min_length=1, max_length=1000)
    auth_mode: Literal["inherit", "custom", "none"] = "inherit"
    definition: ToolInput
    revision: int | None = Field(default=None, ge=1)


class StateInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    revision: int = Field(ge=1)
    enabled: bool | None = None
    archived: bool | None = None


def endpoint(base, path):
    decoded = unquote(path)
    if (
        not path.startswith("/")
        or decoded.startswith("//")
        or any(ord(c) < 32 for c in decoded)
        or any(c in decoded for c in "?#\\")
        or any(p in {".", ".."} for p in decoded.split("/"))
    ):
        raise DomainError("invalid_tool_relative_path", 422)
    return base.rstrip("/") + path


def fingerprint(group, row):
    if not row["workspace_managed"]:
        return None
    spec = group["spec"]
    value = {"url": spec["url"], "headers": spec.get("headers", {})}
    if row["auth_mode"] == "inherit":
        value.update(
            {k: spec.get(k) for k in ("credential_id", "stored_headers", "headers_from_env")}
        )
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


class ToolWorkspace:
    def __init__(self, registry, client):
        self.registry, self.engine, self.client = registry, registry.engine, client
        self.test_limit = asyncio.Semaphore(registry.settings.tool_concurrency)

    async def group(self, c, tenant, gid, lock=False):
        return required(
            await one(
                c,
                "SELECT * FROM tool_collections WHERE tenant_id=:t AND id=:id"
                + (" FOR UPDATE" if lock else ""),
                t=tenant,
                id=gid,
            ),
            "tool_collection_not_found",
        )

    def validate(self, spec):
        if not self.registry.settings.allows_tool_host(urlsplit(spec.url).hostname):
            raise DomainError("tool_host_not_allowed")
        if any(not n.startswith("AGENT_TOOL_SECRET_") for n in spec.headers_from_env.values()):
            raise DomainError("tool_secret_reference_not_allowed")

    async def references(self, c, tenant, gid, tid=None):
        return await many(
            c,
            """SELECT DISTINCT a.id agent_id,a.name agent_name,v.version,
            (a.published_version=v.version AND NOT a.archived) is_current,r.id tool_id,r.display_name,r.name tool_name
            FROM agents a JOIN agent_versions v ON v.agent_id=a.id
            JOIN registered_tools r ON r.tenant_id=a.tenant_id AND (v.config->'tool_names') ? r.name
            WHERE a.tenant_id=:t AND r.collection_id=:g AND (CAST(:tool AS uuid) IS NULL OR r.id=:tool)
            ORDER BY a.name,v.version DESC""",
            t=tenant,
            g=gid,
            tool=tid,
        )

    async def catalog(self, tenant, q="", archived=False, page=1, page_size=12):
        async with self.engine.connect() as c:
            where = "tenant_id=:t AND archived=:archived AND (:q='' OR name ILIKE :query)"
            params = {"t": tenant, "archived": archived, "q": q, "query": "%" + q + "%"}
            total = (
                await one(c, "SELECT count(*) n FROM tool_collections WHERE " + where, **params)
            )["n"]
            rows = await many(
                c,
                "SELECT * FROM tool_collections WHERE "
                + where
                + " ORDER BY updated_at DESC,id LIMIT :limit OFFSET :offset",
                **params,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
            for g in rows:
                apis = await many(
                    c,
                    "SELECT * FROM registered_tools WHERE collection_id=:id AND tenant_id=:t AND NOT archived",
                    id=g["id"],
                    t=tenant,
                )
                refs = await self.references(c, tenant, g["id"])
                g["api_count"] = len(apis)
                g["reference_count"] = len({r["agent_id"] for r in refs if r["is_current"]})
                g["status"] = (
                    "unpublished"
                    if not any(a["published_version"] for a in apis)
                    else "changed"
                    if any(self.changed(g, a) for a in apis)
                    else "published"
                )
            return {"items": rows, "total": total, "page": page, "page_size": page_size}

    @staticmethod
    def changed(g, row):
        return (
            not row["published_version"]
            or row["revision"] != row["published_revision"]
            or fingerprint(g, row) != row["collection_fingerprint"]
        )

    async def save_group(self, tenant, actor, body, gid=None, connection=None):
        from agent_platform.platform.persistence.store import transaction

        definition = body.definition()
        self.validate(definition)
        async with transaction(self.engine, connection) as c:
            old = await self.group(c, tenant, gid, True) if gid else None
            if old and (old["revision"] != body.revision or old["archived"]):
                raise DomainError("tool_revision_conflict_or_archived", 409)
            if not old:
                gid = uuid4()
                await execute(
                    c,
                    "INSERT INTO tool_collections(id,tenant_id,name,spec) VALUES(:id,:t,:name,'{}')",
                    id=gid,
                    t=tenant,
                    name=body.name.strip(),
                )
            spec = await self.registry.save_credentials(
                c, tenant, gid, definition, old["spec"] if old else None, collection=True
            )
            row = await one(
                c,
                """UPDATE tool_collections SET name=:name,description=:description,icon=:icon,spec=CAST(:spec AS jsonb),
                revision=revision+:increment,updated_at=now() WHERE id=:id RETURNING *""",
                id=gid,
                name=body.name.strip(),
                description=body.description,
                icon=body.icon,
                spec=spec.model_dump_json(),
                increment=1 if old else 0,
            )
            await audit(c, tenant, actor, "tool_collection.saved", gid)
            return row

    async def detail(self, tenant, gid, q="", page=1, page_size=20, include_archived=False):
        async with self.engine.connect() as c:
            group = await self.group(c, tenant, gid)
            where = "tenant_id=:t AND collection_id=:g AND (:archived OR NOT archived) AND (:q='' OR display_name ILIKE :query OR name ILIKE :query)"
            params = {
                "t": tenant,
                "g": gid,
                "archived": include_archived,
                "q": q,
                "query": "%" + q + "%",
            }
            count = (
                await one(c, "SELECT count(*) n FROM registered_tools WHERE " + where, **params)
            )["n"]
            rows = await many(
                c,
                "SELECT * FROM registered_tools WHERE "
                + where
                + " ORDER BY updated_at DESC,id LIMIT :limit OFFSET :offset",
                **params,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
            refs = await self.references(c, tenant, gid)
            for row in rows:
                row["changed"] = self.changed(group, row)
                row["collection_changed"] = fingerprint(group, row) != row["collection_fingerprint"]
                row["reference_count"] = len(
                    {r["agent_id"] for r in refs if r["is_current"] and r["tool_id"] == row["id"]}
                )
            return {
                "collection": group,
                "items": rows,
                "total": count,
                "page": page,
                "page_size": page_size,
            }

    async def save_api(self, tenant, actor, gid, body, tid=None, connection=None):
        from agent_platform.platform.persistence.store import transaction

        async with transaction(self.engine, connection) as c:
            group = await self.group(c, tenant, gid, True)
            if group["archived"]:
                raise DomainError("tool_collection_archived", 409)
            old = (
                await one(
                    c,
                    "SELECT * FROM registered_tools WHERE tenant_id=:t AND collection_id=:g AND id=:id FOR UPDATE",
                    t=tenant,
                    g=gid,
                    id=tid,
                )
                if tid
                else None
            )
            if tid and (not old or old["revision"] != body.revision or old["archived"]):
                raise DomainError("tool_revision_conflict_or_archived", 409)
            definition = body.definition.model_copy(
                update={"url": endpoint(group["spec"]["url"], body.relative_path)}
            )
            definition = ToolInput.model_validate(definition.model_dump())
            self.validate(definition)
            if old and definition.name != old["name"]:
                raise DomainError("tool_name_is_immutable", 409)
            if definition.name in self.registry.file_specs or definition.name in {
                "knowledge_search",
                "propose_ticket",
                "ticket_lookup",
            }:
                raise DomainError("reserved_tool_name")
            if body.auth_mode != "custom":
                definition = definition.model_copy(
                    update={"headers_from_env": {}, "header_secrets": {}}
                )
            if not old:
                tid = uuid4()
                inserted = await one(
                    c,
                    """INSERT INTO registered_tools(id,tenant_id,name,spec,collection_id,published_version)
                    VALUES(:id,:t,:name,'{}',:g,NULL) ON CONFLICT(tenant_id,name) DO NOTHING RETURNING id""",
                    id=tid,
                    t=tenant,
                    name=definition.name,
                    g=gid,
                )
                if not inserted:
                    raise DomainError("tool_name_exists", 409)
            spec = await self.registry.save_credentials(
                c, tenant, tid, definition, old["spec"] if old else None
            )
            row = await one(
                c,
                """UPDATE registered_tools SET spec=CAST(:spec AS jsonb),display_name=:display,relative_path=:path,
                auth_mode=:auth,workspace_managed=true,revision=revision+:inc,updated_at=now() WHERE id=:id RETURNING *""",
                id=tid,
                spec=spec.model_dump_json(),
                display=body.display_name,
                path=body.relative_path,
                auth=body.auth_mode,
                inc=1 if old else 0,
            )
            await audit(c, tenant, actor, "tool.workspace_saved", tid)
            return row

    def composed(self, group, row):
        spec = HttpTool.model_validate(row["spec"])
        if not row["workspace_managed"]:
            return spec
        values = spec.model_dump()
        values["url"] = endpoint(group["spec"]["url"], row["relative_path"])
        # Case-insensitive API public headers override the corresponding group public header.
        combined = {k.lower(): (k, v) for k, v in group["spec"].get("headers", {}).items()}
        combined.update({k.lower(): (k, v) for k, v in spec.headers.items()})
        values["headers"] = dict(combined.values())
        if row["auth_mode"] == "inherit":
            values["auth_kind"] = group["spec"].get("auth_kind", "none")
            values.update(
                {
                    k: group["spec"].get(
                        k, {} if k == "headers_from_env" else [] if k == "stored_headers" else None
                    )
                    for k in ("headers_from_env", "credential_id", "stored_headers")
                }
            )
        elif row["auth_mode"] == "none":
            values.update(
                auth_kind="none", headers_from_env={}, credential_id=None, stored_headers=[]
            )
        if row["auth_mode"] == "custom" and spec.credential_id and spec.url != values["url"]:
            raise DomainError("tool_credential_reentry_required", 409)
        return HttpTool.model_validate(values)

    async def preview(self, tenant, tid):
        async with self.engine.connect() as c:
            row = required(
                await one(
                    c,
                    "SELECT * FROM registered_tools WHERE tenant_id=:t AND id=:id",
                    t=tenant,
                    id=tid,
                )
            )
            group = await self.group(c, tenant, row["collection_id"])
            spec = self.composed(group, row).model_dump(mode="json")
            previous = await one(
                c,
                "SELECT spec FROM tool_versions WHERE tool_id=:id AND version=:v",
                id=tid,
                v=row["published_version"],
            )
            old = previous["spec"] if previous else {}

            def safe(s):
                return {k: v for k, v in s.items() if k not in {"credential_id"}}

            keys = set(old) | set(spec)
            changes = [
                {"field": k, "before": safe(old).get(k), "after": safe(spec).get(k)}
                for k in sorted(keys)
                if k != "credential_id" and old.get(k) != spec.get(k)
            ]
            inherited_unchanged = (
                row["workspace_managed"]
                and row["auth_mode"] == "inherit"
                and row["published_version"] is not None
                and fingerprint(group, row) == row["collection_fingerprint"]
                and bool(old.get("credential_id")) == bool(spec.get("credential_id"))
            )
            if not inherited_unchanged and old.get("credential_id") != spec.get("credential_id"):
                changes.append(
                    {
                        "field": "credentials",
                        "before": "configured" if old.get("credential_id") else "none",
                        "after": "configured" if spec.get("credential_id") else "none",
                    }
                )
            return {
                "revision": row["revision"],
                "collection_revision": group["revision"],
                "definition": safe(spec),
                "changes": changes,
                "references": await self.references(c, tenant, group["id"], tid),
            }

    async def publish(self, tenant, actor, tid, revision, collection_revision=None):
        async with self.engine.begin() as c:
            lookup = required(
                await one(
                    c,
                    "SELECT collection_id FROM registered_tools WHERE tenant_id=:t AND id=:id",
                    t=tenant,
                    id=tid,
                )
            )
            group = await self.group(c, tenant, lookup["collection_id"], True)
            row = required(
                await one(
                    c,
                    "SELECT * FROM registered_tools WHERE tenant_id=:t AND id=:id FOR UPDATE",
                    t=tenant,
                    id=tid,
                )
            )
            if (
                row["revision"] != revision
                or row["archived"]
                or not row["enabled"]
                or not group["enabled"]
                or group["archived"]
                or (collection_revision is not None and group["revision"] != collection_revision)
            ):
                raise DomainError("tool_revision_conflict_or_disabled", 409)
            spec = self.composed(group, row)
            self.validate(spec)
            if row["workspace_managed"] and row["auth_mode"] == "inherit" and spec.credential_id:
                headers = await self.registry.credential_headers(
                    tenant, HttpTool.model_validate(group["spec"]), c
                )
                body = ToolInput.model_validate({**spec.model_dump(), "header_secrets": headers})
                spec = await self.registry.save_credentials(c, tenant, tid, body)
            elif spec.credential_id:
                await self.registry.credential_headers(tenant, spec, c)
            version = (
                await one(
                    c,
                    "SELECT COALESCE(max(version),0)+1 n FROM tool_versions WHERE tool_id=:id",
                    id=tid,
                )
            )["n"]
            await execute(
                c,
                "INSERT INTO tool_versions(tool_id,version,spec) VALUES(:id,:v,CAST(:spec AS jsonb))",
                id=tid,
                v=version,
                spec=spec.model_dump_json(),
            )
            await execute(
                c,
                "UPDATE registered_tools SET published_version=:v,published_revision=revision,collection_fingerprint=:fp,updated_at=now() WHERE id=:id",
                id=tid,
                v=version,
                fp=fingerprint(group, row),
            )
            await audit(c, tenant, actor, "tool.published", tid, version=version)
            return {"tool_id": tid, "version": version}

    async def state(self, tenant, actor, gid, body, tid=None):
        async with self.engine.begin() as c:
            group = await self.group(c, tenant, gid, True)
            row = (
                required(
                    await one(
                        c,
                        "SELECT * FROM registered_tools WHERE id=:id AND tenant_id=:t AND collection_id=:g FOR UPDATE",
                        id=tid,
                        t=tenant,
                        g=gid,
                    )
                )
                if tid
                else group
            )
            if row["revision"] != body.revision:
                raise DomainError("tool_revision_conflict_or_archived", 409)
            table = "registered_tools" if tid else "tool_collections"
            result = await one(
                c,
                f"UPDATE {table} SET enabled=COALESCE(:enabled,enabled),archived=COALESCE(:archived,archived),revision=revision+1,updated_at=now() WHERE id=:id RETURNING *",
                id=tid or gid,
                enabled=body.enabled,
                archived=body.archived,
            )
            await audit(
                c,
                tenant,
                actor,
                "tool.state_changed" if tid else "tool_collection.state_changed",
                tid or gid,
                enabled=body.enabled,
                archived=body.archived,
            )
            return result

    async def delete(self, tenant, actor, gid, revision, tid=None):
        async with self.engine.begin() as c:
            group = await self.group(c, tenant, gid, True)
            rows = await many(
                c,
                "SELECT * FROM registered_tools WHERE collection_id=:g AND tenant_id=:t AND (CAST(:tool AS uuid) IS NULL OR id=:tool) FOR UPDATE",
                g=gid,
                t=tenant,
                tool=tid,
            )
            row = required(rows[0] if rows else None) if tid else group
            if row["revision"] != revision:
                raise DomainError("tool_revision_conflict_or_archived", 409)
            if any(r["published_version"] for r in rows):
                raise DomainError("published_tools_archive_instead", 409)
            for r in rows:
                if await one(
                    c,
                    "SELECT id FROM agents WHERE tenant_id=:t AND (draft->'tool_names') ? :name LIMIT 1",
                    t=tenant,
                    name=r["name"],
                ):
                    raise DomainError("tool_has_agent_references", 409)
            await execute(
                c,
                "DELETE FROM registered_tools WHERE collection_id=:g AND tenant_id=:t AND (CAST(:tool AS uuid) IS NULL OR id=:tool)",
                g=gid,
                t=tenant,
                tool=tid,
            )
            if not tid:
                await execute(
                    c, "DELETE FROM tool_collections WHERE id=:g AND tenant_id=:t", g=gid, t=tenant
                )
            await audit(
                c, tenant, actor, "tool.deleted" if tid else "tool_collection.deleted", tid or gid
            )
            return {"deleted": True}

    async def copy(self, tenant, actor, gid, body):
        # One transaction: duplicate names or invalid definitions cannot leave a partial copy.
        async with self.engine.begin() as c:
            source = await self.group(c, tenant, gid, True)
            new_id = uuid4()
            spec = {
                **source["spec"],
                "credential_id": None,
                "stored_headers": [],
                "headers_from_env": {},
            }
            await execute(
                c,
                "INSERT INTO tool_collections(id,tenant_id,name,description,icon,spec) VALUES(:id,:t,:name,:description,:icon,CAST(:spec AS jsonb))",
                id=new_id,
                t=tenant,
                name=body,
                description=source["description"],
                icon=source["icon"],
                spec=json.dumps(spec),
            )
            rows = await many(
                c,
                "SELECT * FROM registered_tools WHERE collection_id=:g AND tenant_id=:t AND NOT archived",
                g=gid,
                t=tenant,
            )
            for row in rows:
                clone = {
                    **row["spec"],
                    "name": row["name"][:48] + "_" + uuid4().hex[:10],
                    "credential_id": None,
                    "stored_headers": [],
                    "headers_from_env": {},
                }
                await self.save_api(
                    tenant,
                    actor,
                    new_id,
                    ApiInput(
                        display_name=row["display_name"] or row["name"],
                        relative_path=row["relative_path"],
                        auth_mode="none",
                        definition=ToolInput.model_validate(clone),
                    ),
                    connection=c,
                )
            await audit(c, tenant, actor, "tool_collection.copied", new_id, source=str(gid))
            return await self.group(c, tenant, new_id)

    async def history(self, tenant, gid, tool=None, page=1, page_size=20):
        async with self.engine.connect() as c:
            await self.group(c, tenant, gid)
            where = "r.tenant_id=:t AND r.collection_id=:g AND (CAST(:tool AS uuid) IS NULL OR r.id=:tool)"
            params = {"t": tenant, "g": gid, "tool": tool}
            total = (
                await one(
                    c,
                    "SELECT count(*) n FROM tool_versions v JOIN registered_tools r ON r.id=v.tool_id WHERE "
                    + where,
                    **params,
                )
            )["n"]
            rows = await many(
                c,
                "SELECT v.*,r.name,r.display_name FROM tool_versions v JOIN registered_tools r ON r.id=v.tool_id WHERE "
                + where
                + " ORDER BY v.created_at DESC,v.tool_id,v.version DESC LIMIT :limit OFFSET :offset",
                **params,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
            for row in rows:
                previous = await one(
                    c,
                    "SELECT spec FROM tool_versions WHERE tool_id=:id AND version=:v",
                    id=row["tool_id"],
                    v=row["version"] - 1,
                )
                old = previous["spec"] if previous else {}
                row["changes"] = [
                    {"field": k, "before": old.get(k), "after": row["spec"].get(k)}
                    for k in sorted(set(old) | set(row["spec"]))
                    if k != "credential_id" and old.get(k) != row["spec"].get(k)
                ]
                if old.get("credential_id") != row["spec"].get("credential_id"):
                    row["changes"].append(
                        {
                            "field": "credentials",
                            "before": "configured" if old.get("credential_id") else "none",
                            "after": "configured" if row["spec"].get("credential_id") else "none",
                        }
                    )
                row["spec"].pop("credential_id", None)
            return {"items": rows, "total": total, "page": page, "page_size": page_size}

    async def calls(
        self, tenant, gid, name="", status="", since=None, until=None, page=1, page_size=20
    ):
        async with self.engine.connect() as c:
            await self.group(c, tenant, gid)
            where = """c.tenant_id=:t AND r.collection_id=:g AND (:name='' OR c.tool_name=:name) AND (:status='' OR c.status=:status)
                AND (CAST(:since AS timestamptz) IS NULL OR c.created_at>=:since) AND (CAST(:until AS timestamptz) IS NULL OR c.created_at<=:until)"""
            params = {
                "t": tenant,
                "g": gid,
                "name": name,
                "status": status,
                "since": since,
                "until": until,
            }
            join = " FROM tool_calls c JOIN registered_tools r ON r.tenant_id=c.tenant_id AND r.name=c.tool_name WHERE "
            total = (await one(c, "SELECT count(*) n" + join + where, **params))["n"]
            rows = await many(
                c,
                "SELECT c.*,r.display_name"
                + join
                + where
                + " ORDER BY c.created_at DESC,c.id DESC LIMIT :limit OFFSET :offset",
                **params,
                limit=page_size,
                offset=(page - 1) * page_size,
            )
            return {"items": rows, "total": total, "page": page, "page_size": page_size}
