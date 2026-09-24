"""Offline, bounded OpenAPI import. No remote references or upstream requests."""

import json
import re

import yaml

from agent_platform.platform.persistence.store import DomainError


def preview(content):
    if len(content.encode()) > 1_000_000:
        raise DomainError("openapi_file_too_large", 422)
    try:
        doc = yaml.safe_load(content)
        # Bound expanded YAML aliases before serialization allocates their expansion.
        remaining = 20000
        active = set()

        def inspect(value, depth=0):
            nonlocal remaining
            remaining -= 1
            if remaining < 0 or depth > 32:
                raise ValueError()
            if isinstance(value, (dict, list)):
                identity = id(value)
                if identity in active:
                    raise ValueError()
                active.add(identity)
                for child in value.values() if isinstance(value, dict) else value:
                    inspect(child, depth + 1)
                active.remove(identity)

        inspect(doc)
        encoded = json.dumps(doc)
        if (
            len(encoded) > 2_000_000
            or not isinstance(doc, dict)
            or not str(doc.get("openapi", "")).startswith("3.")
        ):
            raise ValueError()
    except (ValueError, TypeError, RecursionError, yaml.YAMLError):
        raise DomainError("invalid_openapi_document", 422) from None

    def dereference(value):
        seen = set()
        while isinstance(value, dict) and "$ref" in value:
            ref = value["$ref"]
            if (
                not isinstance(ref, str)
                or not ref.startswith("#/")
                or ref in seen
                or len(seen) > 15
            ):
                raise ValueError("不支持外部或循环引用")
            seen.add(ref)
            value = doc
            for part in ref[2:].split("/"):
                value = value[part.replace("~1", "/").replace("~0", "~")]
        return value

    def expand_schema(value, refs=(), depth=0):
        if depth > 24:
            raise ValueError("Schema 嵌套过深")
        if isinstance(value, dict):
            if "$ref" in value:
                ref = value["$ref"]
                if ref in refs:
                    raise ValueError("不支持循环 Schema 引用")
                return expand_schema(dereference(value), (*refs, ref), depth + 1)
            return {k: expand_schema(v, refs, depth + 1) for k, v in value.items()}
        if isinstance(value, list):
            return [expand_schema(v, refs, depth + 1) for v in value]
        return value

    paths = doc.get("paths", {})
    if not isinstance(paths, dict) or len(paths) > 500:
        raise DomainError("invalid_openapi_paths", 422)
    items, skipped, names = [], [], set()
    for path, raw in paths.items():
        try:
            item = dereference(raw)
            if not isinstance(item, dict):
                raise TypeError("无效路径定义")
            for method, operation in item.items():
                if method not in {
                    "get",
                    "post",
                    "put",
                    "patch",
                    "delete",
                    "head",
                    "options",
                    "trace",
                }:
                    continue
                reason = None
                if method not in {"get", "post"}:
                    reason = "当前仅支持 GET 和 POST"
                elif "{" in path or "}" in path:
                    reason = "暂不支持动态路径参数"
                if reason:
                    skipped.append({"path": path, "method": method.upper(), "reason": reason})
                    continue
                try:
                    operation = dereference(operation)
                    properties, required = {}, []
                    if method == "get" and operation.get("requestBody"):
                        raise ValueError("GET 请求体暂不支持")
                    if operation.get("servers") or item.get("servers"):
                        raise ValueError("操作级服务地址需手动配置")
                    for param in item.get("parameters", []) + operation.get("parameters", []):
                        if method == "post":
                            raise ValueError("POST 参数请使用 JSON 请求体，暂不支持混合 query 参数")
                        param = dereference(param)
                        if param.get("in") != "query":
                            raise ValueError("仅支持 query 参数，其他参数请手动配置")
                        schema = dereference(param.get("schema", {}))
                        if schema.get("type") not in {"string", "integer", "number", "boolean"}:
                            raise ValueError("仅支持标量查询参数")
                        if "$ref" in json.dumps(schema):
                            raise ValueError("嵌套 Schema 引用需手动展开")
                        name = param["name"]
                        properties[name] = {
                            **schema,
                            **(
                                {"description": param["description"]}
                                if param.get("description")
                                else {}
                            ),
                        }
                        if param.get("required"):
                            required.append(name)
                    parameters = {
                        "type": "object",
                        "properties": properties,
                        "required": list(dict.fromkeys(required)),
                        "additionalProperties": False,
                    }
                    if method == "post" and operation.get("requestBody"):
                        request_body = dereference(operation["requestBody"])
                        media = request_body.get("content", {}).get("application/json")
                        if not media:
                            raise ValueError("POST 仅支持 application/json 请求体")
                        parameters = expand_schema(media.get("schema", {}))
                        if parameters.get("type") != "object":
                            raise ValueError("POST 请求体必须是 JSON 对象")
                        parameters.setdefault("additionalProperties", False)
                    name = re.sub(
                        r"[^a-zA-Z0-9_]",
                        "_",
                        operation.get("operationId") or method + "_" + path.strip("/"),
                    )[:55]
                    if not name or not name[0].isalpha():
                        name = "api_" + name
                    original = name
                    n = 2
                    while name in names:
                        name = original + "_" + str(n)
                        n += 1
                    names.add(name)
                    items.append(
                        {
                            "name": name,
                            "display_name": str(operation.get("summary") or name)[:64],
                            "description": str(
                                operation.get("description") or operation.get("summary") or name
                            )[:1000],
                            "relative_path": path,
                            "method": method.upper(),
                            "parameters": parameters,
                        }
                    )
                except (ValueError, KeyError, TypeError, AttributeError) as exc:
                    skipped.append(
                        {"path": path, "method": method.upper(), "reason": str(exc)[:150]}
                    )
        except (ValueError, KeyError, TypeError, AttributeError):
            skipped.append({"path": str(path), "method": "—", "reason": "无法解析路径定义"})
    servers = doc.get("servers") or []
    if not isinstance(servers, list):
        raise DomainError("invalid_openapi_document", 422)
    base = servers[0].get("url", "") if servers and isinstance(servers[0], dict) else ""
    if not isinstance(base, str):
        raise DomainError("invalid_openapi_document", 422)
    info = doc.get("info") if isinstance(doc.get("info"), dict) else {}
    return {
        "name": str(info.get("title") or "导入工具集")[:64],
        "description": str(info.get("description") or "")[:200],
        "base_url": base,
        "items": items[:100],
        "skipped": skipped,
        "truncated": len(items) > 100,
    }


def export_document(group, rows):
    paths = {}
    for row in rows:
        spec = row["spec"]
        schema = spec.get("parameters", {})
        path = row["relative_path"]
        method = spec.get("method", "GET").lower()
        operations = paths.setdefault(path, {})
        if method in operations:
            raise DomainError("duplicate_paths_cannot_export_openapi", 409)
        operation = {
            "operationId": row["name"],
            "summary": row["display_name"],
            "description": spec["description"],
            "responses": {
                "200": {
                    "description": "Successful JSON response",
                    "content": {"application/json": {"schema": spec.get("response_schema") or {}}},
                }
            },
        }
        if method == "post":
            operation["requestBody"] = {
                "required": True,
                "content": {"application/json": {"schema": schema}},
            }
        else:
            operation["parameters"] = [
                {
                    "name": name,
                    "in": "query",
                    "required": name in schema.get("required", []),
                    "schema": value,
                }
                for name, value in schema.get("properties", {}).items()
            ]
        operations[method] = operation
    return {
        "openapi": "3.0.3",
        "info": {"title": group["name"], "description": group["description"], "version": "1.0.0"},
        "servers": [{"url": group["spec"]["url"]}],
        "paths": paths,
    }
