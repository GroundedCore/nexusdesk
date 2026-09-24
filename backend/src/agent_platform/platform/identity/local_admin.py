"""Local emergency access, independent of enterprise identity providers."""

import asyncio
import hashlib
import hmac
import re
import secrets
from pathlib import Path
from uuid import uuid4

from agent_platform.platform.persistence.store import DomainError, audit, execute, one

ITERATIONS = 600_000


def username(value):
    value = value.strip().lower()
    if not re.fullmatch(r"[a-z0-9][a-z0-9_.-]{2,63}", value):
        raise DomainError("invalid_local_username", 422)
    return value


def validate_password(value):
    if not 12 <= len(value) <= 128 or len(value.encode()) > 512 or value.isspace():
        raise DomainError("password_requires_12_to_128_characters", 422)


def hash_password(value):
    validate_password(value)
    salt = secrets.token_bytes(16)
    derived = hashlib.pbkdf2_hmac("sha256", value.encode(), salt, ITERATIONS)
    return f"pbkdf2_sha256${ITERATIONS}${salt.hex()}${derived.hex()}"


def verify_password(value, encoded):
    try:
        kind, rounds, salt, expected = encoded.split("$")
        if kind != "pbkdf2_sha256" or int(rounds) != ITERATIONS:
            return False
        actual = hashlib.pbkdf2_hmac("sha256", value.encode(), bytes.fromhex(salt), int(rounds))
        return hmac.compare_digest(actual.hex(), expected)
    except (ValueError, TypeError):
        return False


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


# Unknown accounts still perform the same password derivation.
DUMMY_HASH = f"pbkdf2_sha256${ITERATIONS}${'00' * 16}${'00' * 32}"


async def seed_default_admin(engine, tenant):
    """Idempotent deployment seed, also supports a new tenant on an upgraded database."""
    sql = Path("migrations/sql/0029_default_admin.sql").read_text(encoding="utf-8")
    async with engine.begin() as c:
        await execute(c, "SELECT set_config('nexusdesk.tenant_id', :t, true)", t=tenant)
        await execute(c, sql)


async def protect_local_admin(c, tenant, *ids):
    for uid in ids:
        if await one(
            c,
            "SELECT user_id FROM local_admin_credentials WHERE tenant_id=:t AND user_id=:u",
            t=tenant,
            u=uid,
        ):
            raise DomainError("independent_admin_protected", 409)


