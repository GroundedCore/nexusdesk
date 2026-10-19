from agent_platform.platform.persistence.store import many, one, required

# The answer round is the one that did not end in tool calls: the ReAct loop stops
# there, so its text is what the customer sees. Its model.started marks when the
# model call began, and the first_token_ms it records says how long that round ran
# before the first visible character.
ANSWER_ROUND = """SELECT
  (SELECT min(s.created_at) FROM runtime_events s
   WHERE s.run_id=c.run_id AND s.type='model.started'
     AND (s.data->>'round')=(c.data->>'round')) AS answer_started_at,
  CAST(c.data->>'first_token_ms' AS double precision) AS first_token_ms
  FROM runtime_events c
  WHERE c.run_id=r.id AND c.type='model.completed' AND (c.data->>'tool_calls')='0'
  ORDER BY c.seq LIMIT 1"""

# Time to the first character of the answer, in milliseconds, measured from when the
# run was queued. NULL when it was not measured: a run whose answer round never
# streamed (deltas disabled, or an older run predating first_token_ms) has no first
# character to time, and reporting that as zero would understate the metric.
TTFC_EXPRESSION = """CASE WHEN a.answer_started_at IS NULL OR a.first_token_ms IS NULL
  THEN NULL
  ELSE extract(epoch FROM (a.answer_started_at-r.created_at))*1000 + a.first_token_ms
  END"""

