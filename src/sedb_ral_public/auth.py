"""Small signing-provider boundary. No host, account or credential discovery."""

from __future__ import annotations

import base64
import binascii
import hashlib
from typing import Protocol

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    BestAvailableEncryption,
    Encoding,
    PrivateFormat,
    PublicFormat,
    load_pem_private_key,
)

from .errors import fail

DOMAIN = b"SEDB-RAL-PUBLIC-EVENT-v1\x00"


class SigningProvider(Protocol):
    """Providers sign bytes; they cannot declare their own signatures verified."""

    def public_key_bytes(self) -> bytes: ...
    def sign(self, message: bytes) -> bytes: ...


def key_id(public_key: bytes) -> str:
    if type(public_key) is not bytes or len(public_key) != 32:
        fail("public_key_invalid")
    return "ed25519:" + hashlib.sha256(public_key).hexdigest()


def encode_public_key(public_key: bytes) -> str:
    key_id(public_key)
    return base64.b64encode(public_key).decode("ascii")


def decode_public_key(value: str) -> bytes:
    try:
        if type(value) is not str:
            fail("public_key_invalid")
        raw = base64.b64decode(value, validate=True)
    except (ValueError, binascii.Error):
        fail("public_key_invalid")
    key_id(raw)
    if encode_public_key(raw) != value:
        fail("public_key_invalid")
    return raw


def verify(public_key: bytes, signature: bytes, message: bytes) -> None:
    key_id(public_key)
    if type(signature) is not bytes or len(signature) != 64:
        fail("signature_invalid")
    try:
        Ed25519PublicKey.from_public_bytes(public_key).verify(signature, message)
    except (InvalidSignature, ValueError):
        fail("signature_invalid")


class Ed25519Signer:
    """In-memory signer; file serialization is encrypted and explicit."""

    def __init__(self, private_key: Ed25519PrivateKey):
        if not isinstance(private_key, Ed25519PrivateKey):
            fail("signer_type_invalid")
        self._key = private_key

    @classmethod
    def generate(cls) -> Ed25519Signer:
        return cls(Ed25519PrivateKey.generate())

    def public_key_bytes(self) -> bytes:
        return self._key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)

    def sign(self, message: bytes) -> bytes:
        return self._key.sign(message)

    def encrypted_pem(self, passphrase: bytes) -> bytes:
        if type(passphrase) is not bytes or not passphrase:
            fail("passphrase_required")
        return self._key.private_bytes(
            Encoding.PEM, PrivateFormat.PKCS8, BestAvailableEncryption(passphrase)
        )

    @classmethod
    def from_encrypted_pem(cls, raw: bytes, passphrase: bytes) -> Ed25519Signer:
        if type(passphrase) is not bytes or not passphrase:
            fail("passphrase_required")
        try:
            private_key = load_pem_private_key(raw, password=passphrase)
        except (ValueError, TypeError):
            fail("key_unlock_failed")
        return cls(private_key)
