"""Embedding-based grouping within structural boundaries; table rows remain intact."""

import math

from agent_platform.modules.model_gateway.contracts import EmbedRequest
from agent_platform.platform.persistence.store import DomainError

from .chunking import chunk_pages


def cosine(left, right):
    norm = math.sqrt(sum(x * x for x in left) * sum(x * x for x in right))
    return sum(a * b for a, b in zip(left, right, strict=True)) / norm if norm else 0.0


async def semantic_chunks(pages, policy, gateway, tenant, progress=None):
    snapshot = await gateway.catalog.resolve(
        tenant, policy.semantic_profile_id, policy.semantic_profile_version
    )
    if snapshot["spec"]["operation"] != "embed":
        raise DomainError("semantic_embedding_profile_required", 422)
    # Use bounded paragraph/sentence-like units, retaining headings and page boundaries.
    unit_policy = policy.model_copy(
        update={"strategy": "hybrid", "size": min(300, policy.size), "overlap": 0}
    )
    units = []
    for page in pages:
        table = bool(page.get("elements")) and all(
            e.get("type") == "table" for e in page["elements"]
        )
        selected = (
            policy.model_copy(update={"strategy": "hybrid", "overlap": 0}) if table else unit_policy
        )
        units.extend((chunk, table) for chunk in chunk_pages([page], selected))
    if len(units) > 20000:
        raise DomainError("semantic_too_many_units", 413)
    vectors = {}
    inputs = [(i, chunk) for i, (chunk, table) in enumerate(units) if not table]
    batch_size = min(16, *(route["model"]["max_batch"] for route in snapshot["routes"]))
    for offset in range(0, len(inputs), batch_size):
        if progress:
            await progress("semantic_embedding", offset, len(inputs))
        batch = inputs[offset : offset + batch_size]
        result = await gateway.invoke(
            tenant,
            policy.semantic_profile_id,
            policy.semantic_profile_version,
            EmbedRequest(inputs=[{"id": str(i), "text": chunk["content"]} for i, chunk in batch]),
        )
        vectors.update({int(v["id"]): v["vector"] for v in result["payload"]["vectors"]})
    if progress:
        await progress("semantic_embedding", len(inputs), len(inputs))
    result = []
    previous = None
    for i, (chunk, table) in enumerate(units):
        source = chunk["source"]
        key = {k: v for k, v in source.items() if k not in {"start", "end"}}
        if (
            not table
            and previous is not None
            and previous[1] == key
            and len(result[-1]["content"]) + len(chunk["content"]) + 2 <= policy.size
            and cosine(vectors[previous[0]], vectors[i]) >= policy.semantic_threshold
        ):
            result[-1]["content"] += "\n\n" + chunk["content"]
            result[-1]["source"]["end"] = source.get("end")
        else:
            result.append({"content": chunk["content"], "source": dict(source), "enabled": True})
        previous = None if table else (i, key)
    return result