class LocalAdmin:
    def __init__(self, engine, tenant):
        self.engine, self.tenant = engine, tenant

    async def initialize(self, login, password, name="本地应急管理员"):
        login = username(login)
        password_hash = await asyncio.to_thread(hash_password, password)
        async with self.engine.begin() as c:
            await execute(
                c,
                "SELECT pg_advisory_xact_lock(hashtextextended(:t,0))",
                t=self.tenant + ":local-admin-init",
            )
            if await one(
                c, "SELECT user_id FROM local_admin_credentials WHERE tenant_id=:t", t=self.tenant
            ):
                raise DomainError("local_admin_already_initialized", 409)
            uid = uuid4()
            await execute(
                c,
                "INSERT INTO enterprise_users(id,tenant_id,name,role) VALUES(:u,:t,:n,'admin')",
                u=uid,
                t=self.tenant,
                n=name,
            )
            await execute(
                c,
                "INSERT INTO local_admin_credentials(user_id,tenant_id,username,password_hash) VALUES(:u,:t,:n,:p)",
                u=uid,
                t=self.tenant,
                n=login,
                p=password_hash,
            )
            await audit(c, self.tenant, "server-console", "local_admin.initialized", uid)
            return {"id": str(uid), "username": login}

    async def limit(self, login, peer):
        denied = False
        # Commit attempts even when authentication fails; shared across API workers.
        async with self.engine.begin() as c:
            await execute(c, "DELETE FROM local_login_limits WHERE expires_at<now()")
            for bucket, maximum in (("ip:" + digest(peer), 30), ("user:" + digest(login), 10)):
                row = await one(
                    c,
                    """INSERT INTO local_login_limits(tenant_id,bucket,attempts,expires_at)
                    VALUES(:t,:b,1,now()+interval '15 minutes')
                    ON CONFLICT(tenant_id,bucket) DO UPDATE SET attempts=local_login_limits.attempts+1
                    RETURNING attempts""",
                    t=self.tenant,
                    b=bucket,
                )
                denied = denied or row["attempts"] > maximum
        if denied:
            raise DomainError("local_login_rate_limited", 429)

    async def login(self, login, password, peer):
        login = login.strip().lower()
        await self.limit(login, peer)
        async with self.engine.connect() as c:
            row = await one(
                c,
                """SELECT a.*,u.enabled,u.role,u.name FROM local_admin_credentials a
                JOIN enterprise_users u ON u.id=a.user_id WHERE a.tenant_id=:t AND a.username=:n""",
                t=self.tenant,
                n=login,
            )
        valid = await asyncio.to_thread(
            verify_password, password, row["password_hash"] if row else DUMMY_HASH
        )
        async with self.engine.begin() as c:
            # Recheck the hash under lock: a concurrent password change must invalidate this login.
            current = await one(
                c,
                """SELECT a.*,u.enabled,u.role,u.name FROM local_admin_credentials a
                JOIN enterprise_users u ON u.id=a.user_id WHERE a.tenant_id=:t AND a.username=:n FOR UPDATE OF a,u""",
                t=self.tenant,
                n=login,
            )
            valid = bool(
                valid
                and current
                and current["enabled"]
                and current["role"] == "admin"
                and current["password_hash"] == row["password_hash"]
            )
            if not valid:
                await audit(c, self.tenant, "anonymous", "local_admin.login_failed", "login")
            else:
                token = "local_" + secrets.token_urlsafe(32)
                await execute(c, "DELETE FROM local_admin_sessions WHERE expires_at<now()")
                await execute(
                    c,
                    """INSERT INTO local_admin_sessions(token_hash,user_id,tenant_id,expires_at)
                    VALUES(:h,:u,:t,now()+interval '1 hour')""",
                    h=digest(token),
                    u=current["user_id"],
                    t=self.tenant,
                )
                await audit(
                    c,
                    self.tenant,
                    "enterprise:" + str(current["user_id"]),
                    "local_admin.login",
                    current["user_id"],
                )
        if not valid:
            raise DomainError("invalid_local_credentials", 401)
        return {
            "access_token": token,
            "token_type": "Bearer",
            "expires_in": 3600,
            "must_change_password": current["must_change_password"],
            "user": {"id": current["user_id"], "name": current["name"], "role": "admin"},
        }

    async def session(self, c, token):
        row = await one(
            c,
            """SELECT s.user_id,u.name,u.role,a.must_change_password FROM local_admin_sessions s
            JOIN enterprise_users u ON u.id=s.user_id JOIN local_admin_credentials a ON a.user_id=u.id
            WHERE s.tenant_id=:t AND a.tenant_id=:t AND u.tenant_id=:t AND s.token_hash=:h
            AND s.expires_at>now() AND u.enabled AND u.role='admin'""",
            t=self.tenant,
            h=digest(token),
        )
        if not row:
            raise DomainError("invalid_local_session", 401)
        return row

    async def logout(self, token):
        async with self.engine.begin() as c:
            row = await one(
                c,
                "DELETE FROM local_admin_sessions WHERE tenant_id=:t AND token_hash=:h RETURNING user_id",
                t=self.tenant,
                h=digest(token),
            )
            if row:
                await audit(
                    c,
                    self.tenant,
                    "enterprise:" + str(row["user_id"]),
                    "local_admin.logout",
                    row["user_id"],
                )

    async def change_password(self, token, old, new):
        validate_password(new)
        async with self.engine.connect() as c:
            session = await self.session(c, token)
            row = await one(
                c, "SELECT * FROM local_admin_credentials WHERE user_id=:u", u=session["user_id"]
            )
        await self.limit(row["username"], "password-change:" + str(row["user_id"]))
        if not await asyncio.to_thread(verify_password, old, row["password_hash"]):
            raise DomainError("invalid_local_credentials", 401)
        if hmac.compare_digest(old.encode(), new.encode()):
            raise DomainError("new_password_must_differ", 422)
        encoded = await asyncio.to_thread(hash_password, new)
        async with self.engine.begin() as c:
            await execute(
                c,
                "SELECT user_id FROM local_admin_credentials WHERE user_id=:u FOR UPDATE",
                u=row["user_id"],
            )
            await self.session(c, token)
            updated = await one(
                c,
                """UPDATE local_admin_credentials SET password_hash=:p,must_change_password=false
                WHERE user_id=:u AND password_hash=:old RETURNING user_id""",
                p=encoded,
                u=row["user_id"],
                old=row["password_hash"],
            )
            if not updated:
                raise DomainError("invalid_local_session", 401)
            await execute(c, "DELETE FROM local_admin_sessions WHERE user_id=:u", u=row["user_id"])
            await audit(
                c,
                self.tenant,
                "enterprise:" + str(row["user_id"]),
                "local_admin.password_changed",
                row["user_id"],
            )
