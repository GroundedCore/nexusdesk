from agent_platform.platform.persistence.audit_context import client_ip


def scope(peer, **headers):
    return {
        "type": "http",
        "client": (peer, 1234),
        "headers": [
            (name.replace("_", "-").encode(), value.encode()) for name, value in headers.items()
        ],
    }


def test_public_peer_cannot_spoof_forwarding_headers():
    request = scope(
        "198.51.100.20",
        x_forwarded_for="203.0.113.11",
        x_real_ip="203.0.113.12",
    )
    assert client_ip(request) == "198.51.100.20"


def test_trusted_proxy_prefers_real_ip():
    request = scope(
        "172.18.0.3",
        x_forwarded_for="203.0.113.10, 172.18.0.2",
        x_real_ip="203.0.113.11",
    )
    assert client_ip(request) == "203.0.113.11"


def test_trusted_proxy_falls_back_to_first_forwarded_address():
    request = scope(
        "172.18.0.3",
        x_forwarded_for="203.0.113.11, 172.18.0.2",
    )
    assert client_ip(request) == "203.0.113.11"


def test_invalid_or_missing_headers_fall_back_to_peer():
    assert client_ip(scope("172.18.0.3", x_real_ip="not-an-ip")) == "172.18.0.3"
    assert client_ip(scope("172.18.0.3", x_forwarded_for="not-an-ip")) == "172.18.0.3"
