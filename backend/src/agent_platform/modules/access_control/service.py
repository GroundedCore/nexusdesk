"""P0 member directory, local credentials, and session lifecycle."""

import asyncio
import secrets
from uuid import uuid4

from agent_platform.platform.identity.local_admin import (
    hash_password,
    protect_local_admin,
)
from agent_platform.platform.identity.local_admin import (
    username as normalize_username,
)
from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)

_MEMBER_SELECT = """
SELECT u.*,
    COALESCE((
        SELECT jsonb_agg(
            jsonb_build_object(
                'id', i.id,
                'source', i.source,
                'subject', i.subject
            ) ORDER BY i.created_at
        )
        FROM enterprise_identities i
        WHERE i.tenant_id=u.tenant_id AND i.user_id=u.id
    ), '[]'::jsonb) AS identities,
    l.username,
    (l.user_id IS NOT NULL) AS has_local_credential,
    COALESCE(l.is_emergency, false) AS is_emergency
FROM enterprise_users u
LEFT JOIN local_admin_credentials l
    ON l.tenant_id=u.tenant_id AND l.user_id=u.id
"""


class MemberDirectory:
    def __init__(self, engine):
        self.engine = engine

    async def _get(self, c, tenant, member_id):
        return required(
            await one(
                c,
                _MEMBER_SELECT + " WHERE u.tenant_id=:t AND u.id=:id",
                t=tenant,
                id=member_id,
            ),
            "member_not_found",
        )

    async def get(self, tenant, member_id):
        async with self.engine.connect() as c:
            return await self._get(c, tenant, member_id)

    async def list(
        self,
        tenant,
        q="",
        role=None,
        status="all",
        page=1,
        page_size=10,
    ):
        where = "u.tenant_id=:t"
        params = {"t": tenant}
        if q:
            where += """ AND (
                u.name ILIKE :pattern
                OR CAST(u.id AS text) ILIKE :pattern
                OR COALESCE(l.username, '') ILIKE :pattern
                OR EXISTS (
                    SELECT 1 FROM enterprise_identities i
                    WHERE i.tenant_id=u.tenant_id AND i.user_id=u.id
                    AND i.subject ILIKE :pattern
                )
            )"""
            params["pattern"] = "%" + q + "%"
        if role == "unassigned":
            where += " AND u.role IS NULL"
        elif role:
            where += " AND u.role=:role"
            params["role"] = role
        if status == "enabled":
            where += " AND u.enabled"
        elif status == "disabled":
            where += " AND NOT u.enabled"
        elif status == "pending":
            where += " AND u.role IS NULL"

        async with self.engine.connect() as c:
            items = await many(
                c,
                _MEMBER_SELECT
                + f" WHERE {where} ORDER BY u.created_at DESC,u.id LIMIT :lim OFFSET :off",
                **params,
                lim=page_size,
                off=(page - 1) * page_size,
            )
            total = await one(
                c,
                f"""SELECT count(*) AS n FROM enterprise_users u
                LEFT JOIN local_admin_credentials l
                    ON l.tenant_id=u.tenant_id AND l.user_id=u.id
                WHERE {where}""",
                **params,
            )
            counts = await one(
                c,
                """SELECT count(*)::int AS total,
                count(*) FILTER (WHERE enabled)::int AS enabled,
                count(*) FILTER (WHERE NOT enabled)::int AS disabled,
                count(*) FILTER (WHERE role IS NULL)::int AS pending
                FROM enterprise_users WHERE tenant_id=:t""",
                t=tenant,
            )
        return {
            "items": items,
            "total": total["n"],
            "page": page,
            "page_size": page_size,
            "counts": counts,
        }

    async def create_local(self, tenant, actor, name, login, role):
        login = normalize_username(login)
        temporary_password = secrets.token_urlsafe(18)
        password_hash = await asyncio.to_thread(hash_password, temporary_password)
        member_id = uuid4()
        async with self.engine.begin() as c:
            await execute(
                c,
                "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))",
                key=tenant + ":local-username:" + login,
            )
            if await one(
                c,
                "SELECT user_id FROM local_admin_credentials WHERE tenant_id=:t AND username=:n",
                t=tenant,
                n=login,
            ):
                raise DomainError("local_username_exists", 409)
            await execute(
                c,
                """INSERT INTO enterprise_users(id,tenant_id,name,role)
                VALUES(:id,:t,:name,:role)""",
                id=member_id,
                t=tenant,
                name=name,
                role=role,
            )
            await execute(
                c,
                """INSERT INTO local_admin_credentials(
                user_id,tenant_id,username,password_hash,must_change_password,is_emergency)
                VALUES(:u,:t,:username,:password,true,false)""",
                u=member_id,
                t=tenant,
                username=login,
                password=password_hash,
            )
            await audit(
                c,
                tenant,
                actor,
                "member.created",
                member_id,
                username=login,
                role=role,
                auth_source="local",
            )
            member = await self._get(c, tenant, member_id)
        return {"member": member, "temporary_password": temporary_password}

    async def update(
        self,
        tenant,
        actor,
        principal_user_id,
        member_id,
        name,
        role,
        enabled,
        revision,
    ):
        async with self.engine.begin() as c:
            await protect_local_admin(c, tenant, member_id)
            await execute(
                c,
                "SELECT pg_advisory_xact_lock(hashtextextended(:key,0))",
                key=tenant + ":member-role-change",
            )
            current = required(
                await one(
                    c,
                    """SELECT * FROM enterprise_users
                    WHERE tenant_id=:t AND id=:id FOR UPDATE""",
                    t=tenant,
                    id=member_id,
                ),
                "member_not_found",
            )
            if current["revision"] != revision:
                raise DomainError("member_revision_conflict", 409)
            access_changed = current["role"] != role or current["enabled"] != enabled
            if principal_user_id == str(member_id) and access_changed:
                raise DomainError("cannot_modify_self", 409)
            losing_admin = current["role"] == "admin" and current["enabled"] and (
                role != "admin" or not enabled
            )
            if losing_admin:
                active_admins = await one(
                    c,
                    """SELECT count(*) AS n FROM enterprise_users
                    WHERE tenant_id=:t AND enabled AND role='admin'""",
                    t=tenant,
                )
                if active_admins["n"] <= 1:
                    raise DomainError("last_admin_protected", 409)
            row = await one(
                c,
                """UPDATE enterprise_users SET
                name=:name,role=:role,enabled=:enabled,
                disabled_reason=:reason,
                auth_version=auth_version+CASE WHEN :access_changed THEN 1 ELSE 0 END,
                revision=revision+1
                WHERE tenant_id=:t AND id=:id AND revision=:revision
                RETURNING *""",
                name=name,
                role=role,
                enabled=enabled,
                reason=None if enabled else "disabled_by_admin",
                access_changed=access_changed,
                t=tenant,
                id=member_id,
                revision=revision,
            )
            if not row:
                raise DomainError("member_revision_conflict", 409)
            if access_changed:
                await execute(
                    c,
                    "UPDATE enterprise_sessions SET revoked=true WHERE user_id=:u",
                    u=member_id,
                )
                await execute(
                    c,
                    "DELETE FROM local_admin_sessions WHERE tenant_id=:t AND user_id=:u",
                    t=tenant,
                    u=member_id,
                )
            await audit(
                c,
                tenant,
                actor,
                "member.updated",
                member_id,
                before={
                    "name": current["name"],
                    "role": current["role"],
                    "enabled": current["enabled"],
                },
                after={"name": name, "role": role, "enabled": enabled},
                sessions_revoked=access_changed,
            )
            member = await self._get(c, tenant, member_id)
        return member

    async def set_enabled(
        self,
        tenant,
        actor,
        principal_user_id,
        member_id,
        enabled,
        revision,
    ):
        current = await self.get(tenant, member_id)
        return await self.update(
            tenant,
            actor,
            principal_user_id,
            member_id,
            current["name"],
            current["role"],
            enabled,
            revision,
        )

    async def reset_password(
        self,
        tenant,
        actor,
        principal_user_id,
        member_id,
    ):
        async with self.engine.begin() as c:
            await protect_local_admin(c, tenant, member_id)
            if principal_user_id == str(member_id):
                raise DomainError("cannot_reset_own_password", 409)
            credential = await one(
                c,
                """SELECT user_id FROM local_admin_credentials
                WHERE tenant_id=:t AND user_id=:u FOR UPDATE""",
                t=tenant,
                u=member_id,
            )
            if not credential:
                required(
                    await one(
                        c,
                        "SELECT id FROM enterprise_users WHERE tenant_id=:t AND id=:u",
                        t=tenant,
                        u=member_id,
                    ),
                    "member_not_found",
                )
                raise DomainError("local_credential_not_configured", 409)
            temporary_password = secrets.token_urlsafe(18)
            password_hash = await asyncio.to_thread(hash_password, temporary_password)
            await execute(
                c,
                """UPDATE local_admin_credentials SET
                password_hash=:password,must_change_password=true
                WHERE tenant_id=:t AND user_id=:u""",
                password=password_hash,
                t=tenant,
                u=member_id,
            )
            await execute(
                c,
                "DELETE FROM local_admin_sessions WHERE tenant_id=:t AND user_id=:u",
                t=tenant,
                u=member_id,
            )
            await audit(
                c,
                tenant,
                actor,
                "member.password_reset",
                member_id,
                sessions_revoked=True,
            )
        return {"temporary_password": temporary_password}

    async def sessions(self, tenant, member_id, current_session_id=None):
        async with self.engine.connect() as c:
            exists = await one(
                c,
                "SELECT id FROM enterprise_users WHERE tenant_id=:t AND id=:u",
                t=tenant,
                u=member_id,
            )
            if not exists:
                raise DomainError("member_not_found", 404)
            rows = await many(
                c,
                """SELECT s.id::text AS id,'local' AS auth_source,
                    s.created_at,s.last_seen_at,s.expires_at,s.ip,s.user_agent
                FROM local_admin_sessions s
                WHERE s.tenant_id=:t AND s.user_id=:u AND s.expires_at>now()
                UNION ALL
                SELECT s.id::text,'sso',s.created_at,s.last_seen_at,s.expires_at,
                    s.ip,s.user_agent
                FROM enterprise_sessions s
                WHERE s.tenant_id=:t AND s.user_id=:u AND s.app_id IS NULL
                    AND NOT s.revoked AND s.expires_at>now()
                ORDER BY last_seen_at DESC""",
                t=tenant,
                u=member_id,
            )
        for row in rows:
            row["current"] = bool(current_session_id and row["id"] == current_session_id)
        return {"items": rows}

    async def revoke_sessions(
        self,
        tenant,
        actor,
        member_id,
        session_id=None,
        all_sessions=False,
    ):
        async with self.engine.begin() as c:
            if not await one(
                c,
                "SELECT id FROM enterprise_users WHERE tenant_id=:t AND id=:u",
                t=tenant,
                u=member_id,
            ):
                raise DomainError("member_not_found", 404)
            revoked = 0
            if all_sessions:
                result = await execute(
                    c,
                    "DELETE FROM local_admin_sessions WHERE tenant_id=:t AND user_id=:u",
                    t=tenant,
                    u=member_id,
                )
                revoked += result.rowcount or 0
                result = await execute(
                    c,
                    """UPDATE enterprise_sessions SET revoked=true
                    WHERE tenant_id=:t AND user_id=:u AND app_id IS NULL AND NOT revoked""",
                    t=tenant,
                    u=member_id,
                )
                revoked += result.rowcount or 0
            else:
                result = await execute(
                    c,
                    """DELETE FROM local_admin_sessions
                    WHERE tenant_id=:t AND user_id=:u AND id=:id""",
                    t=tenant,
                    u=member_id,
                    id=session_id,
                )
                revoked += result.rowcount or 0
                if not result.rowcount:
                    result = await execute(
                        c,
                        """UPDATE enterprise_sessions SET revoked=true
                        WHERE tenant_id=:t AND user_id=:u AND id=:id
                        AND app_id IS NULL AND NOT revoked""",
                        t=tenant,
                        u=member_id,
                        id=session_id,
                    )
                    revoked += result.rowcount or 0
            if not revoked:
                raise DomainError("member_session_not_found", 404)
            await audit(
                c,
                tenant,
                actor,
                "member.sessions_revoked",
                member_id,
                session_id=str(session_id) if session_id else None,
                all_sessions=all_sessions,
                revoked=revoked,
            )
        return {"revoked": revoked}
