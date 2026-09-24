"""Adapters consume validated requests; no arbitrary URLs or provider kwargs."""

import base64
import hashlib
import io
import json
import math
import wave

from langchain_core.messages import AIMessage, HumanMessage, SystemMessage, ToolMessage

from agent_platform.platform.persistence.store import DomainError

from .service import DemoModel
from .thinking import provider_messages, provider_parameters, reasoning_fields


def from_messages(messages):
    result = []
    for item in messages:
        role = {"human": "user", "ai": "assistant"}.get(item.type, item.type)
        row = {"role": role, "content": item.content}
        if role == "assistant" and "reasoning_content" in item.additional_kwargs:
            row["reasoning_content"] = item.additional_kwargs["reasoning_content"]
        if role == "tool":
            row["tool_call_id"] = item.tool_call_id
        if role == "assistant" and item.tool_calls:
            row["tool_calls"] = [
                {
                    "id": t["id"],
                    "type": "function",
                    "function": {"name": t["name"], "arguments": json.dumps(t["args"])},
                }
                for t in item.tool_calls
            ]
        result.append(row)
    return result


def to_messages(messages):
    result = []
    for item in messages:
        role, content = item["role"], item["content"]
        if role == "tool":
            result.append(ToolMessage(content=content, tool_call_id=item.get("tool_call_id") or ""))
        elif role == "assistant":
            calls = [
                {
                    "id": t["id"],
                    "name": t["function"]["name"],
                    "args": json.loads(t["function"]["arguments"]),
                }
                for t in item.get("tool_calls", [])
            ]
            result.append(
                AIMessage(
                    content=content,
                    tool_calls=calls,
                    additional_kwargs={"reasoning_content": item["reasoning_content"]}
                    if item.get("reasoning_content") is not None
                    else {},
                )
            )
        else:
            result.append((HumanMessage if role == "user" else SystemMessage)(content=content))
    return result


