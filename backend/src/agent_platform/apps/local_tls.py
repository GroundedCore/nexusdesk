"""Local TLS material for the quickstart bundle.

Quickstart runs as two containers on a machine with no domain, no ACME account and
no place to keep a private key outside the data volume, so a publicly trusted
certificate is not available. We mint a private CA and a leaf signed by it, keep
them under /data/tls and reuse them across restarts.

Trusting ca.crt on the client is what matters: it clears the browser warning, and
an https origin is a secure context, so the secure-only APIs the console uses
(crypto.randomUUID, navigator.clipboard) behave normally. Even without the import
the origin is still a secure context once the interstitial is bypassed, but every
new device warns again.

The leaf is re-issued when it is close to expiring, or when the configured host
list changes. Both cases keep the same CA, so an already imported ca.crt stays
valid and clients never see a new warning. The CA itself is renewed only when it
is missing or expired, which does require importing the new file.

Only the quickstart deployment uses this. Production is expected to terminate TLS
at the host or an existing ingress proxy, as deploy/README.md describes.
"""

import ipaddress
from datetime import UTC, datetime, timedelta
from pathlib import Path

from cryptography import x509
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID

CA_DAYS = 7300
LEAF_DAYS = 825
RENEW_BEFORE = timedelta(days=30)
DEFAULT_HOSTS = ("localhost", "127.0.0.1", "::1")

CA_ORGANISATION = "NexusDesk Quickstart"
CA_COMMON_NAME = "NexusDesk Quickstart Local CA"


def _write(path: Path, data: bytes, mode: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    path.chmod(mode)


def _read_certificate(path: Path) -> x509.Certificate | None:
    if not path.exists():
        return None
    try:
        return x509.load_pem_x509_certificate(path.read_bytes())
    except ValueError:
        # A truncated file from an interrupted start is regenerated, not fatal.
        return None


def _private_key_bytes(key) -> bytes:
    return key.private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )


def _new_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _expiry(certificate: x509.Certificate) -> datetime:
    return certificate.not_valid_after_utc


def _usable(certificate: x509.Certificate | None, renew_before: timedelta) -> bool:
    return certificate is not None and _expiry(certificate) - datetime.now(UTC) > renew_before


def _alternative_names(hosts):
    names = []
    for host in hosts:
        try:
            names.append(x509.IPAddress(ipaddress.ip_address(host)))
        except ValueError:
            names.append(x509.DNSName(host))
    return x509.SubjectAlternativeName(names)


def ensure_ca(directory: Path) -> tuple[x509.Certificate, object]:
    """Return the local CA certificate and key, creating them only when needed.

    Replacing the CA invalidates every previously imported ca.crt, so the existing
    pair is reused for as long as it can still sign a leaf.
    """
    key_path, cert_path = directory / "ca.key", directory / "ca.crt"
    certificate = _read_certificate(cert_path)
    # Renewing the CA requires clients to import the new file, so allow no grace
    # period: reuse the current CA until it actually expires.
    if _usable(certificate, timedelta(0)) and key_path.exists():
        try:
            key = serialization.load_pem_private_key(key_path.read_bytes(), password=None)
        except (ValueError, TypeError):
            key = None
        if key is not None:
            return certificate, key

    key = _new_key()
    now = datetime.now(UTC)
    subject = x509.Name(
        [
            x509.NameAttribute(NameOID.ORGANIZATION_NAME, CA_ORGANISATION),
            x509.NameAttribute(NameOID.COMMON_NAME, CA_COMMON_NAME),
        ]
    )
    certificate = (
        x509.CertificateBuilder()
        .subject_name(subject)
        .issuer_name(subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=CA_DAYS))
        .add_extension(x509.BasicConstraints(ca=True, path_length=0), critical=True)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=False,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=True,
                crl_sign=True,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        # A self-signed root should carry an AKI matching its own SKI; validators that
        # look for it otherwise reject the whole chain.
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(key.public_key()),
            critical=False,
        )
        .sign(key, hashes.SHA256())
    )
    _write(key_path, _private_key_bytes(key), 0o600)
    _write(cert_path, certificate.public_bytes(serialization.Encoding.PEM), 0o644)
    return certificate, key


def ensure_certificate(directory: Path, hosts=None) -> tuple[Path, Path, Path]:
    """Return (server certificate, server key, CA certificate) paths for ``hosts``.

    Re-issues the leaf when it is missing, close to expiring, or was not issued for
    the current host list. The CA is left alone in all three cases.
    """
    wanted = tuple(dict.fromkeys(hosts or DEFAULT_HOSTS))
    directory.mkdir(parents=True, exist_ok=True)

    ca_certificate, ca_key = ensure_ca(directory)
    cert_path, key_path = directory / "server.crt", directory / "server.key"
    fingerprint = directory / "hosts.txt"

    certificate = _read_certificate(cert_path)
    recorded = (
        set(fingerprint.read_text(encoding="utf-8").split()) if fingerprint.exists() else set()
    )
    if _usable(certificate, RENEW_BEFORE) and key_path.exists() and recorded == set(wanted):
        return cert_path, key_path, directory / "ca.crt"

    key = _new_key()
    now = datetime.now(UTC)
    certificate = (
        x509.CertificateBuilder()
        .subject_name(
            x509.Name(
                [
                    x509.NameAttribute(NameOID.ORGANIZATION_NAME, CA_ORGANISATION),
                    x509.NameAttribute(NameOID.COMMON_NAME, wanted[0]),
                ]
            )
        )
        .issuer_name(ca_certificate.subject)
        .public_key(key.public_key())
        .serial_number(x509.random_serial_number())
        .not_valid_before(now - timedelta(days=1))
        .not_valid_after(now + timedelta(days=LEAF_DAYS))
        .add_extension(_alternative_names(wanted), critical=False)
        .add_extension(x509.BasicConstraints(ca=False, path_length=None), critical=True)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.SERVER_AUTH]), critical=False)
        .add_extension(
            x509.KeyUsage(
                digital_signature=True,
                content_commitment=False,
                key_encipherment=True,
                data_encipherment=False,
                key_agreement=False,
                key_cert_sign=False,
                crl_sign=False,
                encipher_only=False,
                decipher_only=False,
            ),
            critical=True,
        )
        # OpenSSL refuses to build the chain without a matching AKI once the CA
        # publishes an SKI, so a client that trusts only ca.crt rejects the leaf with
        # "Missing Authority Key Identifier". Both key identifiers are mandatory here.
        .add_extension(
            x509.AuthorityKeyIdentifier.from_issuer_public_key(ca_key.public_key()),
            critical=False,
        )
        .add_extension(x509.SubjectKeyIdentifier.from_public_key(key.public_key()), critical=False)
        .sign(ca_key, hashes.SHA256())
    )
    _write(key_path, _private_key_bytes(key), 0o600)
    _write(cert_path, certificate.public_bytes(serialization.Encoding.PEM), 0o644)
    _write(fingerprint, " ".join(wanted).encode("utf-8"), 0o644)
    return cert_path, key_path, directory / "ca.crt"
