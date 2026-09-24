"""Explicit provider capability mapping; unknown models retain their own defaults."""

from urllib.parse import urlsplit

from agent_platform.platform.persistence.store import DomainError


def is_deepseek(connection):
    return (
        connection["protocol"] == "openai_compatible"
        and urlsplit(connection["base_url"]).hostname == "api.deepseek.com"
    )


def supports_thinking(connection, model):
    return is_deepseek(connection) and model["model_name"] in {
        "deepseek-flash",
        "deepseek-v4-pro",
        "deepseek-v4-flash",
    }


def provider_parameters(route, parameters):
    result = {k: v for k, v in parameters.items() if v is not None}
    mode = result.pop("thinking", None)
    if mode is not None:
        if not supports_thinking(route["connection"], route["model"]):
            raise DomainError("model_thinking_not_supported", 422)
        result["thinking"] = {"type": mode}
    return result


def provider_messages(route, messages, tools):
    result = []
    for message in messages:
        row = {
            k: v
            for k, v in message.items()
            if v is not None and k != "reasoning_content" and (k != "tool_calls" or v)
        }
        if is_deepseek(route["connection"]) and tools and row["role"] == "assistant":
            # Legacy history predates reasoning storage; never invent a reasoning trace.
            row["reasoning_content"] = message.get("reasoning_content") or ""
        result.append(row)
    return result


def reasoning_fields(route, payload):
    value = payload.get("reasoning_content")
    if is_deepseek(route["connection"]) and value is not None:
        if not isinstance(value, str) or len(value) > 1_000_000:
            raise DomainError("invalid_model_response", 502)
        return {"reasoning_content": value}
    return {}
