import asyncio
import json
import time
from uuid import uuid4

from jsonschema import Draft202012Validator
from pydantic import BaseModel, ConfigDict, Field, model_validator

from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    many,
    one,
    required,
)

from .scoring import Scorer, ScoringOptions, verdict


class EvaluationCase(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str = Field(min_length=1, max_length=100)
    input: str = Field(min_length=1, max_length=8000)
    expected_contains: list[str] = Field(default_factory=list, max_length=20)
    forbidden_contains: list[str] = Field(default_factory=list, max_length=20)
    expected_tools: list[str] | None = None
    mock_tools: dict = Field(default_factory=dict)
    reference_answers: list[str] = Field(default_factory=list, max_length=10)

    @model_validator(mode="after")
    def valid(self):
        if (
            not self.expected_contains
            and not self.forbidden_contains
            and self.expected_tools is None
        ):
            raise ValueError("At least one assertion is required")
        if len(json.dumps(self.mock_tools)) > 50000:
            raise ValueError("Mock responses too large")
        if any(not s.strip() or len(s) > 8000 for s in self.reference_answers):
            raise ValueError("Reference answers must be nonempty and at most 8000 characters")
        return self


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
            if count >= 20:
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

    async def reports(self, tenant, aid):
        async with self.engine.connect() as c:
            return await many(
                c,
                "SELECT * FROM evaluation_reports WHERE tenant_id=:t AND agent_id=:id ORDER BY created_at DESC LIMIT 20",
                t=tenant,
                id=aid,
            )

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
            if not cases:
                raise DomainError("evaluation_cases_required")
            results = []
            deadline = time.monotonic() + options.budget_seconds
            for row in cases:
                spec = EvaluationCase.model_validate(row["spec"])
                events = []

                async def emit(kind, data, target=events):
                    target.append(kind)

                runtime, mocks = self.factory.evaluation(snapshot, spec.mock_tools)
                error = None
                try:
                    async with asyncio.timeout(max(0, deadline - time.monotonic())):
                        output, _ = await runtime.run(spec.input, [], emit)
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
                        judge_error = getattr(exc, "code", "invalid_judge_response")
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
                results.append(
                    {
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
                )
            async with self.engine.begin() as c:
                report = await one(
                    c,
                    """INSERT INTO evaluation_reports(id,tenant_id,agent_id,agent_version,results)
                    VALUES(:id,:t,:aid,:v,CAST(:results AS jsonb)) RETURNING *""",
                    id=uuid4(),
                    t=tenant,
                    aid=aid,
                    v=snapshot["version"],
                    results=json.dumps(results),
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
