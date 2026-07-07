"""Security primitives for private hunt requests and run capabilities."""

from __future__ import annotations

import base64
import hashlib
import os
import re
import secrets
from dataclasses import dataclass

from cryptography.fernet import Fernet, InvalidToken


DATA_KEYS_ENV = "JOB_HUNT_DATA_KEYS"
MAX_RESUME_CHARS = 50_000
REQUEST_RETENTION_HOURS = 24
RUN_RETENTION_DAYS = 30
_KEY_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,31}$")
_DEV_KEY_ID = "local-dev"


class SecurityConfigError(RuntimeError):
    """Raised when private-data configuration is missing or malformed."""


class DecryptionError(ValueError):
    """Raised when an encrypted request cannot be decrypted safely."""


@dataclass(frozen=True)
class EncryptedEnvelope:
    key_id: str
    ciphertext: str


class DataKeyring:
    """Versioned Fernet keys with the first configured key used for writes."""

    def __init__(self, keys: list[tuple[str, str]]) -> None:
        if not keys:
            raise SecurityConfigError("at least one data-encryption key is required")

        parsed: dict[str, Fernet] = {}
        ordered_ids: list[str] = []
        for key_id, encoded_key in keys:
            if not _KEY_ID_RE.fullmatch(key_id):
                raise SecurityConfigError(f"invalid data-encryption key id {key_id!r}")
            if key_id in parsed:
                raise SecurityConfigError(f"duplicate data-encryption key id {key_id!r}")
            try:
                parsed[key_id] = Fernet(encoded_key.encode("ascii"))
            except (ValueError, TypeError) as exc:
                raise SecurityConfigError(
                    f"invalid Fernet key for data-encryption key id {key_id!r}"
                ) from exc
            ordered_ids.append(key_id)

        self._keys = parsed
        self._active_key_id = ordered_ids[0]

    @property
    def active_key_id(self) -> str:
        return self._active_key_id

    def encrypt(self, plaintext: str) -> EncryptedEnvelope:
        ciphertext = self._keys[self._active_key_id].encrypt(
            plaintext.encode("utf-8")
        )
        return EncryptedEnvelope(
            key_id=self._active_key_id,
            ciphertext=ciphertext.decode("ascii"),
        )

    def decrypt(self, envelope: EncryptedEnvelope) -> str:
        fernet = self._keys.get(envelope.key_id)
        if fernet is None:
            raise DecryptionError(
                f"unknown data-encryption key id {envelope.key_id!r}"
            )
        try:
            plaintext = fernet.decrypt(envelope.ciphertext.encode("ascii"))
        except (InvalidToken, ValueError) as exc:
            raise DecryptionError("encrypted request failed authentication") from exc
        return plaintext.decode("utf-8")


def load_data_keyring(*, production: bool) -> DataKeyring:
    raw = os.getenv(DATA_KEYS_ENV, "").strip()
    if not raw:
        if production:
            raise SecurityConfigError(
                f"{DATA_KEYS_ENV} is required in production; plaintext fallback is disabled"
            )
        return DataKeyring([(_DEV_KEY_ID, _development_fernet_key())])

    keys: list[tuple[str, str]] = []
    for entry in raw.split(","):
        normalized = entry.strip()
        if not normalized:
            continue
        key_id, separator, encoded_key = normalized.partition(":")
        if not separator or not encoded_key.strip():
            raise SecurityConfigError(
                f"{DATA_KEYS_ENV} entries must use key-id:fernet-key"
            )
        keys.append((key_id.strip(), encoded_key.strip()))
    return DataKeyring(keys)


def generate_access_token() -> str:
    return secrets.token_urlsafe(32)


def hash_access_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _development_fernet_key() -> str:
    digest = hashlib.sha256(
        b"job-hunt-signal-local-development-key-not-for-production"
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii")
