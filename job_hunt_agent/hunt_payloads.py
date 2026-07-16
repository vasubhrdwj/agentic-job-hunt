"""Versioned, row-bound encryption helpers for legacy hunt private data."""

from __future__ import annotations

import hashlib
import hmac
import json
from typing import Any

from .private_payloads import (
    PrivatePayloadBindingError,
    decode_private_payload,
    encrypt_private_payload,
)
from .security import DataKeyring, DecryptionError, EncryptedEnvelope


HUNT_REQUEST_RECORD_KIND = "legacy_hunt_request"
HUNT_RESULT_RECORD_KIND = "legacy_hunt_result"
HUNT_OUTCOME_RECORD_KIND = "legacy_hunt_outcome"
_BOUND_ENVELOPE_FIELDS = frozenset(
    {"schema_version", "record_kind", "owner_id", "record_id", "payload"}
)


def encrypt_hunt_request(
    keyring: DataKeyring,
    *,
    owner_id: str,
    hunt_run_id: str,
    request_json: str,
    request_hash: str,
) -> EncryptedEnvelope:
    """Encrypt the exact request bytes and bind them to owner, run, and digest."""

    _validated_request_json(request_json, request_hash=request_hash)
    return encrypt_private_payload(
        keyring,
        record_kind=HUNT_REQUEST_RECORD_KIND,
        owner_id=owner_id,
        record_id=hunt_run_id,
        payload={
            "request_json": request_json,
            "request_hash": request_hash,
        },
    )


def decrypt_hunt_request(
    keyring: DataKeyring,
    *,
    owner_id: str,
    hunt_run_id: str,
    request_hash: str,
    encryption_key_id: str,
    ciphertext: str,
) -> str:
    """Decrypt a bound request, with digest-checked legacy-row compatibility."""

    payload, plaintext, is_bound = _decrypt_json_record(
        keyring,
        record_kind=HUNT_REQUEST_RECORD_KIND,
        owner_id=owner_id,
        record_id=hunt_run_id,
        encryption_key_id=encryption_key_id,
        ciphertext=ciphertext,
    )
    if is_bound:
        request_json = payload.get("request_json")
        embedded_hash = payload.get("request_hash")
        if not isinstance(request_json, str) or not isinstance(embedded_hash, str):
            raise DecryptionError("encrypted hunt request envelope is invalid")
        if not hmac.compare_digest(embedded_hash, request_hash):
            raise DecryptionError("encrypted hunt request binding failed")
    else:
        request_json = plaintext
    _validated_request_json(request_json, request_hash=request_hash)
    return request_json


def encrypt_hunt_result(
    keyring: DataKeyring,
    *,
    owner_id: str,
    hunt_run_id: str,
    payload: dict[str, Any],
) -> EncryptedEnvelope:
    _validated_result_payload(payload, hunt_run_id=hunt_run_id)
    return encrypt_private_payload(
        keyring,
        record_kind=HUNT_RESULT_RECORD_KIND,
        owner_id=owner_id,
        record_id=hunt_run_id,
        payload=payload,
    )


def decrypt_hunt_result(
    keyring: DataKeyring,
    *,
    owner_id: str,
    hunt_run_id: str,
    encryption_key_id: str,
    ciphertext: str,
) -> dict[str, Any]:
    """Decrypt a result and require its payload run id to match the row."""

    payload, _plaintext, _is_bound = _decrypt_json_record(
        keyring,
        record_kind=HUNT_RESULT_RECORD_KIND,
        owner_id=owner_id,
        record_id=hunt_run_id,
        encryption_key_id=encryption_key_id,
        ciphertext=ciphertext,
    )
    _validated_result_payload(payload, hunt_run_id=hunt_run_id)
    return payload


def encrypt_hunt_outcome(
    keyring: DataKeyring,
    *,
    owner_id: str,
    outcome_id: str,
    draft_id: str,
    payload: dict[str, Any],
) -> EncryptedEnvelope:
    _validated_outcome_payload(payload, draft_id=draft_id)
    return encrypt_private_payload(
        keyring,
        record_kind=HUNT_OUTCOME_RECORD_KIND,
        owner_id=owner_id,
        record_id=outcome_id,
        payload=payload,
    )


