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
