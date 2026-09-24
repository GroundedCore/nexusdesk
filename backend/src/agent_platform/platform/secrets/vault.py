"""Authenticated encryption with an installation key, never a database-stored master key."""

import json
import os
import tempfile
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from agent_platform.platform.persistence.store import DomainError


class CredentialVault:
    def __init__(self, path: Path):
        self.path = path

    def cipher(self, create=False):
        try:
            if create and not self.path.exists():
                self.path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
                # Publish a fully written key atomically, without replacing a concurrent key.
                fd, temporary = tempfile.mkstemp(dir=self.path.parent, prefix=".key-")
                try:
                    with os.fdopen(fd, "wb") as stream:
                        stream.write(Fernet.generate_key())
                        stream.flush()
                        os.fsync(stream.fileno())
                    try:
                        os.link(temporary, self.path)
                    except FileExistsError:
                        pass
                finally:
                    os.unlink(temporary)
            return Fernet(self.path.read_bytes())
        except (OSError, ValueError):
            raise DomainError("model_credential_key_unavailable", 503) from None

    def encrypt(self, secret, tenant, identifier, spec, create=False):
        value = {
            "secret": secret,
            "tenant": tenant,
            "connection": str(identifier),
            "address": spec["base_url"],
            "protocol": spec["protocol"],
        }
        return self.cipher(create).encrypt(json.dumps(value).encode()).decode()

    def decrypt(self, encrypted, tenant, identifier, spec):
        try:
            value = json.loads(self.cipher().decrypt(encrypted.encode()))
        except (InvalidToken, ValueError, KeyError):
            raise DomainError("model_credential_decryption_failed", 503) from None
        if (
            value.get("tenant"),
            value.get("connection"),
            value.get("address"),
            value.get("protocol"),
        ) != (tenant, str(identifier), spec["base_url"], spec["protocol"]):
            raise DomainError("model_credential_binding_mismatch", 409)
        return value["secret"]
