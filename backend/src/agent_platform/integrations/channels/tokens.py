"""Cross-process access-token cache for provider adapters.

Adapters keep a per-process hot cache so a send never pays a database round
trip; this table is the second level. Without it, every API/channel-worker
replica would mint its own provider access token. Scopes are hashed so a
provider secret never lands in the table or in logs.
"""

from agent_platform.platform.persistence.store import execute, one


class TokenStore:
    def __init__(self, engine):
        self.engine = engine

    async def get(self, scope: str) -> tuple[str, float] | None:
        async with self.engine.connect() as c:
            row = await one(
                c,
                "SELECT token,expires_at FROM channel_access_tokens WHERE scope=:scope",
                scope=scope,
            )
        if not row:
            return None
        return row["token"], row["expires_at"].timestamp()

    async def put(self, scope: str, token: str, expires_at: float) -> None:
        async with self.engine.begin() as c:
            await execute(
                c,
                """INSERT INTO channel_access_tokens(scope,token,expires_at,updated_at)
                VALUES(:scope,:token,to_timestamp(:expires),now())
                ON CONFLICT(scope) DO UPDATE SET
                    token=EXCLUDED.token,
                    expires_at=EXCLUDED.expires_at,
                    updated_at=now()""",
                scope=scope,
                token=token,
                expires=expires_at,
            )
