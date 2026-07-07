"""Delete expired private requests and run data."""

from __future__ import annotations

from . import persistence


def main() -> None:
    persistence.init_db()
    cleared, deleted = persistence.purge_expired_data()
    print(f"cleared_request_payloads={cleared} deleted_runs={deleted}")


if __name__ == "__main__":
    main()
