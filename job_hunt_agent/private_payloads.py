"""Bound encrypted envelopes for private owner-scoped product records."""

from __future__ import annotations

import json
from typing import Any

from .security import DataKeyring, EncryptedEnvelope


PRIVATE_ENVELOPE_SCHEMA_VERSION = 1


class PrivatePayloadBindingError(ValueError):
    """Ciphertext authenticated but was not created for the expected record."""


def encrypt_private_payload(
    keyring: DataKeyring,
    *,
    record_kind: str,
    owner_id: str,
    record_id: str,
    payload: dict[str, Any],
) -> EncryptedEnvelope:
    plaintext = json.dumps(
        {
            "schema_version": PRIVATE_ENVELOPE_SCHEMA_VERSION,
            "record_kind": record_kind,
            "owner_id": owner_id,
            "record_id": record_id,
            "payload": payload,
        },
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
    )
    return keyring.encrypt(plaintext)


def decrypt_private_payload(
    keyring: DataKeyring,
    *,
    record_kind: str,
    owner_id: str,
    record_id: str,
    encryption_key_id: str,
    ciphertext: str,
) -> dict[str, Any]:
    plaintext = keyring.decrypt(
        EncryptedEnvelope(key_id=encryption_key_id, ciphertext=ciphertext)
    )
    return decode_private_payload(
        plaintext,
        record_kind=record_kind,
        owner_id=owner_id,
        record_id=record_id,
    )


def decode_private_payload(
    plaintext: str,
    *,
    record_kind: str,
    owner_id: str,
    record_id: str,
) -> dict[str, Any]:
    """Validate an already-decrypted private envelope against its database row."""

    try:
        envelope = json.loads(plaintext)
    except (TypeError, ValueError) as exc:
        raise PrivatePayloadBindingError("private payload envelope is invalid") from exc
    if not isinstance(envelope, dict):
        raise PrivatePayloadBindingError("private payload envelope is invalid")
    if (
        envelope.get("schema_version") != PRIVATE_ENVELOPE_SCHEMA_VERSION
        or envelope.get("record_kind") != record_kind
        or envelope.get("owner_id") != owner_id
        or envelope.get("record_id") != record_id
        or not isinstance(envelope.get("payload"), dict)
    ):
        raise PrivatePayloadBindingError("private payload binding does not match record")
    return envelope["payload"]


__all__ = [
    "PRIVATE_ENVELOPE_SCHEMA_VERSION",
    "PrivatePayloadBindingError",
    "decode_private_payload",
    "decrypt_private_payload",
    "encrypt_private_payload",
]
