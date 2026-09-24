import hashlib
import hmac
import importlib.util
import io
import json
from pathlib import Path
from urllib.error import HTTPError

import pytest

spec = importlib.util.spec_from_file_location(
    "nexusdesk", Path(__file__).resolve().parents[2] / "sdk/python/nexusdesk/__init__.py"
)
sdk = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sdk)


def test_python_sdk_events_and_headers():
    client = sdk.Client("http://localhost/openapi/v1", "secret", "employee")
    requests = []

    class Opener:
        def open(self, request, timeout):
            requests.append(request)
            return io.BytesIO(
                'id: 9\r\nevent: run.completed\r\ndata: {"output":\r\ndata: "你好"}\r\n\r\n'.encode()
            )

    client._opener = Opener()
    assert list(client.events("r1", last_event_id=8)) == [
        {"event": "run.completed", "id": "9", "data": {"output": "你好"}}
    ]
    assert requests[0].get_header("Last-event-id") == "8"
    assert requests[0].get_header("X-external-user-id") == "employee"


def test_python_sdk_error_and_write_idempotency():
    client = sdk.Client("http://localhost/openapi/v1", "secret")
    calls = []

    class Opener:
        def open(self, request, timeout):
            calls.append(request)
            raise HTTPError(
                request.full_url,
                429,
                "limited",
                {"X-Request-ID": "req", "Retry-After": "60"},
                io.BytesIO(json.dumps({"error": {"code": "limited"}}).encode()),
            )

    client._opener = Opener()
    with pytest.raises(sdk.APIError) as exc:
        client.send_message("cid", "hi", idempotency_key="same")
    assert (
        exc.value.status == 429 and exc.value.request_id == "req" and exc.value.retry_after == "60"
    )
    assert len(calls) == 1 and calls[0].get_header("Idempotency-key") == "same"
    assert "secret" not in str(exc.value)


def test_python_sdk_webhook_signatures():
    body = b'{"event_id":"e1"}'
    timestamp = "1000"
    signature = (
        "sha256="
        + hmac.new(b"secret", timestamp.encode() + b"." + body, hashlib.sha256).hexdigest()
    )
    assert sdk.verify_webhook(body, signature, timestamp, "secret", now=1000)
    assert not sdk.verify_webhook(b"changed", signature, timestamp, "secret", now=1000)
    assert not sdk.verify_webhook(body, signature, timestamp, "secret", now=1400)
