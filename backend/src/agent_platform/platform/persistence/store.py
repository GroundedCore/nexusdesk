import json
from contextlib import asynccontextmanager

from sqlalchemy import text


class DomainError(Exception):
    def __init__(self, code, status=400):
        self.code, self.status = code, status
        super().__init__(code)


@asynccontextmanager
async def transaction(engine, connection=None):
    if connection is not None:
        yield connection
    else:
        async with engine.begin() as conn:
            yield conn


async def one(conn, sql, **params):
    row = (await conn.execute(text(sql), params)).mappings().first()
    return dict(row) if row else None


async def many(conn, sql, **params):
    return [dict(row) for row in (await conn.execute(text(sql), params)).mappings().all()]


async def execute(conn, sql, **params):
    return await conn.execute(text(sql), params)


async def audit(conn, tenant, actor, action, resource, **details):
    await execute(
        conn,
        """INSERT INTO audit_records(tenant_id,actor,action,resource,details)
        VALUES (:tenant,:actor,:action,:resource,CAST(:details AS jsonb))""",
        tenant=tenant,
        actor=actor,
        action=action,
        resource=str(resource),
        details=json.dumps(details),
    )


def required(row, code="not_found"):
    if row is None:
        raise DomainError(code, 404)
    return row
