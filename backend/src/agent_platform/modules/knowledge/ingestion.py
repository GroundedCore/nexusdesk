import asyncio
import hashlib
import json
from pathlib import PurePath
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field

from agent_platform.platform.persistence.store import (
    DomainError,
    audit,
    execute,
    many,
    one,
    required,
)
from agent_platform.platform.storage.objects import S3Archive

from .service import DocumentInput

FORMATS = {
    ".txt",
    ".md",
    ".pdf",
    ".doc",
    ".docx",
    ".rtf",
    ".odt",
    ".ppt",
    ".pptx",
    ".xls",
    ".xlsx",
    ".png",
    ".jpg",
    ".jpeg",
    ".ofd",
    ".caj",
    ".xps",
}


class ParsedPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    page_num: int = Field(ge=1)
    text: str = Field(max_length=200000)
    elements: list[dict] = Field(default_factory=list, max_length=10000)


class IngestionService:
    def __init__(self, engine, knowledge, settings, client):
        self.engine, self.knowledge, self.settings, self.client = (
            engine,
            knowledge,
            settings,
            client,
        )

    async def create(self, tenant, actor, kid, filename, content):
        filename = filename.replace("\\", "/").rsplit("/", 1)[-1]
        if not filename or len(filename) > 200:
            raise DomainError("invalid_document_filename")
        extension = PurePath(filename).suffix.lower()
        if extension not in FORMATS:
            raise DomainError("unsupported_document_format", 415)
        if not content or len(content) > self.settings.knowledge_upload_bytes:
            raise DomainError("document_size_exceeded", 413)
        if extension not in {".txt", ".md"} and not self.settings.knowledge_parser_url:
            raise DomainError("parser_not_configured", 503)
        digest = hashlib.sha256(content).hexdigest()
        async with self.engine.begin() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM knowledge_bases WHERE tenant_id=:t AND id=:id AND enabled FOR UPDATE",
                    t=tenant,
                    id=kid,
                )
            )
            row = await one(
                c,
                "INSERT INTO knowledge_jobs(id,tenant_id,kb_id,filename,content,content_hash,config) VALUES(:id,:t,:kid,:name,:content,:hash,CAST(:config AS jsonb)) ON CONFLICT(tenant_id,kb_id,content_hash) DO NOTHING RETURNING id,status,created_at",
                id=uuid4(),
                t=tenant,
                kid=kid,
                name=filename,
                content=content,
                hash=digest,
                config=json.dumps(
                    {"parser_url": self.settings.knowledge_parser_url, "protocol": "page-json-v1"}
                ),
            )
            if row is None:
                return await one(
                    c,
                    "SELECT id,status,created_at FROM knowledge_jobs WHERE tenant_id=:t AND kb_id=:kid AND content_hash=:hash",
                    t=tenant,
                    kid=kid,
                    hash=digest,
                )
            await audit(c, tenant, actor, "knowledge.job_created", row["id"])
            return row

    async def list(self, tenant, kid):
        async with self.engine.connect() as c:
            return await many(
                c,
                "SELECT id,filename,status,attempts,error_code,document_id,created_at FROM knowledge_jobs WHERE tenant_id=:t AND kb_id=:kid ORDER BY created_at DESC LIMIT 100",
                t=tenant,
                kid=kid,
            )

    async def cancel(self, tenant, actor, jid):
        async with self.engine.begin() as c:
            row = required(
                await one(
                    c,
                    "UPDATE knowledge_jobs SET cancel_requested=true,status=CASE WHEN status='queued' THEN 'cancelled' ELSE status END,updated_at=now() WHERE id=:id AND tenant_id=:t RETURNING id,status",
                    id=jid,
                    t=tenant,
                )
            )
            await audit(c, tenant, actor, "knowledge.job_cancel_requested", jid)
            return row

    async def retry(self, tenant, actor, jid):
        async with self.engine.begin() as c:
            row = await one(
                c,
                "UPDATE knowledge_jobs SET status='queued',cancel_requested=false,error_code=NULL,owner=NULL,lease_until=NULL,updated_at=now() WHERE id=:id AND tenant_id=:t AND status IN ('failed','cancelled') RETURNING id,status",
                id=jid,
                t=tenant,
            )
            if not row:
                raise DomainError("job_not_retryable", 409)
            await audit(c, tenant, actor, "knowledge.job_retried", jid)
            return row

    async def pages(self, tenant, jid):
        async with self.engine.connect() as c:
            required(
                await one(
                    c,
                    "SELECT id FROM knowledge_jobs WHERE id=:id AND tenant_id=:t",
                    id=jid,
                    t=tenant,
                )
            )
            return await many(
                c,
                "SELECT page_num,payload FROM knowledge_job_pages WHERE job_id=:id ORDER BY page_num",
                id=jid,
            )

    async def run_next(self, tenant):
        owner = uuid4()
        async with self.engine.begin() as c:
            await execute(
                c,
                "UPDATE knowledge_jobs SET status=CASE WHEN cancel_requested THEN 'cancelled' WHEN attempts>=3 THEN 'failed' ELSE 'queued' END,error_code='parser_worker_lost',owner=NULL WHERE tenant_id=:t AND status='running' AND lease_until<now()",
                t=tenant,
            )
            job = await one(
                c,
                "UPDATE knowledge_jobs SET status='running',owner=:owner,attempts=attempts+1,lease_until=now()+interval '120 seconds',updated_at=now() WHERE id=(SELECT id FROM knowledge_jobs WHERE tenant_id=:t AND status='queued' AND NOT cancel_requested ORDER BY created_at FOR UPDATE SKIP LOCKED LIMIT 1) RETURNING *",
                owner=owner,
                t=tenant,
            )
        if not job:
            return False
        error = None
        try:
            async with asyncio.timeout(90):
                if self.settings.knowledge_s3_bucket:
                    await S3Archive(self.settings).put(
                        "knowledge/" + str(job["kb_id"]) + "/" + str(job["id"]) + "/original",
                        bytes(job["content"]),
                    )
                if PurePath(job["filename"]).suffix.lower() in {".txt", ".md"}:
                    pages = [ParsedPage(page_num=1, text=bytes(job["content"]).decode("utf-8-sig"))]
                else:
                    # Fixed deployment endpoint; parser owns format conversion and OCR.
                    async with self.client.stream(
                        "POST",
                        job["config"]["parser_url"].rstrip("/") + "/parse",
                        files={
                            "file": (
                                job["filename"],
                                bytes(job["content"]),
                                "application/octet-stream",
                            )
                        },
                        timeout=60,
                    ) as response:
                        response.raise_for_status()
                        body = bytearray()
                        async for block in response.aiter_bytes():
                            body.extend(block)
                            if len(body) > self.settings.knowledge_upload_bytes:
                                raise DomainError("parser_output_too_large")
                    pages = [ParsedPage.model_validate(p) for p in json.loads(body)["pages"]]
                if (
                    not pages
                    or len(pages) > 10000
                    or sorted(p.page_num for p in pages) != list(range(1, len(pages) + 1))
                ):
                    raise DomainError("invalid_parser_page_numbers")
                for page in pages:
                    async with self.engine.begin() as c:
                        live = required(
                            await one(
                                c,
                                "SELECT * FROM knowledge_jobs WHERE id=:id AND owner=:owner AND status='running' AND lease_until>now() FOR UPDATE",
                                id=job["id"],
                                owner=owner,
                            )
                        )
                        if live["cancel_requested"]:
                            raise DomainError("document_cancelled")
                        await execute(
                            c,
                            "INSERT INTO knowledge_job_pages(job_id,page_num,payload) VALUES(:id,:num,CAST(:p AS jsonb)) ON CONFLICT(job_id,page_num) DO UPDATE SET payload=EXCLUDED.payload",
                            id=job["id"],
                            num=page.page_num,
                            p=page.model_dump_json(),
                        )
                text = "\n\n".join(page.text for page in sorted(pages, key=lambda p: p.page_num))
                async with self.engine.begin() as c:
                    live = required(
                        await one(
                            c,
                            "SELECT * FROM knowledge_jobs WHERE id=:id AND owner=:owner AND status='running' AND lease_until>now() FOR UPDATE",
                            id=job["id"],
                            owner=owner,
                        )
                    )
                    if live["cancel_requested"]:
                        raise DomainError("document_cancelled")
                    await execute(
                        c,
                        "DELETE FROM knowledge_job_pages WHERE job_id=:id AND page_num>:last",
                        id=job["id"],
                        last=len(pages),
                    )
                    saved = await self.knowledge.save_document(
                        tenant,
                        "knowledge-worker",
                        job["kb_id"],
                        DocumentInput(title=job["filename"][:200], content=text),
                        connection=c,
                    )
                    await execute(
                        c,
                        "UPDATE knowledge_jobs SET status='completed',document_id=:doc,lease_until=NULL,updated_at=now() WHERE id=:id AND owner=:owner",
                        id=job["id"],
                        owner=owner,
                        doc=saved["id"],
                    )
        except asyncio.CancelledError:
            error = "worker_cancelled"
            raise
        except Exception as exc:  # noqa: BLE001 -- provider payloads never enter error logs.
            error = getattr(exc, "code", "document_processing_failed")
        finally:
            if error:
                async with self.engine.begin() as c:
                    await execute(
                        c,
                        "UPDATE knowledge_jobs SET status=CASE WHEN cancel_requested THEN 'cancelled' ELSE 'failed' END,error_code=:error,lease_until=NULL,updated_at=now() WHERE id=:id AND owner=:owner AND status='running'",
                        id=job["id"],
                        owner=owner,
                        error=error,
                    )
        return True
