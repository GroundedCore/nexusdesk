import hashlib
import secrets
from uuid import uuid4

from agent_platform.platform.persistence.store import audit, many, one, required


class StaffDirectory:
    def __init__(self, engine):
        self.engine = engine

    async def list(self, tenant):
        async with self.engine.connect() as c:
            return await many(
                c,
                "SELECT id,name,role,enabled,created_at FROM staff_accounts WHERE tenant_id=:t ORDER BY created_at DESC LIMIT 200",
                t=tenant,
            )

    async def create(self, tenant, actor, name, role):
        token = "staff_" + secrets.token_urlsafe(32)
        async with self.engine.begin() as c:
            row = await one(
                c,
                "INSERT INTO staff_accounts(id,tenant_id,name,role,token_hash) VALUES(:id,:t,:name,:role,:hash) RETURNING id,name,role,enabled",
                id=uuid4(),
                t=tenant,
                name=name,
                role=role,
                hash=hashlib.sha256(token.encode()).hexdigest(),
            )
            await audit(c, tenant, actor, "staff.created", row["id"], role=role)
            return {**row, "token": token}

    async def enable(self, tenant, actor, identifier, enabled):
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    "UPDATE staff_accounts SET enabled=:e WHERE id=:id AND tenant_id=:t RETURNING id,name,role,enabled",
                    id=identifier,
                    t=tenant,
                    e=enabled,
                )
            )
            await audit(
                c, tenant, actor, "staff.enabled" if enabled else "staff.disabled", identifier
            )
            return row
