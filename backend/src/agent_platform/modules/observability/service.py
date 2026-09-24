from agent_platform.platform.persistence.store import many, one, required


class ObservabilityService:
    def __init__(self, engine):
        self.engine = engine

    async def summary(self, tenant):
        async with self.engine.connect() as c:
            result = await one(
                c,
                """SELECT count(*) AS total,
                count(*) FILTER(WHERE status='completed') AS completed,
                count(*) FILTER(WHERE status='failed') AS failed,
                count(*) FILTER(WHERE status='cancelled') AS cancelled,
                count(*) FILTER(WHERE status='running') AS running,
                count(*) FILTER(WHERE status='queued') AS queued,
                avg(extract(epoch FROM finished_at-started_at)) FILTER(WHERE finished_at IS NOT NULL) AS average_seconds,
                percentile_cont(0.95) WITHIN GROUP(ORDER BY extract(epoch FROM finished_at-started_at)) FILTER(WHERE finished_at IS NOT NULL) AS p95_seconds
                FROM runtime_runs WHERE tenant_id=:t AND created_at>now()-interval '24 hours'""",
                t=tenant,
            )
            result["tool_failures"] = (
                await one(
                    c,
                    """SELECT count(*) AS n FROM runtime_events e JOIN runtime_runs r ON r.id=e.run_id
                WHERE r.tenant_id=:t AND e.created_at>now()-interval '24 hours' AND e.type='tool.completed'
                AND e.data->>'ok'='false'""",
                    t=tenant,
                )
            )["n"]
            result["tokens"] = (
                await one(
                    c,
                    """SELECT COALESCE(sum(CAST(e.data->'usage'->>'total_tokens' AS bigint)),0) AS n
                FROM runtime_events e JOIN runtime_runs r ON r.id=e.run_id WHERE r.tenant_id=:t
                AND e.created_at>now()-interval '24 hours' AND e.type='model.completed'""",
                    t=tenant,
                )
            )["n"]
            return result

    async def runs(self, tenant):
        async with self.engine.connect() as c:
            return await many(
                c,
                """SELECT id,conversation_id,status,error_code,created_at,started_at,finished_at
                FROM runtime_runs WHERE tenant_id=:t ORDER BY created_at DESC LIMIT 100""",
                t=tenant,
            )

    async def audit(self, tenant, after=0):
        async with self.engine.connect() as c:
            return await many(
                c,
                "SELECT * FROM audit_records WHERE tenant_id=:t AND id>:after ORDER BY id DESC LIMIT 100",
                t=tenant,
                after=after,
            )

    async def gateways(self, tenant):
        async with self.engine.connect() as c:
            models = await many(
                c,
                "SELECT operation,status,count(*) calls,avg(duration_ms) average_ms,percentile_cont(0.95) WITHIN GROUP(ORDER BY duration_ms) p95_ms FROM gateway_calls WHERE tenant_id=:t AND created_at>now()-interval '24 hours' GROUP BY operation,status ORDER BY operation,status",
                t=tenant,
            )
            tools = await many(
                c,
                "SELECT adapter_type,effect,status,count(*) calls,avg(duration_ms) average_ms FROM tool_calls WHERE tenant_id=:t AND created_at>now()-interval '24 hours' GROUP BY adapter_type,effect,status ORDER BY adapter_type,effect,status",
                t=tenant,
            )
            usage = await many(
                c,
                "SELECT g.operation,u.key unit,sum((u.value#>>'{}')::numeric) amount FROM gateway_attempts a JOIN gateway_calls g ON g.id=a.call_id CROSS JOIN LATERAL jsonb_each(CASE WHEN jsonb_typeof(a.usage)='object' THEN a.usage ELSE '{}'::jsonb END) u WHERE g.tenant_id=:t AND g.created_at>now()-interval '24 hours' GROUP BY g.operation,u.key",
                t=tenant,
            )
            return {"models": models, "tools": tools, "known_usage": usage, "window_hours": 24}

    async def trace(self, tenant, rid):
        async with self.engine.connect() as c:
            row = required(
                await one(
                    c,
                    "SELECT id,conversation_id,status,error_code,config FROM runtime_runs WHERE id=:id AND tenant_id=:t",
                    id=rid,
                    t=tenant,
                )
            )
            row["model_calls"] = await many(
                c,
                "SELECT id,operation,profile_id,profile_version,status,error_code,duration_ms FROM gateway_calls WHERE run_id=:id AND tenant_id=:t ORDER BY created_at",
                id=rid,
                t=tenant,
            )
            row["tool_calls"] = await many(
                c,
                "SELECT * FROM tool_calls WHERE run_id=:id AND tenant_id=:t ORDER BY created_at",
                id=rid,
                t=tenant,
            )
            return row
