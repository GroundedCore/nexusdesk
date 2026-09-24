"""Read provider model identifiers without exposing credentials or provider errors."""

import asyncio
import json

import httpx

from agent_platform.platform.persistence.store import DomainError


async def discover_models(gateway, tenant, identifier):
    async with gateway.engine.connect() as c:
        row = await gateway.catalog.get(c, tenant, "connections", identifier, True)
    spec = row["spec"]
    if spec["protocol"] != "openai_compatible":
        raise DomainError("model_discovery_not_supported", 422)
    gateway.catalog.validate_address(spec)
    credential = await gateway.catalog.credential_for(tenant, identifier, spec)
    headers = {"Authorization": "Bearer " + credential} if credential else {}
    try:
        async with asyncio.timeout(15):
            async with gateway.client.stream(
                "GET",
                spec["base_url"].rstrip("/") + "/models",
                headers=headers,
                follow_redirects=False,
            ) as response:
                if response.status_code in (401, 403):
                    raise DomainError("model_discovery_unauthorized", 422)
                if response.status_code in (404, 405, 501):
                    raise DomainError("model_discovery_not_supported", 422)
                response.raise_for_status()
                content = bytearray()
                async for chunk in response.aiter_bytes():
                    content.extend(chunk)
                    if len(content) > min(gateway.settings.model_gateway_response_bytes, 2_000_000):
                        raise DomainError("model_discovery_response_too_large", 502)
                payload = json.loads(content)
        if not isinstance(payload, dict) or not isinstance(payload.get("data"), list):
            raise TypeError("invalid model list")
        identifiers = sorted(
            {
                item["id"]
                for item in payload["data"]
                if isinstance(item, dict)
                and isinstance(item.get("id"), str)
                and 0 < len(item["id"]) <= 200
                and item["id"].strip()
                and not any(ord(ch) < 32 for ch in item["id"])
            }
        )
        return {
            "items": [{"id": value} for value in identifiers[:1000]],
            "truncated": len(identifiers) > 1000 or bool(payload.get("has_more")),
        }
    except (httpx.HTTPError, TimeoutError):
        raise DomainError("model_discovery_unavailable", 502) from None
    except (ValueError, TypeError):
        raise DomainError("model_discovery_invalid_response", 502) from None
