import asyncio
import json
import re
import time
from uuid import uuid4

from jsonschema import Draft202012Validator

from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)

from .contracts import EvaluationCase
from .scoring import Scorer, ScoringOptions, verdict
from .workbook import build_workbook, parse_workbook

__all__ = ["MAX_CASES", "EvaluationCase", "EvaluationService", "MockTools"]

# One agent never holds more than this many cases. Both the single-create and the
# spreadsheet import path enforce it inside the same row lock on the agent.
MAX_CASES = 20

INVALID_FILENAME = re.compile(r'[\\/:*?"<>|\r\n\t]+')


class MockTools:
    def __init__(self, schemas, responses):
        self.schemas, self.responses, self.calls = schemas, responses, []
        self.violations = []

    async def execute(self, name, args):
        self.calls.append(name)
        schema = next(
            (s["function"]["parameters"] for s in self.schemas if s["function"]["name"] == name),
            None,
        )
        if schema is None or not Draft202012Validator(schema).is_valid(args):
            self.violations.append(name)
            return {"ok": False, "error": "tool_not_allowed_or_invalid_arguments"}
        return self.responses.get(name, {"ok": False, "error": "tool_not_mocked"})


class EvaluationService:
    def __init__(self, engine, agents, factory):
        self.engine, self.agents, self.factory = engine, agents, factory
        self.busy = False

    async def cases(self, tenant, aid):
        async with self.engine.connect() as c:
            return await many(
                c,
                "SELECT * FROM evaluation_cases WHERE tenant_id=:t AND agent_id=:id ORDER BY created_at",
                t=tenant,
                id=aid,
            )

    async def create(self, tenant, actor, aid, body):
        async with self.engine.begin() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM agents WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=aid,
                    t=tenant,
                )
            )
            count = (
                await one(
                    c,
                    "SELECT count(*) AS n FROM evaluation_cases WHERE tenant_id=:t AND agent_id=:id",
                    t=tenant,
                    id=aid,
                )
            )["n"]
            if count >= MAX_CASES:
                raise DomainError("maximum_20_cases_per_agent")
            row = await one(
                c,
                "INSERT INTO evaluation_cases(id,tenant_id,agent_id,name,spec) VALUES(:id,:t,:aid,:name,CAST(:spec AS jsonb)) RETURNING *",
                id=uuid4(),
                t=tenant,
                aid=aid,
                name=body.name,
                spec=body.model_dump_json(),
            )
            await audit(c, tenant, actor, "evaluation.case_created", row["id"])
            return row

    async def template(self, tenant, aid):
        """Current cases (or samples when empty) as a downloadable xlsx payload."""
        async with self.engine.connect() as c:
            agent = required(
                await one(
                    c,
                    "SELECT name FROM agents WHERE id=:id AND tenant_id=:t",
                    id=aid,
                    t=tenant,
                )
            )
            rows = await many(
                c,
                "SELECT name,spec FROM evaluation_cases WHERE tenant_id=:t AND agent_id=:id ORDER BY created_at",
                t=tenant,
                id=aid,
            )
        stem = INVALID_FILENAME.sub("_", agent["name"]).strip()[:60] or "agent"
        return build_workbook([{"name": r["name"], "spec": r["spec"]} for r in rows]), (
            f"{stem}-评测用例.xlsx"
        )

    async def import_workbook(self, tenant, actor, aid, content, filename=""):
        """Merge a filled workbook into the agent's cases, keyed by case name.

        Rows without a name still report their own validation error: a partially filled
        sheet is a normal outcome, so one bad row never discards the good ones.
        """
        parsed = parse_workbook(content)
        async with self.engine.begin() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM agents WHERE id=:id AND tenant_id=:t FOR UPDATE",
                    id=aid,
                    t=tenant,
                )
            )
            existing = await many(
                c,
                "SELECT id,name FROM evaluation_cases WHERE tenant_id=:t AND agent_id=:id",
                t=tenant,
                id=aid,
            )
            ids = {row["name"]: row["id"] for row in existing}
            count, created, updated, errors = len(ids), 0, 0, []
            for entry in parsed:
                if entry["error"]:
                    errors.append(
                        {"row": entry["row"], "name": entry["name"], "error": entry["error"]}
                    )
                    continue
                body = entry["spec"]
                if entry["name"] in ids:
                    await execute(
                        c,
                        """UPDATE evaluation_cases SET name=:name, spec=CAST(:spec AS jsonb)
                        WHERE id=:cid AND tenant_id=:t AND agent_id=:aid""",
                        cid=ids[entry["name"]],
                        t=tenant,
                        aid=aid,
                        name=body.name,
                        spec=body.model_dump_json(),
                    )
                    updated += 1
                elif count >= MAX_CASES:
                    errors.append(
                        {
                            "row": entry["row"],
                            "name": entry["name"],
                            "error": "maximum_20_cases_per_agent",
                        }
                    )
                else:
                    row = await one(
                        c,
                        """INSERT INTO evaluation_cases(id,tenant_id,agent_id,name,spec)
                        VALUES(:id,:t,:aid,:name,CAST(:spec AS jsonb)) RETURNING id""",
                        id=uuid4(),
                        t=tenant,
                        aid=aid,
                        name=body.name,
                        spec=body.model_dump_json(),
                    )
                    ids[entry["name"]] = row["id"]
                    count += 1
                    created += 1
            await audit(
                c,
                tenant,
                actor,
                "evaluation.cases_imported",
                aid,
                created=created,
                updated=updated,
                failed=len(errors),
                filename=filename,
            )
            return {
                "created": created,
                "updated": updated,
                "errors": errors,
                "total": count,
            }

    async def update(self, tenant, actor, aid, cid, body):
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    """UPDATE evaluation_cases SET name=:name, spec=CAST(:spec AS jsonb)
                    WHERE id=:cid AND agent_id=:aid AND tenant_id=:t RETURNING *""",
                    cid=cid,
                    aid=aid,
                    t=tenant,
                    name=body.name,
                    spec=body.model_dump_json(),
                )
            )
            await audit(c, tenant, actor, "evaluation.case_updated", row["id"])
            return row

    async def delete(self, tenant, actor, aid, cid):
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    """DELETE FROM evaluation_cases
                    WHERE id=:cid AND agent_id=:aid AND tenant_id=:t RETURNING id""",
                    cid=cid,
                    aid=aid,
                    t=tenant,
                )
            )
            await audit(c, tenant, actor, "evaluation.case_deleted", row["id"])
            return {"deleted": True}

    async def reports(self, tenant, aid, page=1, page_size=10, agent_version=None):
        page = max(1, int(page))
        page_size = max(1, min(int(page_size), 100))
        clause = "tenant_id=:t AND agent_id=:id"
        params = {"t": tenant, "id": aid}
        if agent_version is not None:
            clause += " AND agent_version=:v"
            params["v"] = agent_version
        async with self.engine.connect() as c:
            total = (
                await one(c, f"SELECT count(*) AS n FROM evaluation_reports WHERE {clause}", **params)
            )["n"]
            items = await many(
                c,
                f"""SELECT * FROM evaluation_reports WHERE {clause}
                ORDER BY created_at DESC LIMIT :lim OFFSET :off""",
                **params,
                lim=page_size,
                off=(page - 1) * page_size,
            )
            versions = await many(
                c,
                """SELECT DISTINCT agent_version FROM evaluation_reports
                WHERE tenant_id=:t AND agent_id=:id ORDER BY agent_version DESC""",
                t=tenant,
                id=aid,
            )
        return {
            "items": items,
            "total": total,
            "page": page,
            "page_size": page_size,
            "versions": [row["agent_version"] for row in versions],
        }

    async def trend(self, tenant, aid, limit=20):
        limit = max(1, min(int(limit), 100))
        async with self.engine.connect() as c:
            rows = await many(
                c,
                """SELECT id,agent_version,created_at,results FROM evaluation_reports
                WHERE tenant_id=:t AND agent_id=:id ORDER BY created_at DESC LIMIT :lim""",
                t=tenant,
                id=aid,
                lim=limit,
            )
        points = []
        for row in reversed(rows):
            results = row["results"] or []
            total = len(results)
            passed = sum(1 for result in results if result.get("passed"))
            points.append(
                {
                    "id": str(row["id"]),
                    "agent_version": row["agent_version"],
                    "created_at": row["created_at"],
                    "total": total,
                    "passed": passed,
                    "pass_rate": (passed / total) if total else 0.0,
                    "needs_review": sum(
                        1 for result in results if result.get("status") == "needs_review"
                    ),
                    "errors": sum(
                        1
                        for result in results
                        if result.get("status") == "error" or result.get("error")
                    ),
                }
            )
        return {"points": points, "limit": limit}

    async def _environment(self, tenant, snapshot, c):
        config = snapshot.get("config") or {}
        knowledge = []
        for kid in config.get("knowledge_base_ids") or []:
            row = await one(
                c,
                """SELECT k.name,
                count(l.document_id) FILTER (WHERE l.enabled AND d.enabled) AS documents,
                count(l.document_id) FILTER (
                    WHERE l.enabled AND d.enabled AND l.published_version IS NOT NULL
                ) AS published
                FROM knowledge_bases k
                LEFT JOIN knowledge_links l ON l.kb_id=k.id
                LEFT JOIN knowledge_documents d ON d.id=l.document_id
                WHERE k.id=:id AND k.tenant_id=:t
                GROUP BY k.name""",
                id=kid,
                t=tenant,
            )
            if row:
                knowledge.append(
                    {
                        "id": str(kid),
                        "name": row["name"],
                        "documents": row["documents"],
                        "published": row["published"],
                    }
                )
        profile_id = config.get("model_profile_id")
        return {
            "agent_version": snapshot["version"],
            "model": {
                "profile_id": str(profile_id) if profile_id else None,
                "version": config.get("model_profile_version"),
            },
            "tools": list(config.get("tool_names") or []),
            "knowledge": knowledge,
            "memory": {
                "enabled": config.get("memory_enabled", True),
                "write": config.get("memory_write", True),
                "summary": config.get("summary_enabled", True),
            },
        }

    async def _evaluate_case(self, tenant, snapshot, row, options, deadline):
        spec = EvaluationCase.model_validate(row["spec"])
        events = []

        async def emit(kind, data):
            events.append(kind)

        runtime, mocks = self.factory.evaluation(snapshot, spec.mock_tools)
        error = None
        try:
            async with asyncio.timeout(max(0, deadline - time.monotonic())):
                output, _, _ = await runtime.run(spec.input, [], emit)
        except Exception as exc:  # noqa: BLE001 - isolate evaluation cases
            output, error = "", getattr(exc, "code", type(exc).__name__)
        passed = (
            error is None
            and not mocks.violations
            and all(s in output for s in spec.expected_contains)
            and all(s not in output for s in spec.forbidden_contains)
            and (spec.expected_tools is None or spec.expected_tools == mocks.calls)
        )
        judge, judge_error, similarity = None, None, None
        scorer = Scorer(self.factory.platform.gateway)
        if options.judge and error is None:
            try:
                async with asyncio.timeout(max(0, deadline - time.monotonic())):
                    judge = await scorer.judge(
                        tenant,
                        options.judge,
                        spec.input,
                        output,
                        {"tool:" + k: v for k, v in spec.mock_tools.items()},
                        spec.reference_answers,
                    )
            except (DomainError, ValueError, TimeoutError) as exc:
                judge_error = getattr(exc, "code", "judge_response_invalid")
        if options.embedding and error is None:
            try:
                async with asyncio.timeout(max(0, deadline - time.monotonic())):
                    similarity = await scorer.similarity(
                        tenant, options.embedding, output, spec.reference_answers
                    )
            except (DomainError, ValueError, TimeoutError) as exc:
                similarity = {
                    "status": "error",
                    "error": getattr(exc, "code", "invalid_embedding_response"),
                }
        status = verdict(passed, error, judge, judge_error)
        return {
            "case_id": str(row["id"]),
            "name": row["name"],
            "passed": status == "passed",
            "status": status,
            "rules_passed": passed,
            "rule_violations": mocks.violations,
            "judge": judge,
            "judge_error": judge_error,
            "similarity": similarity,
            "scoring": options.model_dump(mode="json"),
            "case_snapshot": row["spec"],
            "agent_snapshot": snapshot,
            "output": output,
            "tools": mocks.calls,
            "error": error,
        }

    async def run(self, tenant, actor, aid, options=None):
        options = options or ScoringOptions()
        if self.busy:
            raise DomainError("evaluation_busy", 429)
        self.busy = True
        try:
            async with self.engine.begin() as c:
                snapshot = await self.agents.snapshot(tenant, aid, c)
                cases = await many(
                    c,
                    "SELECT * FROM evaluation_cases WHERE tenant_id=:t AND agent_id=:id ORDER BY created_at LIMIT 20",
                    t=tenant,
                    id=aid,
                )
                environment = await self._environment(tenant, snapshot, c)
            if not cases:
                raise DomainError("evaluation_cases_required")
            deadline = time.monotonic() + options.budget_seconds
            results = [
                await self._evaluate_case(tenant, snapshot, row, options, deadline) for row in cases
            ]
            async with self.engine.begin() as c:
                report = await one(
                    c,
                    """INSERT INTO evaluation_reports(id,tenant_id,agent_id,agent_version,results,environment)
                    VALUES(:id,:t,:aid,:v,CAST(:results AS jsonb),CAST(:environment AS jsonb)) RETURNING *""",
                    id=uuid4(),
                    t=tenant,
                    aid=aid,
                    v=snapshot["version"],
                    results=json.dumps(results),
                    environment=json.dumps(environment),
                )
                await audit(
                    c,
                    tenant,
                    actor,
                    "evaluation.completed",
                    report["id"],
                    passed=sum(r["passed"] for r in results),
                    total=len(results),
                )
                return report
        except TimeoutError:
            raise DomainError("evaluation_timeout", 504) from None
        finally:
            self.busy = False

    async def run_case(self, tenant, actor, aid, cid, options=None):
        options = options or ScoringOptions()
        if self.busy:
            raise DomainError("evaluation_busy", 429)
        self.busy = True
        try:
            async with self.engine.begin() as c:
                snapshot = await self.agents.snapshot(tenant, aid, c)
                row = required(
                    await one(
                        c,
                        """SELECT * FROM evaluation_cases
                        WHERE id=:cid AND agent_id=:aid AND tenant_id=:t""",
                        cid=cid,
                        aid=aid,
                        t=tenant,
                    )
                )
            deadline = time.monotonic() + options.budget_seconds
            try:
                result = await self._evaluate_case(tenant, snapshot, row, options, deadline)
            except TimeoutError:
                raise DomainError("evaluation_timeout", 504) from None
            async with self.engine.begin() as c:
                await audit(
                    c,
                    tenant,
                    actor,
                    "evaluation.case_run",
                    str(row["id"]),
                    passed=result["passed"],
                    status=result["status"],
                )
            return {"agent_version": snapshot["version"], "result": result}
        finally:
            self.busy = False
