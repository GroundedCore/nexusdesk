import json
from urllib.parse import urlsplit
from uuid import uuid4

from agent_platform.modules.tool_gateway.service import HttpTool
from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
    transaction,
)
from agent_platform.platform.secrets.vault import CredentialVault

BUILTINS = {"knowledge_search", "propose_ticket", "ticket_lookup"}


class ToolRegistry:
    def __init__(self, engine, settings, file_specs):
        self.engine, self.settings = engine, settings
        self.file_specs = {s.name: s for s in file_specs}
        self.vault = CredentialVault(settings.model_credential_key_file)

    @staticmethod
    def credential_binding(spec):
        return {"base_url": spec.url, "protocol": "tool-http-headers"}

    async def credential_headers(self, tenant, spec, connection=None):
        if not spec.credential_id:
            return {}
        async with transaction(self.engine, connection) as c:
            row = await one(
                c,
                "SELECT * FROM tool_credentials WHERE id=:id AND tenant_id=:t",
                id=spec.credential_id,
                t=tenant,
            )
        if not row:
            raise DomainError("tool_credential_unavailable", 503)
        try:
            value = self.vault.decrypt(
                row["encrypted_secret"],
                tenant,
                row["tool_id"] or row.get("collection_id"),
                self.credential_binding(spec),
            )
        except DomainError as exc:
            raise DomainError(exc.code.replace("model_", "tool_"), exc.status) from None
        return json.loads(value)

    async def save_credentials(self, c, tenant, tid, body, previous=None, collection=False):
        # Never trust credential identifiers supplied by the caller.
        spec = HttpTool.model_validate(
            body.model_dump(exclude={"header_secrets", "credential_id", "stored_headers"})
        )
        previous = HttpTool.model_validate(previous) if previous else None
        submitted = getattr(body, "header_secrets", None)
        if submitted is None:
            if previous and previous.credential_id:
                if previous.url != spec.url:
                    raise DomainError("tool_credential_reentry_required", 409)
                spec.credential_id = previous.credential_id
                spec.stored_headers = previous.stored_headers
        elif submitted:
            old = {}
            if any(value is None for value in submitted.values()):
                if not previous or not previous.credential_id or previous.url != spec.url:
                    raise DomainError("tool_credential_reentry_required", 409)
                old = {
                    name.lower(): value
                    for name, value in (await self.credential_headers(tenant, previous, c)).items()
                }
            headers = {}
            for name, value in submitted.items():
                if value is None and name.lower() not in old:
                    raise DomainError("tool_credential_reentry_required", 409)
                headers[name] = old[name.lower()] if value is None else value.get_secret_value()
            existing = await one(
                c,
                "SELECT (SELECT count(*) FROM tool_credentials) + (SELECT count(*) FROM gateway_credentials) n",
            )
            try:
                encrypted = self.vault.encrypt(
                    json.dumps(headers),
                    tenant,
                    tid,
                    self.credential_binding(spec),
                    create=existing["n"] == 0,
                )
            except DomainError as exc:
                raise DomainError(exc.code.replace("model_", "tool_"), exc.status) from None
            spec.credential_id = uuid4()
            spec.stored_headers = list(headers)
            await execute(
                c,
                "INSERT INTO tool_credentials(id,tool_id,collection_id,tenant_id,encrypted_secret) VALUES(:id,:tool,:collection,:t,:secret)",
                id=spec.credential_id,
                tool=None if collection else tid,
                collection=tid if collection else None,
                t=tenant,
                secret=encrypted,
            )
        if {name.lower() for name in spec.stored_headers} & {
            name.lower() for name in spec.headers_from_env
        }:
            raise DomainError("duplicate_tool_header", 422)
        return spec

    async def default_collection(self, c, tenant, url):
        parsed = urlsplit(url)
        origin = f"{parsed.scheme}://{parsed.netloc}"
        spec = HttpTool(
            name="collection",
            description="Collection",
            url=origin,
            parameters={"type": "object", "properties": {}, "additionalProperties": False},
        )
        return await one(
            c,
            """INSERT INTO tool_collections(id,tenant_id,name,spec,auto_origin)
            VALUES(:id,:t,:origin,CAST(:spec AS jsonb),:origin)
            ON CONFLICT(tenant_id,auto_origin) DO UPDATE SET auto_origin=EXCLUDED.auto_origin RETURNING *""",
            id=uuid4(),
            t=tenant,
            origin=origin,
            spec=spec.model_dump_json(),
        )

    async def list(self, tenant):
        async with self.engine.connect() as c:
            rows = await many(
                c,
                "SELECT t.*, (t.enabled AND NOT t.archived AND "
                "(g.id IS NULL OR (g.enabled AND NOT g.archived))) AS enabled "
                "FROM registered_tools t LEFT JOIN tool_collections g ON g.id=t.collection_id "
                "WHERE t.tenant_id=:t ORDER BY t.name",
                t=tenant,
            )
        return (
            [{**r, "source": "database"} for r in rows]
            + [
                {
                    "id": s.name,
                    "name": s.name,
                    "enabled": True,
                    "revision": 0,
                    "source": "configuration",
                    "spec": s.model_dump(),
                }
                for s in self.file_specs.values()
            ]
            + [
                {
                    "id": n,
                    "name": n,
                    "enabled": True,
                    "revision": 0,
                    "source": "builtin",
                    "spec": {},
                }
                for n in sorted(BUILTINS)
            ]
        )

    async def create(self, tenant, actor, spec):
        if spec.name in BUILTINS or spec.name in self.file_specs:
            raise DomainError("reserved_tool_name")
        if not self.settings.allows_tool_host(urlsplit(spec.url).hostname):
            raise DomainError("tool_host_not_allowed")
        if any(
            not name.startswith("AGENT_TOOL_SECRET_") for name in spec.headers_from_env.values()
        ):
            raise DomainError("tool_secret_reference_not_allowed")
        async with self.engine.begin() as c:
            group = await self.default_collection(c, tenant, spec.url)
            row = await one(
                c,
                """INSERT INTO registered_tools(id,tenant_id,name,spec,collection_id,display_name,relative_path,published_version,published_revision)
                VALUES(:id,:t,:n,CAST(:s AS jsonb),:group,:n,:path,1,1) ON CONFLICT(tenant_id,name) DO NOTHING RETURNING *""",
                group=group["id"],
                path=urlsplit(spec.url).path or "/",
                id=uuid4(),
                t=tenant,
                n=spec.name,
                s=HttpTool.model_validate(
                    spec.model_dump(exclude={"header_secrets", "credential_id", "stored_headers"})
                ).model_dump_json(),
            )
            if not row:
                raise DomainError("tool_name_exists", 409)
            spec = await self.save_credentials(c, tenant, row["id"], spec)
            row = await one(
                c,
                "UPDATE registered_tools SET spec=CAST(:s AS jsonb) WHERE id=:id RETURNING *",
                id=row["id"],
                s=spec.model_dump_json(),
            )
            await execute(
                c,
                "INSERT INTO tool_versions(tool_id,version,spec) VALUES(:id,1,CAST(:s AS jsonb))",
                id=row["id"],
                s=spec.model_dump_json(),
            )
            await audit(c, tenant, actor, "tool.registered", row["id"], name=spec.name)
            return row

    async def update(self, tenant, actor, tid, spec, revision):
        if not self.settings.allows_tool_host(urlsplit(spec.url).hostname):
            raise DomainError("tool_host_not_allowed")
        if any(not n.startswith("AGENT_TOOL_SECRET_") for n in spec.headers_from_env.values()):
            raise DomainError("tool_secret_reference_not_allowed")
        async with self.engine.begin() as c:
            previous = await one(
                c,
                "SELECT * FROM registered_tools WHERE id=:id AND tenant_id=:t FOR UPDATE",
                id=tid,
                t=tenant,
            )
            if not previous or previous["revision"] != revision or previous["name"] != spec.name:
                raise DomainError("tool_revision_conflict_or_name_changed", 409)
            if previous.get("workspace_managed"):
                raise DomainError("use_tool_workspace_editor", 409)
            spec = await self.save_credentials(c, tenant, tid, spec, previous["spec"])
            row = await one(
                c,
                "UPDATE registered_tools SET spec=CAST(:s AS jsonb),revision=revision+1 WHERE id=:id AND tenant_id=:t AND name=:n AND revision=:r RETURNING *",
                id=tid,
                t=tenant,
                n=spec.name,
                r=revision,
                s=spec.model_dump_json(),
            )
            if not row:
                raise DomainError("tool_revision_conflict_or_name_changed", 409)
            await audit(c, tenant, actor, "tool.draft_saved", tid, revision=row["revision"])
            return row

    async def versions(self, tenant, tid):
        async with self.engine.connect() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM registered_tools WHERE id=:id AND tenant_id=:t",
                    id=tid,
                    t=tenant,
                )
            )
            return await many(
                c, "SELECT * FROM tool_versions WHERE tool_id=:id ORDER BY version DESC", id=tid
            )

    async def publish(self, tenant, actor, tid, revision):
        if hasattr(self, "workspace"):
            return await self.workspace.publish(tenant, actor, tid, revision)
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    "SELECT * FROM registered_tools WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=tid,
                    t=tenant,
                )
            )
            if row["revision"] != revision or not row["enabled"]:
                raise DomainError("tool_revision_conflict_or_disabled", 409)
            spec = HttpTool.model_validate(row["spec"])
            if not self.settings.allows_tool_host(urlsplit(spec.url).hostname):
                raise DomainError("tool_host_not_allowed")
            version = (
                await one(
                    c,
                    "SELECT COALESCE(max(version),0)+1 n FROM tool_versions WHERE tool_id=:id",
                    id=tid,
                )
            )["n"]
            await execute(
                c,
                "INSERT INTO tool_versions(tool_id,version,spec) VALUES(:id,:v,CAST(:s AS jsonb))",
                id=tid,
                v=version,
                s=spec.model_dump_json(),
            )
            await execute(
                c,
                "UPDATE registered_tools SET published_version=:v WHERE id=:id",
                id=tid,
                v=version,
            )
            await audit(c, tenant, actor, "tool.published", tid, version=version)
            return {"tool_id": tid, "version": version}

    async def toggle(self, tenant, actor, tid, enabled):
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    """UPDATE registered_tools SET enabled=:enabled,revision=revision+1
                WHERE id=:id AND tenant_id=:t RETURNING *""",
                    id=tid,
                    t=tenant,
                    enabled=enabled,
                )
            )
            await audit(c, tenant, actor, "tool.enabled" if enabled else "tool.disabled", tid)
            return row

    async def resolve(self, tenant, names, connection=None):
        if len(names) != len(set(names)):
            raise DomainError("duplicate_tool_names")
        result = []
        async with transaction(self.engine, connection) as c:
            for name in names:
                if name in BUILTINS:
                    continue
                if name in self.file_specs:
                    result.append(
                        {"source": "configuration", "spec": self.file_specs[name].model_dump()}
                    )
                    continue
                row = await one(
                    c,
                    "SELECT t.id,t.published_version,v.spec FROM registered_tools t JOIN tool_versions v ON v.tool_id=t.id AND v.version=t.published_version LEFT JOIN tool_collections g ON g.id=t.collection_id WHERE t.tenant_id=:t AND t.name=:n AND t.enabled AND NOT t.archived AND (g.id IS NULL OR (g.enabled AND NOT g.archived))",
                    t=tenant,
                    n=name,
                )
                if not row:
                    raise DomainError("tool_unavailable:" + name)
                HttpTool.model_validate(row["spec"])
                result.append(
                    {
                        "source": "database",
                        "id": str(row["id"]),
                        "version": row["published_version"],
                        "spec": row["spec"],
                    }
                )
        return result

    async def enabled(self, tenant, name):
        if name in BUILTINS or name in self.file_specs:
            return True
        async with self.engine.connect() as c:
            return bool(
                await one(
                    c,
                    "SELECT t.id FROM registered_tools t LEFT JOIN tool_collections g ON g.id=t.collection_id WHERE t.tenant_id=:t AND t.name=:n AND t.enabled AND NOT t.archived AND (g.id IS NULL OR (g.enabled AND NOT g.archived))",
                    t=tenant,
                    n=name,
                )
            )
