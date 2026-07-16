"""Generate the one-time private workspace access key and stored hash."""

from __future__ import annotations

import hashlib
import secrets

from cryptography.fernet import Fernet


def main() -> None:
    token = secrets.token_urlsafe(32)
    digest = hashlib.sha256(token.encode("utf-8")).hexdigest()
    data_key = Fernet.generate_key().decode("ascii")
    print(
        "Private workspace access key "
        "(save this in your password manager; do not put it in .env):"
    )
    print(token)
    print("\nBackend .env values (keep this file private and never commit it):")
    print(f"JOB_HUNT_OWNER_TOKEN_HASH={digest}")
    print(f"JOB_HUNT_DATA_KEYS=v1:{data_key}")


if __name__ == "__main__":
    main()
