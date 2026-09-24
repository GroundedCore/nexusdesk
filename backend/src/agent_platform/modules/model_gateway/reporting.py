from datetime import UTC, datetime, timedelta

from agent_platform.platform.persistence.store import DomainError, many, one


def window(since=None, until=None):
    end = until or datetime.now(UTC)
    start = since or end - timedelta(days=30)
    if (
        start.tzinfo is None
        or end.tzinfo is None
        or start >= end
        or end - start > timedelta(days=366)
    ):
        raise DomainError("invalid_time_range")
    return start, end


async def call_page(
    engine, tenant, page, size, q, status, operation, since, until, profile_id, access_key_id
):
    start, end = window(since, until)
    params = {"t": tenant, "since": start, "until": end, "limit": size, "offset": (page - 1) * size}
    where = "c.tenant_id=:t AND c.created_at>=:since AND c.created_at<:until"
    for key, value in [
        ("status", status),
        ("operation", operation),
        ("profile_id", profile_id),
        ("access_key_id", access_key_id),
    ]:
        if value:
            where += f" AND c.{key}=:{key}"
            params[key] = value
    if q:
        where += " AND (c.id::text ILIKE :q OR c.error_code ILIKE :q OR c.operation ILIKE :q OR p.name ILIKE :q)"
        params["q"] = f"%{q}%"
    async with engine.connect() as c:
        total = (
            await one(
                c,
                f"SELECT count(*) n FROM gateway_calls c LEFT JOIN gateway_profiles p ON p.id=c.profile_id WHERE {where}",
                **params,
            )
        )["n"]
        items = await many(
            c,
            f"""SELECT c.*,p.name profile_name,k.name key_name,
            (SELECT sum(amount) FROM gateway_costs cost WHERE cost.call_id=c.id) estimated_cost,
            (SELECT count(*) FROM gateway_costs cost WHERE cost.call_id=c.id AND amount IS NULL) unknown_cost_attempts
            FROM gateway_calls c LEFT JOIN gateway_profiles p ON p.id=c.profile_id
            LEFT JOIN gateway_access_keys k ON k.id=c.access_key_id WHERE {where}
            ORDER BY c.created_at DESC,c.id LIMIT :limit OFFSET :offset""",
            **params,
        )
    for row in items:
        if row["estimated_cost"] is not None:
            row["estimated_cost"] = str(row["estimated_cost"])
    return {"items": items, "total": total, "page": page, "page_size": size}


async def statistics(engine, tenant, since=None, until=None):
    start, end = window(since, until)
    params = {"t": tenant, "since": start, "until": end}
    where = "tenant_id=:t AND created_at>=:since AND created_at<:until"
    async with engine.connect() as c:
        summary = await one(
            c,
            f"""SELECT count(*) requests,
            count(*) FILTER(WHERE status='completed') completed,
            count(*) FILTER(WHERE status='failed') failed,
            avg(duration_ms) average_ms,
            count(*) FILTER(WHERE error_code LIKE 'content_blocked%') blocked
            FROM gateway_calls WHERE {where}""",
            **params,
        )
        costs = await one(
            c,
            f"SELECT sum(amount) estimated_cost,count(*) FILTER(WHERE amount IS NULL) unknown_cost_attempts FROM gateway_costs WHERE {where}",
            **params,
        )
        daily = await many(
            c,
            f"""SELECT (created_at AT TIME ZONE 'UTC')::date AS day,count(*) requests,
            count(*) FILTER(WHERE status='failed') failed,avg(duration_ms) average_ms
            FROM gateway_calls WHERE {where} GROUP BY 1 ORDER BY 1""",
            **params,
        )
        cost_days = await many(
            c,
            f"SELECT (created_at AT TIME ZONE 'UTC')::date AS day,sum(amount) estimated_cost FROM gateway_costs WHERE {where} GROUP BY 1",
            **params,
        )
        cost_map = {row["day"]: row["estimated_cost"] for row in cost_days}
        for row in daily:
            row["estimated_cost"] = cost_map.get(row["day"])
        operations = await many(
            c,
            f"SELECT operation,count(*) requests FROM gateway_calls WHERE {where} GROUP BY operation ORDER BY requests DESC",
            **params,
        )
        by_model = await many(
            c,
            """SELECT cost.model_id,m.name,count(*) attempts,sum(cost.amount) estimated_cost,
            count(*) FILTER(WHERE cost.amount IS NULL) unknown_cost_attempts
            FROM gateway_costs cost LEFT JOIN gateway_models m ON m.id=cost.model_id
            WHERE cost.tenant_id=:t AND cost.created_at>=:since AND cost.created_at<:until
            GROUP BY cost.model_id,m.name ORDER BY estimated_cost DESC NULLS LAST""",
            **params,
        )
    for row in [costs, *daily, *by_model]:
        if row["estimated_cost"] is not None:
            row["estimated_cost"] = str(row["estimated_cost"])
    return {
        "since": start,
        "until": end,
        "timezone": "UTC",
        "currency": "CNY",
        "summary": {**summary, **costs},
        "daily": daily,
        "operations": operations,
        "models": by_model,
    }
