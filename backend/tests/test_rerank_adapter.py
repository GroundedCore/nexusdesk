import json

import httpx
import pytest

from agent_platform.modules.model_gateway.adapters import invoke_http
from agent_platform.platform.persistence.store import DomainError


@pytest.mark.asyncio
@pytest.mark.parametrize("deepexi", [True, False])
async def test_rerank_maps_provider_indices_to_candidate_ids(deepexi):
    def handler(request):
        assert str(request.url) == "https://example.test/v2/rerank"
        body = json.loads(request.content)
        assert body["documents"] == ["irrelevant", "refund instructions"]
        assert body["top_n"] == 1
        result = (
            {
                "data": [
                    {"index": 0, "rerank": [{"index": 0, "score": 0.1}, {"index": 1, "score": 0.9}]}
                ]
            }
            if deepexi
            else {
                "results": [
                    {"index": 0, "relevance_score": 0.1},
                    {"index": 1, "relevance_score": 0.9},
                ]
            }
        )
        return httpx.Response(200, json=result)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
        payload, _ = await invoke_http(client, ROUTE, REQUEST, {}, "secret", {}, 10000)
    assert payload == {"results": [{"id": "relevant", "score": 0.9}]}


ROUTE = {
    "connection": {"protocol": "openai_compatible", "base_url": "https://example.test/v2"},
    "model": {"model_name": "reranker"},
}
REQUEST = {
    "operation": "rerank",
    "query": "refund",
    "top_n": 1,
    "candidates": [
        {"id": "other", "text": "irrelevant"},
        {"id": "relevant", "text": "refund instructions"},
    ],
}


@pytest.mark.asyncio
@pytest.mark.parametrize("indices", [[-1], [2], [True], [0, 0]])
async def test_rerank_rejects_invalid_indices(indices):
    raw = {"results": [{"index": i, "relevance_score": 0.5} for i in indices]}
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=raw))
    ) as client:
        with pytest.raises(DomainError, match="invalid_rerank_indices"):
            await invoke_http(client, ROUTE, REQUEST, {}, None, {}, 10000)


@pytest.mark.asyncio
async def test_rerank_validates_even_rows_beyond_top_n():
    raw = {
        "results": [{"index": 1, "relevance_score": 0.9}, {"index": 0, "relevance_score": "bad"}]
    }
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=raw))
    ) as client:
        with pytest.raises(DomainError, match="invalid_rerank_scores"):
            await invoke_http(client, ROUTE, REQUEST, {}, None, {}, 10000)
