"""Small, provider-specific crypto helpers used by channel adapters."""

import base64
import hashlib
import hmac
import os
import struct

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes

from agent_platform.platform.persistence.store import DomainError


def pkcs7_pad(value: bytes, block_size: int = 32) -> bytes:
    size = block_size - len(value) % block_size
    return value + bytes([size]) * size


def pkcs7_unpad(value: bytes) -> bytes:
    if not value:
        raise ValueError("empty padded value")
    size = value[-1]
    if size < 1 or size > 32 or value[-size:] != bytes([size]) * size:
        raise ValueError("invalid padding")
    return value[:-size]


def aes_cbc_decrypt(key: bytes, value: bytes) -> bytes:
    if len(value) == 0 or len(value) % 16:
        raise ValueError("invalid ciphertext length")
    decryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).decryptor()
    return decryptor.update(value) + decryptor.finalize()


def aes_cbc_encrypt(key: bytes, value: bytes) -> bytes:
    encryptor = Cipher(algorithms.AES(key), modes.CBC(key[:16])).encryptor()
    return encryptor.update(value) + encryptor.finalize()


def wechat_signature(token: str, timestamp: str, nonce: str, encrypted: str) -> str:
    values = sorted((token, timestamp, nonce, encrypted))
    return hashlib.sha1("".join(values).encode()).hexdigest()


def decode_wechat_key(encoding_aes_key: str) -> bytes:
    try:
        key = base64.b64decode(encoding_aes_key + "=")
    except (ValueError, TypeError):
        raise DomainError("channel_invalid_encoding_aes_key", 422) from None
    if len(key) != 32:
        raise DomainError("channel_invalid_encoding_aes_key", 422)
    return key


def decrypt_wechat_payload(
    encoding_aes_key: str,
    encrypted: str,
    *,
    expected_receive_id: str | None = None,
) -> str:
    key = decode_wechat_key(encoding_aes_key)
    try:
        raw = base64.b64decode(encrypted)
        plain = pkcs7_unpad(aes_cbc_decrypt(key, raw))
        size = struct.unpack("!I", plain[16:20])[0]
        content = plain[20 : 20 + size]
        receive_id = plain[20 + size :].decode()
    except (ValueError, UnicodeDecodeError, TypeError):
        raise DomainError("channel_invalid_encrypted_payload", 401) from None
    if expected_receive_id and receive_id != expected_receive_id:
        raise DomainError("channel_encrypted_receiver_mismatch", 401)
    return content.decode()


def encrypt_wechat_payload(
    encoding_aes_key: str,
    content: str,
    receive_id: str,
    *,
    random_bytes: bytes | None = None,
) -> str:
    key = decode_wechat_key(encoding_aes_key)
    payload = (
        (random_bytes or os.urandom(16))
        + struct.pack("!I", len(content.encode()))
        + content.encode()
        + receive_id.encode()
    )
    return base64.b64encode(aes_cbc_encrypt(key, pkcs7_pad(payload))).decode()


def decrypt_feishu_payload(encrypt_key: str, encrypted: str) -> bytes:
    try:
        raw = base64.b64decode(encrypted)
        key = hashlib.sha256(encrypt_key.encode()).digest()
        plain = pkcs7_unpad(aes_cbc_decrypt(key, raw[16:]))
    except (ValueError, TypeError):
        raise DomainError("channel_invalid_encrypted_payload", 401) from None
    return plain


def feishu_signature(timestamp: str, nonce: str, encrypt_key: str, body: bytes) -> str:
    value = timestamp.encode() + nonce.encode() + encrypt_key.encode() + body
    return hashlib.sha256(value).hexdigest()


def dingtalk_robot_signature(secret: str, timestamp: str) -> str:
    value = (timestamp + "\n" + secret).encode()
    return base64.b64encode(hmac.new(secret.encode(), value, hashlib.sha256).digest()).decode()


def slack_signature(signing_secret: str, timestamp: str, body: bytes) -> str:
    base = b"v0:" + timestamp.encode() + b":" + body
    return "v0=" + hmac.new(signing_secret.encode(), base, hashlib.sha256).hexdigest()


def verify_ed25519(public_key_hex: str, signature_hex: str, message: bytes) -> bool:
    from cryptography.exceptions import InvalidSignature
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

    try:
        key = Ed25519PublicKey.from_public_bytes(bytes.fromhex(public_key_hex.strip()))
        key.verify(bytes.fromhex(signature_hex.strip()), message)
    except (InvalidSignature, ValueError):
        return False
    return True