def decrypt_hunt_outcome(
    keyring: DataKeyring,
    *,
    owner_id: str,
    outcome_id: str,
    draft_id: str,
    encryption_key_id: str,
    ciphertext: str,
) -> dict[str, Any]:
    """Decrypt an outcome and bind it to its owner, row id, and draft id."""

    payload, _plaintext, is_bound = _decrypt_json_record(
        keyring,
        record_kind=HUNT_OUTCOME_RECORD_KIND,
        owner_id=owner_id,
        record_id=outcome_id,
        encryption_key_id=encryption_key_id,
        ciphertext=ciphertext,
    )
    if not is_bound:
        # Old outcome JSON contains only a commonly reused draft_id, so neither
        # its owner nor its run can be established after a ciphertext swap.
        raise DecryptionError("unbound legacy hunt outcome is not trusted")
    _validated_outcome_payload(payload, draft_id=draft_id)
    return payload


def _decrypt_json_record(
    keyring: DataKeyring,
    *,
    record_kind: str,
    owner_id: str,
    record_id: str,
    encryption_key_id: str,
    ciphertext: str,
) -> tuple[dict[str, Any], str, bool]:
    try:
        plaintext = keyring.decrypt(
            EncryptedEnvelope(
                key_id=encryption_key_id,
                ciphertext=ciphertext,
            )
        )
        parsed = json.loads(plaintext)
    except (DecryptionError, UnicodeError, TypeError, ValueError) as exc:
        raise DecryptionError("encrypted hunt payload is invalid") from exc
    if not isinstance(parsed, dict):
        raise DecryptionError("encrypted hunt payload is invalid")

    # Fernet prevents an attacker from partially editing this discriminator.
    # Therefore all envelope fields means a new bound row; otherwise this is a
    # legitimate pre-envelope row and receives the contextual checks below.
    if _BOUND_ENVELOPE_FIELDS.issubset(parsed):
        try:
            payload = decode_private_payload(
                plaintext,
                record_kind=record_kind,
                owner_id=owner_id,
                record_id=record_id,
            )
        except PrivatePayloadBindingError as exc:
            raise DecryptionError("encrypted hunt payload binding failed") from exc
        return payload, plaintext, True
    return parsed, plaintext, False


def _validated_request_json(request_json: str, *, request_hash: str) -> dict[str, Any]:
    try:
        payload = json.loads(request_json)
    except (TypeError, ValueError) as exc:
        raise DecryptionError("encrypted hunt request is invalid") from exc
    if not isinstance(payload, dict):
        raise DecryptionError("encrypted hunt request is invalid")
    calculated = hashlib.sha256(request_json.encode("utf-8")).hexdigest()
    if not hmac.compare_digest(calculated, request_hash):
        raise DecryptionError("encrypted hunt request digest failed")
    return payload


def _validated_result_payload(
    payload: dict[str, Any],
    *,
    hunt_run_id: str,
) -> None:
    run_id = payload.get("run_id")
    if not isinstance(run_id, str) or not hmac.compare_digest(run_id, hunt_run_id):
        raise DecryptionError("encrypted hunt result binding failed")


def _validated_outcome_payload(
    payload: dict[str, Any],
    *,
    draft_id: str,
) -> None:
    payload_draft_id = payload.get("draft_id")
    if not isinstance(payload_draft_id, str) or not hmac.compare_digest(
        payload_draft_id,
        draft_id,
    ):
        raise DecryptionError("encrypted hunt outcome binding failed")


__all__ = [
    "HUNT_OUTCOME_RECORD_KIND",
    "HUNT_REQUEST_RECORD_KIND",
    "HUNT_RESULT_RECORD_KIND",
    "decrypt_hunt_outcome",
    "decrypt_hunt_request",
    "decrypt_hunt_result",
    "encrypt_hunt_outcome",
    "encrypt_hunt_request",
    "encrypt_hunt_result",
]