TTFC_ROWS = (
    "SELECT r.created_at," + TTFC_EXPRESSION + " AS ttfc_ms FROM runtime_runs r "
    "LEFT JOIN LATERAL (" + ANSWER_ROUND + ") a ON true WHERE r.tenant_id=:t"
)


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
            # Time to the first character of the answer. Derived from the persisted
            # round boundaries plus the first_token_ms each round records, so it needs
            # no extra writes and no stored token text.
            result["ttfc"] = await one(
                c,
                """SELECT percentile_cont(0.5) WITHIN GROUP(ORDER BY ttfc_ms) AS p50_ms,
                percentile_cont(0.95) WITHIN GROUP(ORDER BY ttfc_ms) AS p95_ms,
                count(*) AS samples
                FROM (""" + TTFC_ROWS + """ AND r.created_at>now()-interval '24 hours') t
                WHERE ttfc_ms IS NOT NULL""",
                t=tenant,
            )
            return result

    async def runs(self, tenant, page=1, page_size=10):
        async with self.engine.connect() as c:
            total = await one(
                c, "SELECT count(*) AS total FROM runtime_runs WHERE tenant_id=:t", t=tenant
            )
            items = await many(
                c,
                """SELECT r.id,r.conversation_id,r.status,r.error_code,r.created_at,r.started_at,
                r.finished_at,""" + TTFC_EXPRESSION + """ AS ttfc_ms
                FROM runtime_runs r LEFT JOIN LATERAL (""" + ANSWER_ROUND + """) a ON true
                WHERE r.tenant_id=:t ORDER BY r.created_at DESC LIMIT :lim OFFSET :off""",
                t=tenant,
                lim=page_size,
                off=(page - 1) * page_size,
            )
            return {"items": items, "total": total["total"], "page": page, "page_size": page_size}

    async def audit(self, tenant, page=1, page_size=10, action_type=None):
        async with self.engine.connect() as c:
            where = "tenant_id=:t"
            params = {"t": tenant}
            if action_type:
                where += " AND action=:action_type"
                params["action_type"] = action_type
            total = await one(
                c, f"SELECT count(*) AS total FROM audit_records WHERE {where}", **params
            )
            items = await many(
                c,
                f"""SELECT * FROM audit_records WHERE {where}
                ORDER BY id DESC LIMIT :lim OFFSET :off""",
                **params,
                lim=page_size,
                off=(page - 1) * page_size,
            )
            action_types = await many(
                c,
                """SELECT action, count(*)::int AS count FROM audit_records
                WHERE tenant_id=:t GROUP BY action ORDER BY action""",
                t=tenant,
            )
            return {
                "items": items,
                "total": total["total"],
                "page": page,
                "page_size": page_size,
                "action_types": action_types,
            }

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

    async def memory_summary(self, tenant):
        """Phase 4 memory observability (FR-7, R20).

        Extraction volume aggregates the ``memory.extract.applied`` audit
        details (same-key upserts rewrite item rows, so the row table cannot
        reconstruct the added/updated/pending/dropped split); the hit rate
        divides customer-bound runs with a ``memory.injected`` event by all
        customer-bound runs; governance/decay counts group the R17 audit
        actions; reinforcement reads the ``reinforce`` task queue. Windows
        follow the existing observability style: 24h for rates and counters,
        7d for the daily extraction and recovery trends.
        """
        async with self.engine.connect() as c:
            extraction_daily = await many(
                c,
                """SELECT date_trunc('day', created_at)::text AS day,
                count(*)::int AS runs,
                COALESCE(sum(CAST(details->>'added' AS int)),0)::int AS added,
                COALESCE(sum(CAST(details->>'updated' AS int)),0)::int AS updated,
                COALESCE(sum(CAST(details->>'pending' AS int)),0)::int AS pending,
                COALESCE(sum(CAST(details->>'dropped' AS int)),0)::int AS dropped,
                COALESCE(sum(CAST(details->>'pii_masked' AS int)),0)::int AS pii_masked
                FROM audit_records
                WHERE tenant_id=:t AND action='memory.extract.applied'
                AND created_at>now()-interval '7 days'
                GROUP BY 1 ORDER BY 1""",
                t=tenant,
            )
            hit_rate = await one(
                c,
                """SELECT count(*)::int AS customer_runs,
                count(*) FILTER (WHERE EXISTS (
                    SELECT 1 FROM runtime_events e
                    WHERE e.run_id=r.id AND e.type='memory.injected'))::int AS injected_runs
                FROM runtime_runs r
                JOIN runtime_conversations cv ON cv.id=r.conversation_id
                WHERE r.tenant_id=:t AND cv.customer_id IS NOT NULL
                AND r.created_at>now()-interval '24 hours'""",
                t=tenant,
            )
            backlog = await one(
                c,
                """SELECT count(*)::int AS pending,
                EXTRACT(EPOCH FROM max(now()-updated_at))::bigint AS oldest_age_seconds
                FROM memory_items WHERE tenant_id=:t AND status='pending'""",
                t=tenant,
            )
            governance = await many(
                c,
                """SELECT action, count(*)::int AS n FROM audit_records
                WHERE tenant_id=:t AND action LIKE 'memory.%'
                AND created_at>now()-interval '24 hours'
                GROUP BY action ORDER BY action""",
                t=tenant,
            )
            pii = await one(
                c,
                """SELECT COALESCE(sum(CAST(details->>'pii_masked' AS int)),0)::int AS n
                FROM audit_records
                WHERE tenant_id=:t AND action LIKE 'memory.%'
                AND created_at>now()-interval '24 hours'""",
                t=tenant,
            )
            reinforce = await one(
                c,
                """SELECT count(*) FILTER (WHERE status='done')::int AS tasks_done,
                count(*) FILTER (WHERE status='failed')::int AS tasks_failed,
                COALESCE(sum((SELECT count(*) FROM jsonb_object_keys(payload->'hits')))
                    FILTER (WHERE status='done'),0)::int AS ids_refreshed
                FROM memory_tasks
                WHERE tenant_id=:t AND kind='reinforce'
                AND created_at>now()-interval '24 hours'""",
                t=tenant,
            )
            decay = await one(
                c,
                """SELECT count(*) FILTER (WHERE action='memory.decay_pending')::int AS decay_pending,
                count(*) FILTER (WHERE action='memory.decay_expired')::int AS decay_expired,
                count(*) FILTER (WHERE action='memory.evicted')::int AS evicted,
                count(*) FILTER (WHERE action='memory.item.restored'
                    AND details->>'old_status'='expired')::int AS expired_restored
                FROM audit_records
                WHERE tenant_id=:t AND created_at>now()-interval '7 days'""",
                t=tenant,
            )
            search = await one(
                c,
                """SELECT count(*)::int AS calls,
                count(*) FILTER (WHERE CAST(e.data->>'hits' AS int)=0)::int AS empty
                FROM runtime_events e JOIN runtime_runs r ON r.id=e.run_id
                WHERE r.tenant_id=:t AND e.type='memory_search.completed'
                AND e.created_at>now()-interval '24 hours'""",
                t=tenant,
            )
            return {
                "window_hours": 24,
                "extraction_daily": extraction_daily,
                "hit_rate": hit_rate,
                "backlog": backlog,
                "governance": governance,
                "pii_masked": pii["n"],
                "reinforce": reinforce,
                "decay": decay,
                "search": search,
            }

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
