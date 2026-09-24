import asyncio

import pytest
from test_platform_postgres import platform as _platform_fixture

from agent_platform.platform.persistence.store import DomainError

platform = _platform_fixture

ROOT = "/api/v1/knowledge-workspace"


@pytest.mark.asyncio
@pytest.mark.postgres
async def test_preview_task_progress_cancel_and_paging(platform):
    client, services, settings = platform
    w = services.platform.knowledge_workspace
    upload = await client.post(
        ROOT + "/uploads?filename=task.md", content=("段落内容。" * 500).encode()
    )
    did = upload.json()["id"]
    await w.run_next(settings.tenant_id)
    path = ROOT + f"/documents/{did}"

    async def start():
        return await client.post(
            path + "/preview-tasks?revision=1", json={"size": 200, "overlap": 20}
        )

    first = (await start()).json()["task_id"]
    assert (await start()).status_code == 409
    await client.post(ROOT + f"/tasks/{first}/cancel")
    assert (await client.get(ROOT + f"/tasks/{first}/result")).status_code == 409
    task_id = (await start()).json()["task_id"]
    entered, resume = asyncio.Event(), asyncio.Event()
    generate = w.generate_chunks

    async def controlled(*args, **kwargs):
        await kwargs["progress"]("semantic_embedding", 16, 32)
        entered.set()
        await resume.wait()
        await kwargs["progress"]("semantic_embedding", 32, 32)
        raise AssertionError("cancelled work must not continue")

    w.generate_chunks = controlled
    worker = asyncio.create_task(w.run_next(settings.tenant_id))
    try:
        await asyncio.wait_for(entered.wait(), 10)
        task = (await client.get(path + "/tasks")).json()[0]
        assert task["progress"]["completed"] == 16
        await client.post(ROOT + f"/tasks/{task_id}/cancel")
        resume.set()
        await worker
    finally:
        resume.set()
        await asyncio.gather(worker, return_exceptions=True)
        w.generate_chunks = generate
    assert (await client.get(path + "/tasks")).json()[0]["status"] == "cancelled"
    assert (await client.get(path)).json()["current_version"] == 1
    task_id = (await start()).json()["task_id"]
    await w.run_next(settings.tenant_id)
    assert (await client.get(path + "/tasks")).json()[0]["status"] == "completed"
    result = (await client.get(ROOT + f"/tasks/{task_id}/result?limit=1&offset=1")).json()
    assert len(result["chunks"]) == 1 and result["total"] > 1
    with pytest.raises(DomainError):
        await w.preview_result("other-tenant", task_id, 0, 20)
    saved = await client.put(path + "/draft", json={"revision": 1, "policy": result["policy"]})
    assert saved.status_code == 200
    assert (await client.get(ROOT + f"/tasks/{task_id}/result")).status_code == 409
