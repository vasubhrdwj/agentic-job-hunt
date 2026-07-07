"""Tests for private request encryption and access capabilities."""

from __future__ import annotations

from cryptography.fernet import Fernet
import pytest

from job_hunt_agent.security import (
    DATA_KEYS_ENV,
    DataKeyring,
    DecryptionError,
    EncryptedEnvelope,
    SecurityConfigError,
    generate_access_token,
    hash_access_token,
    load_data_keyring,
)


def _key() -> str:
    return Fernet.generate_key().decode("ascii")


def test_encrypted_request_round_trip_hides_plaintext() -> None:
    marker = "PRIVATE-RESUME-MARKER-7d9f"
    keyring = DataKeyring([("v1", _key())])

    envelope = keyring.encrypt(marker)

    assert envelope.key_id == "v1"
    assert marker not in envelope.ciphertext
    assert keyring.decrypt(envelope) == marker


def test_key_rotation_reads_old_data_and_uses_new_key_for_writes() -> None:
    old_key = _key()
    new_key = _key()
    old_keyring = DataKeyring([("v1", old_key)])
    old_envelope = old_keyring.encrypt("resume")

    rotated = DataKeyring([("v2", new_key), ("v1", old_key)])

    assert rotated.decrypt(old_envelope) == "resume"
    assert rotated.encrypt("new resume").key_id == "v2"


def test_decryption_fails_closed_for_unknown_or_wrong_key() -> None:
    keyring = DataKeyring([("v1", _key())])
    envelope = keyring.encrypt("resume")

    with pytest.raises(DecryptionError, match="unknown"):
        keyring.decrypt(EncryptedEnvelope(key_id="missing", ciphertext=envelope.ciphertext))

    wrong = DataKeyring([("v1", _key())])
    with pytest.raises(DecryptionError, match="failed authentication"):
        wrong.decrypt(envelope)


def test_production_requires_explicit_data_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATA_KEYS_ENV, raising=False)

    with pytest.raises(SecurityConfigError, match="required in production"):
        load_data_keyring(production=True)


def test_local_development_has_encrypted_non_plaintext_fallback(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv(DATA_KEYS_ENV, raising=False)
    keyring = load_data_keyring(production=False)

    envelope = keyring.encrypt("local resume")

    assert "local resume" not in envelope.ciphertext
    assert keyring.decrypt(envelope) == "local resume"


def test_malformed_key_config_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(DATA_KEYS_ENV, "missing-separator")

    with pytest.raises(SecurityConfigError, match="key-id:fernet-key"):
        load_data_keyring(production=False)


def test_access_tokens_are_random_and_only_hashes_are_stable() -> None:
    first = generate_access_token()
    second = generate_access_token()

    assert first != second
    assert len(first) >= 40
    assert hash_access_token(first) == hash_access_token(first)
    assert hash_access_token(first) != first
    assert hash_access_token(first) != hash_access_token(second)
