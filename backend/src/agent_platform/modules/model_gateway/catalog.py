import json
import os
from urllib.parse import urlsplit
from uuid import uuid4

from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
    transaction,
)

from .contracts import Connection, Model, Profile
from .credentials import CredentialVault
from .thinking import provider_parameters

SCHEMAS = {"connections": Connection, "models": Model, "profiles": Profile}


class Catalog:
    def __init__(self, engine, settings):
        self.engine, self.settings = engine, settings
        self.vault = CredentialVault(settings.model_credential_key_file)

    async def annotate_credentials(self, tenant, rows, connection=None):
        async with transaction(self.engine, connection) as c:
            stored = {
                str(row["connection_id"])
                for row in await many(
                    c, "SELECT connection_id FROM gateway_credentials WHERE tenant_id=:t", t=tenant
                )
            }
        for row in rows:
            direct = str(row["id"]) in stored
            ref = row["spec"].get("credential_ref")
            row["credential_source"] = "stored" if direct else "environment" if ref else "none"
            row["credential_configured"] = direct or bool(ref and os.environ.get(ref))
        return rows

    async def credential_for(self, tenant, identifier, spec):
        async with self.engine.connect() as c:
            row = await self.get(c, tenant, "connections", identifier, True)
            secret = await one(
                c,
                "SELECT encrypted_secret FROM gateway_credentials WHERE tenant_id=:t AND connection_id=:id",
                t=tenant,
                id=identifier,
            )
        # Never send a rotated secret to the old address of a published snapshot.
        if (row["spec"]["base_url"], row["spec"]["protocol"]) != (
            spec["base_url"],
            spec["protocol"],
        ):
            raise DomainError("model_credential_binding_mismatch", 409)
        if secret:
            return self.vault.decrypt(secret["encrypted_secret"], tenant, identifier, spec)
        return self.credential(row["spec"])

    def validate_address(self, spec):
        if spec["protocol"] == "demo":
            return
        try:
            url = urlsplit(spec["base_url"])
            _ = url.port
        except ValueError:
            raise DomainError("invalid_model_address") from None
        if (
            url.scheme not in ("http", "https")
            or not url.hostname
            or url.hostname.lower() not in self.settings.model_gateway_allowed_hosts
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise DomainError("model_address_not_allowed")
        if url.scheme == "http" and url.hostname not in ("localhost", "127.0.0.1", "::1"):
            raise DomainError("model_connection_requires_https")

    def credential(self, spec):
        ref = spec.get("credential_ref")
        if not ref:
            return None
        value = os.environ.get(ref)
        if not value:
            raise DomainError("model_credential_unavailable", 503)
        return value

    async def list(self, tenant, kind):
        async with self.engine.connect() as c:
            rows = await many(
                c,
                f"SELECT * FROM gateway_{kind} WHERE tenant_id=:t AND NOT archived ORDER BY created_at DESC LIMIT 200",
                t=tenant,
            )
        if kind == "connections":
            await self.annotate_credentials(tenant, rows)
        return rows

    async def get(self, c, tenant, kind, identifier, enabled=False):
        row = required(
            await one(
                c,
                f"SELECT * FROM gateway_{kind} WHERE id=:id AND tenant_id=:t AND NOT archived",
                id=identifier,
                t=tenant,
            ),
            "model_resource_not_found",
        )
        if enabled and not row["enabled"]:
            raise DomainError("model_resource_disabled", 409)
        return row

    async def validate(self, c, tenant, kind, spec):
        if kind == "connections":
            self.validate_address(spec)
        if kind == "models":
            connection = await self.get(c, tenant, "connections", spec["connection_id"], True)
            if connection["spec"]["protocol"] == "openai_compatible" and set(spec["operations"]) - {
                "chat",
                "embed",
                "rerank",
                "transcribe",
                "synthesize",
            }:
                raise DomainError("protocol_operation_not_supported")
        if kind == "profiles":
            await self.materialize(c, tenant, spec)

    async def save(self, tenant, actor, kind, body, identifier=None, revision=None):
        spec = body.model_dump(mode="json")
        async with self.engine.begin() as c:
            await execute(
                c,
                "SELECT pg_advisory_xact_lock(hashtextextended(:s,9120))",
                s=f"{tenant}:governance",
            )
            if kind == "profiles" and identifier is not None:
                previous = await self.get(c, tenant, kind, identifier)
                if previous["spec"]["operation"] != spec["operation"]:
                    raise DomainError("profile_operation_is_immutable")
            await self.validate(c, tenant, kind, spec)
            if kind == "connections":
                if body.api_key is not None and (body.clear_api_key or body.credential_ref):
                    raise DomainError("model_credential_conflict", 422)
                if body.api_key is not None:
                    value = body.api_key.get_secret_value()
                    if not value.strip() or any(ch in value for ch in "\r\n"):
                        raise DomainError("invalid_model_credential", 422)
                if (
                    identifier is not None
                    and body.api_key is None
                    and not body.clear_api_key
                    and not body.credential_ref
                ):
                    previous = await self.get(c, tenant, kind, identifier)
                    stored = await one(
                        c,
                        "SELECT connection_id FROM gateway_credentials WHERE tenant_id=:t AND connection_id=:id",
                        t=tenant,
                        id=identifier,
                    )
                    if stored and any(
                        previous["spec"][key] != spec[key] for key in ("base_url", "protocol")
                    ):
                        raise DomainError("model_credential_reentry_required", 409)
            if identifier is None:
                row = await one(
                    c,
                    f"INSERT INTO gateway_{kind}(id,tenant_id,name,spec) VALUES(:id,:t,:name,CAST(:spec AS jsonb)) RETURNING *",
                    id=uuid4(),
                    t=tenant,
                    name=body.name,
                    spec=json.dumps(spec),
                )
            else:
                row = await one(
                    c,
                    f"UPDATE gateway_{kind} SET name=:name,spec=CAST(:spec AS jsonb),revision=revision+1,updated_at=now() WHERE id=:id AND tenant_id=:t AND revision=:r AND NOT archived RETURNING *",
                    id=identifier,
                    t=tenant,
                    r=revision,
                    name=body.name,
                    spec=json.dumps(spec),
                )
                if not row:
                    raise DomainError("model_revision_conflict", 409)
            if kind == "connections":
                if body.api_key is not None:
                    existing = await one(c, "SELECT (SELECT count(*) FROM gateway_credentials) + (SELECT count(*) FROM tool_credentials) n")
                    encrypted = self.vault.encrypt(
                        body.api_key.get_secret_value(),
                        tenant,
                        row["id"],
                        spec,
                        create=existing["n"] == 0,
                    )
                    await execute(
                        c,
                        """INSERT INTO gateway_credentials(connection_id,tenant_id,encrypted_secret)
                        VALUES(:id,:t,:secret) ON CONFLICT(connection_id) DO UPDATE
                        SET encrypted_secret=EXCLUDED.encrypted_secret,updated_at=now()""",
                        id=row["id"],
                        t=tenant,
                        secret=encrypted,
                    )
                elif body.clear_api_key or body.credential_ref:
                    await execute(
                        c,
                        "DELETE FROM gateway_credentials WHERE tenant_id=:t AND connection_id=:id",
                        t=tenant,
                        id=row["id"],
                    )
                await self.annotate_credentials(tenant, [row], c)
            await audit(
                c, tenant, actor, f"model.{kind}.saved", row["id"], revision=row["revision"]
            )
            return row

    async def toggle(self, tenant, actor, kind, identifier, body):
        async with self.engine.begin() as c:
            row = await one(
                c,
                f"UPDATE gateway_{kind} SET enabled=:enabled,revision=revision+1,updated_at=now() WHERE id=:id AND tenant_id=:t AND revision=:r AND NOT archived RETURNING *",
                enabled=body.enabled,
                id=identifier,
                t=tenant,
                r=body.revision,
            )
            if not row:
                raise DomainError("model_revision_conflict", 409)
            await audit(c, tenant, actor, f"model.{kind}.enabled", identifier, enabled=body.enabled)
            return row

    async def materialize(self, c, tenant, spec):
        routes = []
        for mid in [spec["model_id"], *spec["fallback_model_ids"]]:
            model = await self.get(c, tenant, "models", mid, True)
            ms = model["spec"]
            connection = await self.get(c, tenant, "connections", ms["connection_id"], True)
            if spec["operation"] not in ms["operations"] or (
                spec["require_tools"] and not ms["tool_calling"]
            ):
                raise DomainError("model_capability_mismatch")
            self.validate_address(connection["spec"])
            provider_parameters({"connection": connection["spec"], "model": ms}, spec["parameters"])
            # Credentials are resolved at call time, so rotation does not alter profile versions.
            routes.append(
                {
                    "model_id": mid,
                    "model": ms,
                    "connection_id": str(connection["id"]),
                    "connection": connection["spec"],
                }
            )
        if spec["operation"] == "embed":
            primary = routes[0]["model"]
            if any(
                (r["model"]["vector_space"], r["model"]["embedding_dimension"])
                != (primary["vector_space"], primary["embedding_dimension"])
                for r in routes
            ):
                raise DomainError("embedding_space_mismatch")
        return {"spec": spec, "routes": routes}

    async def publish(self, tenant, actor, identifier, revision):
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    "SELECT * FROM gateway_profiles WHERE id=:id AND tenant_id=:t AND NOT archived FOR UPDATE",
                    id=identifier,
                    t=tenant,
                )
            )
            if row["revision"] != revision or not row["enabled"]:
                raise DomainError("model_revision_conflict_or_disabled", 409)
            snapshot = await self.materialize(c, tenant, row["spec"])
            version = (
                await one(
                    c,
                    "SELECT COALESCE(max(version),0)+1 n FROM gateway_profile_versions WHERE profile_id=:id",
                    id=identifier,
                )
            )["n"]
            snapshot.update(profile_id=str(identifier), version=version)
            await execute(
                c,
                "INSERT INTO gateway_profile_versions(profile_id,version,snapshot) VALUES(:id,:v,CAST(:s AS jsonb))",
                id=identifier,
                v=version,
                s=json.dumps(snapshot),
            )
            await execute(
                c,
                "UPDATE gateway_profiles SET published_version=:v,updated_at=now() WHERE id=:id",
                id=identifier,
                v=version,
            )
            await audit(c, tenant, actor, "model.profile.published", identifier, version=version)
            return {"profile_id": identifier, "version": version}

    async def resolve(self, tenant, identifier, version, connection=None):
        async with transaction(self.engine, connection) as c:
            await self.get(c, tenant, "profiles", identifier, True)
            row = required(
                await one(
                    c,
                    "SELECT snapshot FROM gateway_profile_versions WHERE profile_id=:id AND version=:v",
                    id=identifier,
                    v=version,
                ),
                "model_profile_version_not_found",
            )
            return row["snapshot"]

    async def versions(self, tenant, identifier):
        async with self.engine.connect() as c:
            await self.get(c, tenant, "profiles", identifier)
            return await many(
                c,
                "SELECT version,snapshot,created_at FROM gateway_profile_versions WHERE profile_id=:id ORDER BY version DESC",
                id=identifier,
            )

    async def rollback(self, tenant, actor, identifier, version):
        async with self.engine.begin() as c:
            await self.resolve(tenant, identifier, version, c)
            await execute(
                c,
                "UPDATE gateway_profiles SET published_version=:v,updated_at=now() WHERE id=:id",
                id=identifier,
                v=version,
            )
            await audit(c, tenant, actor, "model.profile.rolled_back", identifier, version=version)
        return {"profile_id": identifier, "version": version}
