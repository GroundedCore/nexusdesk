import socket
import ssl
import threading
from datetime import datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives.asymmetric import padding

from agent_platform.apps import local_tls


def load(path: Path) -> x509.Certificate:
    return x509.load_pem_x509_certificate(path.read_bytes())


def alternative_names(path: Path):
    certificate = load(path)
    extension = certificate.extensions.get_extension_for_class(x509.SubjectAlternativeName)
    return {str(value.value) for value in extension.value}


def handshake(certificate: Path, key: Path, authority: Path, hostname="localhost"):
    """Run a real TLS handshake, trusting only ``authority``. Returns an error or None.

    This is the check that matters: a certificate can look fine field by field and
    still be rejected by OpenSSL when the chain cannot be built. A socket pair keeps
    it off the network stack, so no port is bound and nothing is flaky.
    """
    server_context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    server_context.load_cert_chain(certfile=str(certificate), keyfile=str(key))
    client_context = ssl.create_default_context(cafile=str(authority))

    server_socket, client_socket = socket.socketpair()
    # Bounded on both ends so a failed handshake surfaces as an exception instead of
    # hanging the suite.
    server_socket.settimeout(5)
    client_socket.settimeout(5)
    outcome = {}

    def serve():
        try:
            with server_context.wrap_socket(server_socket, server_side=True) as connection:
                connection.recv(1)
                connection.send(b"y")
        except Exception as exc:  # noqa: BLE001 -- reported back to the assertions
            outcome["server"] = exc

    worker = threading.Thread(target=serve, daemon=True)
    worker.start()
    try:
        with client_context.wrap_socket(client_socket, server_hostname=hostname) as connection:
            connection.send(b"x")
            connection.recv(1)
    except Exception as exc:  # noqa: BLE001 -- reported back to the assertions
        outcome["client"] = exc
        client_socket.close()
    worker.join(timeout=5)
    return outcome.get("client") or outcome.get("server")


def test_trusting_the_ca_is_enough_for_a_real_handshake(tmp_path):
    certificate, key, authority = local_tls.ensure_certificate(tmp_path, ["localhost"])
    assert handshake(certificate, key, authority) is None


def test_leaf_carries_the_key_identifiers_validators_require(tmp_path):
    certificate, _, authority = local_tls.ensure_certificate(tmp_path, ["localhost"])
    leaf, ca = load(certificate), load(authority)

    # Without a matching AKI, OpenSSL rejects the chain with "Missing Authority Key
    # Identifier" even though the CA is trusted, and the browser warning never goes
    # away. Accepting only ca.crt must therefore be enough on its own.
    leaf_aki = leaf.extensions.get_extension_for_class(x509.AuthorityKeyIdentifier).value
    ca_ski = ca.extensions.get_extension_for_class(x509.SubjectKeyIdentifier).value
    assert leaf_aki.key_identifier == ca_ski.digest
    assert leaf.extensions.get_extension_for_class(x509.SubjectKeyIdentifier)


def test_an_untrusted_ca_is_still_rejected(tmp_path):
    certificate, key, authority = local_tls.ensure_certificate(tmp_path, ["localhost"])
    other = tmp_path / "other"
    _, _, unrelated = local_tls.ensure_certificate(other, ["localhost"])

    # The point of importing ca.crt is that nothing is trusted by accident.
    assert handshake(certificate, key, unrelated) is not None
    assert handshake(certificate, key, authority) is None


def test_creates_ca_and_leaf_for_the_requested_hosts(tmp_path):
    certificate, _key, authority = local_tls.ensure_certificate(
        tmp_path, ["localhost", "192.168.1.50", "nexusdesk.test"]
    )

    assert {path.name for path in tmp_path.iterdir()} == {
        "ca.crt",
        "ca.key",
        "hosts.txt",
        "server.crt",
        "server.key",
    }
    assert alternative_names(certificate) == {"localhost", "192.168.1.50", "nexusdesk.test"}

    # IP literals must become IPAddress entries, not DNSName, or browsers reject the
    # name match even after the CA is trusted.
    extension = load(certificate).extensions.get_extension_for_class(x509.SubjectAlternativeName)
    kinds = {type(value) for value in extension.value}
    assert x509.IPAddress in kinds and x509.DNSName in kinds

    ca_certificate = load(authority)
    constraints = ca_certificate.extensions.get_extension_for_class(x509.BasicConstraints)
    assert constraints.value.ca is True and constraints.value.path_length == 0
    assert load(certificate).issuer == ca_certificate.subject


def test_leaf_is_signed_by_the_ca(tmp_path):
    certificate, _, authority = local_tls.ensure_certificate(tmp_path, ["localhost"])
    leaf, ca_certificate = load(certificate), load(authority)
    ca_certificate.public_key().verify(
        leaf.signature,
        leaf.tbs_certificate_bytes,
        padding.PKCS1v15(),
        leaf.signature_hash_algorithm,
    )


def test_defaults_to_loopback_names(tmp_path):
    certificate, _, _ = local_tls.ensure_certificate(tmp_path)
    assert alternative_names(certificate) == {"localhost", "127.0.0.1", "::1"}


def test_reuses_material_when_nothing_changed(tmp_path):
    certificate, key, authority = local_tls.ensure_certificate(tmp_path, ["localhost"])
    before = (certificate.read_bytes(), key.read_bytes(), authority.read_bytes())
    local_tls.ensure_certificate(tmp_path, ["localhost"])
    assert (certificate.read_bytes(), key.read_bytes(), authority.read_bytes()) == before


def test_reissues_leaf_on_host_change_and_keeps_the_ca(tmp_path):
    certificate, _, authority = local_tls.ensure_certificate(tmp_path, ["localhost"])
    leaf_before, ca_before = certificate.read_bytes(), authority.read_bytes()

    local_tls.ensure_certificate(tmp_path, ["localhost", "10.0.0.7"])

    # The CA must survive, otherwise every client that imported ca.crt would warn again.
    assert certificate.read_bytes() != leaf_before
    assert authority.read_bytes() == ca_before
    assert alternative_names(certificate) == {"localhost", "10.0.0.7"}


def test_reissues_leaf_that_is_close_to_expiring(tmp_path, monkeypatch):
    certificate, _, authority = local_tls.ensure_certificate(tmp_path, ["localhost"])
    leaf_before, ca_before = certificate.read_bytes(), authority.read_bytes()

    # Pretend the renewal window covers the whole leaf lifetime.
    monkeypatch.setattr(local_tls, "RENEW_BEFORE", timedelta(days=local_tls.LEAF_DAYS + 1))
    local_tls.ensure_certificate(tmp_path, ["localhost"])

    assert certificate.read_bytes() != leaf_before
    assert authority.read_bytes() == ca_before


def test_regenerates_a_corrupt_certificate_instead_of_failing(tmp_path):
    certificate, _, _ = local_tls.ensure_certificate(tmp_path, ["localhost"])
    certificate.write_bytes(b"not a pem file")

    local_tls.ensure_certificate(tmp_path, ["localhost"])

    assert load(certificate).subject is not None
    assert alternative_names(certificate) == {"localhost"}


def test_leaf_outlives_a_year_but_stays_within_browser_limits(tmp_path):
    certificate, _, authority = local_tls.ensure_certificate(tmp_path, ["localhost"])
    leaf, ca_certificate = load(certificate), load(authority)
    now = datetime.now(leaf.not_valid_after_utc.tzinfo)
    assert leaf.not_valid_after_utc - now > timedelta(days=365)
    assert ca_certificate.not_valid_after_utc - now > timedelta(days=365 * 10)