async def demo(route, request, media):
    op = request["operation"]
    if op == "chat":
        reply = await DemoModel(request["tools"]).ainvoke(to_messages(request["messages"]))
        return {"content": reply.content, "tool_calls": reply.tool_calls}, None
    if op == "embed":
        dimension = route["model"]["embedding_dimension"]
        vectors = []
        for item in request["inputs"]:
            digest = hashlib.shake_256(item["text"].encode()).digest(dimension)
            vector = [(v - 127.5) / 127.5 for v in digest]
            norm = math.sqrt(sum(v * v for v in vector))
            vectors.append({"id": item["id"], "vector": [v / norm for v in vector]})
        return {"vectors": vectors}, None
    if op == "rerank":
        rows = [
            {
                "id": x["id"],
                "score": len(set(request["query"]) & set(x["text"]))
                / max(1, len(set(request["query"]))),
            }
            for x in request["candidates"]
        ]
        return {
            "results": sorted(rows, key=lambda x: x["score"], reverse=True)[: request["top_n"]]
        }, None
    if op == "transcribe":
        return {"text": "[演示模式] 此结果不是音频识别结果。"}, None
    if op == "synthesize":
        if request["format"] != "wav":
            raise DomainError("demo_tts_requires_wav")
        buffer = io.BytesIO()
        with wave.open(buffer, "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(16000)
            output.writeframes(b"\0\0" * 1600)
        return {"audio_bytes": buffer.getvalue(), "mime_type": "audio/wav"}, None
    return {
        "pages": [
            {"source_id": p["source_id"], "text": "[演示模式] 此结果不是 OCR 识别结果。"}
            for p in request["pages"]
        ]
    }, None


async def request_bytes(client, url, headers, max_bytes, **kwargs):
    async with client.stream("POST", url, headers=headers, **kwargs) as response:
        response.raise_for_status()
        data = bytearray()
        async for chunk in response.aiter_bytes():
            data.extend(chunk)
            if len(data) > max_bytes:
                raise DomainError("model_response_too_large", 502)
        return bytes(data)


async def invoke_http(client, route, request, media, credential, parameters, max_bytes):
    parameters = provider_parameters(route, parameters)
    connection, model = route["connection"], route["model"]["model_name"]
    headers = {"Authorization": "Bearer " + credential} if credential else {}
    base = connection["base_url"].rstrip("/")
    op = request["operation"]
    if connection["protocol"] == "gateway_http":
        body = {
            "model": model,
            "request": request,
            "parameters": parameters,
            "media": {
                key: {
                    "mime_type": value["mime_type"],
                    "base64": base64.b64encode(value["content"]).decode(),
                }
                for key, value in media.items()
            },
        }
        result = json.loads(
            await request_bytes(client, base + "/" + op, headers, max_bytes, json=body)
        )
        payload = result["payload"]
        if op == "synthesize":
            payload = {
                "audio_bytes": base64.b64decode(payload["audio_base64"], validate=True),
                "mime_type": payload["mime_type"],
            }
        return payload, result.get("usage")
    if op == "chat":
        messages = provider_messages(route, request["messages"], request["tools"])
        body = {"model": model, "messages": messages, **parameters}
        if request["tools"]:
            body["tools"] = request["tools"]
        raw = json.loads(
            await request_bytes(client, base + "/chat/completions", headers, max_bytes, json=body)
        )
        message = raw["choices"][0]["message"]
        calls = [
            {
                "id": t["id"],
                "name": t["function"]["name"],
                "args": json.loads(t["function"]["arguments"]),
            }
            for t in message.get("tool_calls", [])
        ]
        return {
            "content": message.get("content") or "",
            "tool_calls": calls,
            **reasoning_fields(route, message),
        }, raw.get("usage")
    if op == "embed":
        raw = json.loads(
            await request_bytes(
                client,
                base + "/embeddings",
                headers,
                max_bytes,
                json={
                    "model": model,
                    "input": [x["text"] for x in request["inputs"]],
                    "encoding_format": "float",
                },
            )
        )
        data = sorted(raw["data"], key=lambda x: x["index"])
        if [x["index"] for x in data] != list(range(len(request["inputs"]))):
            raise DomainError("invalid_embedding_indices", 502)
        return {
            "vectors": [
                {"id": request["inputs"][i]["id"], "vector": x["embedding"]}
                for i, x in enumerate(data)
            ]
        }, raw.get("usage")
    if op == "rerank":
        raw = json.loads(
            await request_bytes(
                client,
                base + "/rerank",
                headers,
                max_bytes,
                json={
                    "model": model,
                    "query": request["query"],
                    "documents": [x["text"] for x in request["candidates"]],
                    "top_n": request["top_n"],
                },
            )
        )
        # Standard rerank APIs and Deepexi v2 use different response envelopes.
        if "results" in raw:
            rows, score_key = raw["results"], "relevance_score"
        else:
            batches = raw["data"]
            if len(batches) != 1 or batches[0]["index"] != 0:
                raise DomainError("invalid_rerank_response", 502)
            rows, score_key = batches[0]["rerank"], "score"
        indices = [x["index"] for x in rows]
        if any(
            type(i) is not int or not 0 <= i < len(request["candidates"]) for i in indices
        ) or len(set(indices)) != len(indices):
            raise DomainError("invalid_rerank_indices", 502)
        if any(
            type(x[score_key]) not in (int, float) or not math.isfinite(x[score_key]) for x in rows
        ):
            raise DomainError("invalid_rerank_scores", 502)
        # Some providers ignore top_n and return every candidate. Validate all rows before slicing.
        rows = sorted(rows, key=lambda x: x[score_key], reverse=True)[: request["top_n"]]
        return {
            "results": [
                {"id": request["candidates"][x["index"]]["id"], "score": x[score_key]} for x in rows
            ]
        }, raw.get("usage")
    if op == "transcribe":
        artifact = media[request["media_id"]]
        extension = {
            "audio/wav": "wav",
            "audio/mpeg": "mp3",
            "audio/ogg": "ogg",
            "audio/webm": "webm",
        }[artifact["mime_type"]]
        data = {"model": model, "response_format": "json"}
        if request.get("language"):
            data["language"] = request["language"]
        raw = json.loads(
            await request_bytes(
                client,
                base + "/audio/transcriptions",
                headers,
                max_bytes,
                data=data,
                files={"file": ("audio." + extension, artifact["content"], artifact["mime_type"])},
            )
        )
        return {"text": raw["text"]}, raw.get("usage")
    if op == "synthesize":
        content = await request_bytes(
            client,
            base + "/audio/speech",
            headers,
            max_bytes,
            json={
                "model": model,
                "input": request["text"],
                "voice": request["voice_id"],
                "response_format": request["format"],
            },
        )
        return {
            "audio_bytes": content,
            "mime_type": "audio/wav" if request["format"] == "wav" else "audio/mpeg",
        }, None
    raise DomainError("protocol_operation_not_supported")


async def stream_chat_http(client, route, request, credential, parameters, max_bytes, emit):
    """OpenAI-compatible SSE with bounded buffering and backpressure."""
    parameters = provider_parameters(route, parameters)
    messages = provider_messages(route, request["messages"], request["tools"])
    reasoning = []
    body = {
        "model": route["model"]["model_name"],
        "messages": messages,
        "stream": True,
        "stream_options": {"include_usage": True},
        **parameters,
    }
    if request["tools"]:
        body["tools"] = request["tools"]
    headers = {"Authorization": "Bearer " + credential} if credential else {}
    content, calls, usage, buffer, size, finished = [], {}, None, b"", 0, False
    async with client.stream(
        "POST",
        route["connection"]["base_url"].rstrip("/") + "/chat/completions",
        headers=headers,
        json=body,
    ) as response:
        response.raise_for_status()
        async for chunk in response.aiter_bytes():
            size += len(chunk)
            if size > max_bytes:
                raise DomainError("model_response_too_large", 502)
            buffer += chunk
            while b"\n" in buffer:
                line, buffer = buffer.split(b"\n", 1)
                line = line.rstrip(b"\r")
                if not line.startswith(b"data:"):
                    continue
                data = line[5:].strip()
                if data == b"[DONE]":
                    finished = True
                    break
                event = json.loads(data)
                if event.get("usage") is not None:
                    usage = event["usage"]
                for choice in event.get("choices", []):
                    if choice.get("index", 0) != 0:
                        continue
                    delta = choice.get("delta", {})
                    if "reasoning_content" in delta:
                        fields = reasoning_fields(route, delta)
                        if fields:
                            reasoning.append(fields["reasoning_content"])
                    if delta.get("content"):
                        content.append(delta["content"])
                    for item in delta.get("tool_calls", []):
                        index = item["index"]
                        if not isinstance(index, int) or not 0 <= index < 30:
                            raise ValueError("tool index")
                        call = calls.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        call["id"] += item.get("id", "")
                        call["name"] += item.get("function", {}).get("name", "")
                        call["arguments"] += item.get("function", {}).get("arguments", "")
                    if delta.get("content") or delta.get("tool_calls"):
                        await emit({k: delta[k] for k in ("content", "tool_calls") if k in delta})
            if finished:
                break
    if not finished:
        raise DomainError("model_stream_interrupted", 502)
    return {
        "content": "".join(content),
        **(reasoning_fields(route, {"reasoning_content": "".join(reasoning)}) if reasoning else {}),
        "tool_calls": [
            {"id": c["id"], "name": c["name"], "args": json.loads(c["arguments"])}
            for _, c in sorted(calls.items())
        ],
    }, usage
