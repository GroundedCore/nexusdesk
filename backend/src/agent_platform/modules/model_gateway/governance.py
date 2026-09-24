"""Database-backed gateway management and admission controls.

No prompt or secret is persisted. Quota admission is serialized across processes.
Costs are estimates with immutable rate snapshots, never supplier invoices.
"""

import hashlib
import json
import secrets
import unicodedata
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from uuid import uuid4

from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)

from .governance_contracts import ModelSettings, Quota

KINDS = {"sensitive_words", "access_keys", "alert_rules"}
CATALOG = {"connections", "models", "profiles"}


def normalize(value):
    return unicodedata.normalize("NFKC", value).casefold()


def texts(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for child in value.values():
            yield from texts(child)
    elif isinstance(value, list):
        for child in value:
            yield from texts(child)


def estimate(pricing, usage):
    if not usage:
        return None
    units = [
        ("input_per_million", usage.get("prompt_tokens", usage.get("input_tokens")), 1000000),
        ("output_per_million", usage.get("completion_tokens", usage.get("output_tokens")), 1000000),
        ("audio_per_second", usage.get("audio_seconds"), 1),
        ("per_thousand_characters", usage.get("characters"), 1000),
        ("per_page", usage.get("pages"), 1),
    ]
    amounts = []
    for rate, quantity, divisor in units:
        if quantity is None:
            # A configured unit with no reported quantity is unknown, not zero.
            if pricing.get(rate) is not None:
                return None
            continue
        if quantity and pricing.get(rate) is None:
            return None
        amounts.append(Decimal(str(quantity)) * Decimal(str(pricing.get(rate) or 0)) / divisor)
    return sum(amounts, Decimal(0)) if amounts else None


def checked(row, code="not_found"):
    if row is None and code == "resource_revision_conflict":
        raise DomainError(code, 409)
    return required(row, code)


class Governance:
    def __init__(self, gateway):
        self.gateway = gateway
        self.engine = gateway.engine

    async def lock(self, c, tenant, scope="governance"):
        await execute(
            c, "SELECT pg_advisory_xact_lock(hashtextextended(:s, 9120))", s=f"{tenant}:{scope}"
        )

    async def page(
        self, tenant, kind, page=1, size=20, q="", enabled=None, operation=None, connection_id=None
    ):
        if kind not in KINDS | CATALOG:
            raise DomainError("unknown_resource", 404)
        where = "tenant_id=:t AND NOT archived AND (name ILIKE :q OR id::text ILIKE :q OR spec::text ILIKE :q)"
        params = {"t": tenant, "q": f"%{q}%", "limit": size, "offset": (page - 1) * size}
        if enabled is not None:
            where += " AND enabled=:enabled"
            params["enabled"] = enabled
        if operation:
            where += (
                " AND (spec->'operations' @> CAST(:op AS jsonb) OR spec->>'operation'=:operation)"
            )
            params.update(op=json.dumps([operation]), operation=operation)
        if connection_id:
            where += " AND spec->>'connection_id'=:connection_id"
            params["connection_id"] = str(connection_id)
        async with self.engine.connect() as c:
            total = (
                await one(c, f"SELECT count(*) n FROM gateway_{kind} WHERE {where}", **params)
            )["n"]
            rows = await many(
                c,
                f"SELECT * FROM gateway_{kind} WHERE {where} ORDER BY created_at DESC,id LIMIT :limit OFFSET :offset",
                **params,
            )
        for row in rows:
            row.pop("token_hash", None)
        if kind == "connections":
            await self.gateway.catalog.annotate_credentials(tenant, rows)
        return {"items": rows, "total": total, "page": page, "page_size": size}

    async def save(self, tenant, actor, kind, body, identifier=None, revision=None):
        if kind not in KINDS:
            raise DomainError("unknown_resource", 404)
        spec = body.model_dump(mode="json")
        secret = None
        async with self.engine.begin() as c:
            await self.lock(c, tenant)
            if kind == "sensitive_words":
                duplicates = await many(
                    c,
                    "SELECT id,name FROM gateway_sensitive_words WHERE tenant_id=:t AND NOT archived",
                    t=tenant,
                )
                if any(
                    normalize(r["name"]) == normalize(spec["name"])
                    and str(r["id"]) != str(identifier)
                    for r in duplicates
                ):
                    raise DomainError("sensitive_word_exists", 409)
            if kind == "access_keys":
                for profile_id in spec["profile_ids"]:
                    await self.gateway.catalog.get(c, tenant, "profiles", profile_id, True)
            if kind == "alert_rules" and spec["profile_id"]:
                await self.gateway.catalog.get(c, tenant, "profiles", spec["profile_id"])
            params = {
                "t": tenant,
                "name": spec["name"],
                "spec": json.dumps(spec),
                "id": identifier or uuid4(),
            }
            if identifier:
                row = await one(
                    c,
                    f"UPDATE gateway_{kind} SET name=:name,spec=CAST(:spec AS jsonb),revision=revision+1,updated_at=now() WHERE id=:id AND tenant_id=:t AND revision=:r AND NOT archived RETURNING *",
                    **params,
                    r=revision,
                )
                checked(row, "resource_revision_conflict")
            else:
                extra_columns, extra_values = "", ""
                if kind == "access_keys":
                    secret = "mgw_" + secrets.token_urlsafe(32)
                    extra_columns, extra_values = ",token_hash,prefix", ",:hash,:prefix"
                    params.update(
                        hash=hashlib.sha256(secret.encode()).hexdigest(), prefix=secret[:12]
                    )
                row = await one(
                    c,
                    f"INSERT INTO gateway_{kind}(id,tenant_id,name,spec{extra_columns}) VALUES(:id,:t,:name,CAST(:spec AS jsonb){extra_values}) RETURNING *",
                    **params,
                )
            await audit(c, tenant, actor, f"gateway.{kind}.saved", row["id"])
        row.pop("token_hash", None)
        if secret:
            row["secret"] = secret
        return row

    async def toggle(self, tenant, actor, kind, identifier, revision, enabled):
        if kind not in KINDS:
            raise DomainError("unknown_resource", 404)
        async with self.engine.begin() as c:
            await self.lock(c, tenant)
            row = checked(
                await one(
                    c,
                    f"UPDATE gateway_{kind} SET enabled=:enabled,revision=revision+1,updated_at=now() WHERE id=:id AND tenant_id=:t AND revision=:r AND NOT archived RETURNING *",
                    enabled=enabled,
                    id=identifier,
                    t=tenant,
                    r=revision,
                ),
                "resource_revision_conflict",
            )
            await audit(c, tenant, actor, f"gateway.{kind}.toggled", identifier, enabled=enabled)
        row.pop("token_hash", None)
        return row

    async def archive(self, tenant, actor, kind, identifier, revision):
        if kind not in KINDS | CATALOG:
            raise DomainError("unknown_resource", 404)
        async with self.engine.begin() as c:
            await self.lock(c, tenant)
            if kind in CATALOG:
                # Retain immutable versions and historic logs; reject referenced resources.
                if kind == "profiles":
                    for sql in (
                        "SELECT id FROM agents WHERE tenant_id=:t AND draft->>'model_profile_id'=:id LIMIT 1",
                        "SELECT a.id FROM agent_versions v JOIN agents a ON a.id=v.agent_id WHERE a.tenant_id=:t AND v.config->>'model_profile_id'=:id LIMIT 1",
                        "SELECT id FROM gateway_access_keys WHERE tenant_id=:t AND NOT archived AND spec->'profile_ids' @> CAST(:ids AS jsonb) LIMIT 1",
                    ):
                        if await one(
                            c, sql, t=tenant, id=str(identifier), ids=json.dumps([str(identifier)])
                        ):
                            raise DomainError("resource_is_referenced", 409)
                for table in ("models", "profiles"):
                    if table == kind or kind == "profiles":
                        continue
                    linked = await one(
                        c,
                        f"SELECT id FROM gateway_{table} WHERE tenant_id=:t AND NOT archived AND spec::text LIKE :needle LIMIT 1",
                        t=tenant,
                        needle=f"%{identifier}%",
                    )
                    if linked:
                        raise DomainError("resource_is_referenced", 409)
                linked = await one(
                    c,
                    "SELECT v.profile_id FROM gateway_profile_versions v JOIN gateway_profiles p ON p.id=v.profile_id WHERE p.tenant_id=:t AND NOT p.archived AND v.snapshot::text LIKE :needle LIMIT 1",
                    t=tenant,
                    needle=f"%{identifier}%",
                )
                if linked and kind != "profiles":
                    raise DomainError("resource_has_published_references", 409)
            row = checked(
                await one(
                    c,
                    f"UPDATE gateway_{kind} SET archived=true,enabled=false,revision=revision+1,updated_at=now() WHERE tenant_id=:t AND id=:id AND revision=:r AND NOT archived RETURNING id",
                    t=tenant,
                    id=identifier,
                    r=revision,
                ),
                "resource_revision_conflict",
            )
            await audit(c, tenant, actor, f"gateway.{kind}.archived", identifier)
        return row

    async def rotate(self, tenant, actor, identifier, revision):
        secret = "mgw_" + secrets.token_urlsafe(32)
        async with self.engine.begin() as c:
            await self.lock(c, tenant)
            row = checked(
                await one(
                    c,
                    "UPDATE gateway_access_keys SET token_hash=:hash,prefix=:prefix,revision=revision+1,updated_at=now() WHERE tenant_id=:t AND id=:id AND revision=:r AND NOT archived RETURNING id,prefix,revision",
                    t=tenant,
                    id=identifier,
                    r=revision,
                    hash=hashlib.sha256(secret.encode()).hexdigest(),
                    prefix=secret[:12],
                ),
                "resource_revision_conflict",
            )
            await audit(c, tenant, actor, "gateway.key.rotated", identifier)
        return {**row, "secret": secret}

    async def authenticate(self, secret):
        if not secret.startswith("mgw_"):
            raise DomainError("invalid_gateway_key", 401)
        async with self.engine.connect() as c:
            row = await one(
                c,
                "SELECT * FROM gateway_access_keys WHERE token_hash=:hash AND enabled AND NOT archived",
                hash=hashlib.sha256(secret.encode()).hexdigest(),
            )
        if not row or datetime.fromisoformat(row["spec"]["expires_at"]) <= datetime.now(UTC):
            raise DomainError("invalid_or_expired_gateway_key", 401)
        return row

    async def model_settings(self, tenant, model_id, c=None):
        if c is None:
            async with self.engine.connect() as conn:
                return await self.model_settings(tenant, model_id, conn)
        await self.gateway.catalog.get(c, tenant, "models", model_id)
        return await one(
            c,
            "SELECT * FROM gateway_model_settings WHERE tenant_id=:t AND model_id=:id",
            t=tenant,
            id=model_id,
        ) or {
            "model_id": str(model_id),
            "revision": 0,
            "spec": ModelSettings().model_dump(mode="json"),
        }

    async def quota(self, tenant, c=None):
        if c is None:
            async with self.engine.connect() as conn:
                return await self.quota(tenant, conn)
        return await one(c, "SELECT * FROM gateway_quotas WHERE tenant_id=:t", t=tenant) or {
            "revision": 0,
            "spec": Quota().model_dump(),
        }

    async def settings_save(self, tenant, actor, body, model_id=None):
        async with self.engine.begin() as c:
            await self.lock(c, tenant)
            old = (
                await self.model_settings(tenant, model_id, c)
                if model_id
                else await self.quota(tenant, c)
            )
            if old["revision"] != body.revision:
                raise DomainError("resource_revision_conflict", 409)
            spec = json.dumps(body.spec.model_dump(mode="json"))
            if model_id:
                row = await one(
                    c,
                    "INSERT INTO gateway_model_settings(model_id,tenant_id,spec) VALUES(:id,:t,CAST(:s AS jsonb)) ON CONFLICT(model_id) DO UPDATE SET spec=excluded.spec,revision=gateway_model_settings.revision+1,updated_at=now() RETURNING *",
                    id=model_id,
                    t=tenant,
                    s=spec,
                )
            else:
                row = await one(
                    c,
                    "INSERT INTO gateway_quotas(tenant_id,spec) VALUES(:t,CAST(:s AS jsonb)) ON CONFLICT(tenant_id) DO UPDATE SET spec=excluded.spec,revision=gateway_quotas.revision+1,updated_at=now() RETURNING *",
                    t=tenant,
                    s=spec,
                )
            await audit(c, tenant, actor, "gateway.settings.saved", model_id or tenant)
        return row

    async def review(self, tenant, value):
        content = normalize("\n".join(texts(value)))
        async with self.engine.connect() as c:
            words = await many(
                c,
                "SELECT id,name,spec->>'category' category FROM gateway_sensitive_words WHERE tenant_id=:t AND enabled AND NOT archived",
                t=tenant,
            )
        return [
            {"id": str(row["id"]), "category": row["category"]}
            for row in words
            if normalize(row["name"]) in content
        ]

    async def admit(self, tenant, call_id, profile_id, timeout, key=None):
        async with self.engine.begin() as c:
            await self.lock(c, tenant)
            now = datetime.now(UTC)
            budgets = [("tenant", (await self.quota(tenant, c))["spec"])]
            if key:
                current = checked(
                    await one(
                        c,
                        "SELECT * FROM gateway_access_keys WHERE tenant_id=:t AND id=:id AND enabled AND NOT archived",
                        t=tenant,
                        id=key["id"],
                    ),
                    "gateway_key_revoked",
                )
                if (
                    current["token_hash"] != key["token_hash"]
                    or datetime.fromisoformat(current["spec"]["expires_at"]) <= now
                ):
                    raise DomainError("invalid_or_expired_gateway_key", 401)
                if str(profile_id) not in current["spec"]["profile_ids"]:
                    raise DomainError("gateway_key_profile_forbidden", 403)
                budgets.append((str(key["id"]), current["spec"]))
            await execute(
                c, "DELETE FROM gateway_leases WHERE tenant_id=:t AND expires_at<now()", t=tenant
            )
            # Each call has a lease for each quota scope; both admissions are atomic.
            for scope, limits in budgets:
                active = (
                    await one(
                        c,
                        "SELECT count(*) n FROM gateway_leases WHERE tenant_id=:t AND scope=:s",
                        t=tenant,
                        s=scope,
                    )
                )["n"]
                if active >= limits["concurrency"]:
                    raise DomainError("gateway_concurrency_exceeded", 429)
                for bucket, limit in [
                    (now.strftime("%Y-%m-%dT%H:%M"), limits["requests_per_minute"]),
                    (now.strftime("%Y-%m-%d"), limits["requests_per_day"]),
                ]:
                    count = await one(
                        c,
                        "INSERT INTO gateway_quota_counters(tenant_id,scope,bucket,requests) VALUES(:t,:s,:b,1) ON CONFLICT(tenant_id,scope,bucket) DO UPDATE SET requests=gateway_quota_counters.requests+1 RETURNING requests",
                        t=tenant,
                        s=scope,
                        b=bucket,
                    )
                    if count["requests"] > limit:
                        raise DomainError("gateway_request_quota_exceeded", 429)
                await execute(
                    c,
                    "INSERT INTO gateway_leases(id,call_id,tenant_id,scope,expires_at) VALUES(:id,:call,:t,:s,:expires)",
                    id=uuid4(),
                    call=call_id,
                    t=tenant,
                    s=scope,
                    expires=now + timedelta(seconds=timeout + 30),
                )
            return budgets

    async def release(self, tenant, call_id, key=None):
        async with self.engine.begin() as c:
            await execute(
                c,
                "DELETE FROM gateway_leases WHERE tenant_id=:t AND call_id=:id",
                t=tenant,
                id=call_id,
            )

    async def import_words(self, tenant, actor, words):
        async with self.engine.begin() as c:
            await self.lock(c, tenant)
            old = await many(
                c,
                "SELECT name FROM gateway_sensitive_words WHERE tenant_id=:t AND NOT archived",
                t=tenant,
            )
            seen = {normalize(row["name"]) for row in old}
            imported = 0
            for word in words:
                if normalize(word.name) in seen:
                    continue
                seen.add(normalize(word.name))
                await execute(
                    c,
                    "INSERT INTO gateway_sensitive_words(id,tenant_id,name,spec) VALUES(:id,:t,:name,CAST(:spec AS jsonb))",
                    id=uuid4(),
                    t=tenant,
                    name=word.name,
                    spec=word.model_dump_json(),
                )
                imported += 1
            await audit(c, tenant, actor, "gateway.words.imported", tenant, count=imported)
        return {"imported": imported, "skipped": len(words) - imported}

    async def record_cost(self, c, tenant, attempt_id, call_id, model_id, pricing, usage):
        await execute(
            c,
            "INSERT INTO gateway_costs(attempt_id,tenant_id,model_id,call_id,amount,pricing,usage) VALUES(:aid,:t,:mid,:cid,:amount,CAST(:pricing AS jsonb),CAST(:usage AS jsonb)) ON CONFLICT(attempt_id) DO NOTHING",
            aid=attempt_id,
            t=tenant,
            mid=model_id,
            cid=call_id,
            amount=estimate(pricing, usage),
            pricing=json.dumps(pricing),
            usage=json.dumps(usage),
        )

    async def alerts(self, c, tenant, call_id):
        await self.lock(c, tenant, "alerts")
        call = checked(
            await one(
                c, "SELECT * FROM gateway_calls WHERE tenant_id=:t AND id=:id", t=tenant, id=call_id
            )
        )
        rules = await many(
            c,
            "SELECT * FROM gateway_alert_rules WHERE tenant_id=:t AND enabled AND NOT archived",
            t=tenant,
        )
        for rule in rules:
            spec = rule["spec"]
            if spec["profile_id"] and spec["profile_id"] != str(call["profile_id"]):
                continue
            triggered = {
                "failure": call["status"] == "failed",
                "latency": (call["duration_ms"] or 0) >= spec["threshold_ms"],
                "content_blocked": (call["error_code"] or "").startswith("content_blocked"),
            }[spec["metric"]]
            if not triggered:
                continue
            recent = await one(
                c,
                "SELECT id FROM gateway_alert_events WHERE rule_id=:r AND created_at>:since LIMIT 1",
                r=rule["id"],
                since=datetime.now(UTC) - timedelta(seconds=spec["cooldown_seconds"]),
            )
            if recent:
                continue
            await execute(
                c,
                "INSERT INTO gateway_alert_events(id,tenant_id,rule_id,call_id,rule_name,metric,detail) VALUES(:id,:t,:r,:call,:name,:metric,CAST(:detail AS jsonb)) ON CONFLICT(rule_id,call_id) DO NOTHING",
                id=uuid4(),
                t=tenant,
                r=rule["id"],
                call=call_id,
                name=rule["name"],
                metric=spec["metric"],
                detail=json.dumps(
                    {"error_code": call["error_code"], "duration_ms": call["duration_ms"]}
                ),
            )
