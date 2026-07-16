"""Run provider-free liveness, readiness, and legacy-policy deployment checks."""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Mapping, Sequence
from urllib.error import HTTPError, URLError
from urllib.parse import urljoin, urlparse
from urllib.request import Request, urlopen


class SmokeError(RuntimeError):
    """A deployment endpoint failed a safe operational assertion."""


@dataclass(frozen=True)
class HttpResult:
    status: int
    headers: Mapping[str, str]
    body: bytes

    def json_object(self) -> dict[str, Any]:
        try:
            value = json.loads(self.body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise SmokeError("endpoint did not return valid UTF-8 JSON") from exc
        if not isinstance(value, dict):
            raise SmokeError("endpoint JSON must be an object")
        return value


def deployment_smoke(
    base_url: str,
    *,
    timeout_seconds: float = 10,
    expect_legacy_mode: str | None = None,
    previous_snapshot: Mapping[str, Any] | None = None,
) -> dict[str, Any]:
    """Verify an already-running deployment without starting provider work."""

    parsed_base = urlparse(base_url)
    if parsed_base.scheme not in {"http", "https"} or not parsed_base.netloc:
        raise SmokeError("base URL must be an absolute HTTP(S) URL")
    if timeout_seconds <= 0:
        raise SmokeError("timeout must be greater than zero")
    normalized_base = base_url.rstrip("/") + "/"
    health = _request(normalized_base, "/health", timeout_seconds=timeout_seconds)
    if health.status != 200 or health.json_object().get("ok") is not True:
        raise SmokeError("liveness check failed")

    readiness = _request(normalized_base, "/ready", timeout_seconds=timeout_seconds)
    snapshot = readiness.json_object()
    if readiness.status != 200 or snapshot.get("ok") is not True:
        raise SmokeError("readiness check failed")
    if "no-store" not in readiness.headers.get("cache-control", "").lower():
        raise SmokeError("readiness response is cacheable")
    _validate_readiness(snapshot)

    if previous_snapshot is not None:
        _compare_restart_snapshot(previous_snapshot, snapshot)

    legacy: dict[str, Any] | None = None
    if expect_legacy_mode is not None:
        legacy = _probe_legacy_policy(
            normalized_base,
            expect_legacy_mode,
            timeout_seconds=timeout_seconds,
        )

    return {
        "ok": True,
        "base_url": normalized_base.rstrip("/"),
        "health": {"status": health.status},
        "readiness": snapshot,
        "legacy_policy": legacy,
    }


def _validate_readiness(snapshot: Mapping[str, Any]) -> None:
    database = snapshot.get("database")
    migrations = snapshot.get("migrations")
    worker = snapshot.get("worker")
    if not isinstance(database, dict) or database.get("reachable") is not True:
        raise SmokeError("readiness database is not reachable")
    if not isinstance(migrations, dict) or migrations.get("current") is not True:
        raise SmokeError("readiness migrations are not current")
    if not migrations.get("revision") or (
        migrations.get("revision") != migrations.get("expected_revision")
    ):
        raise SmokeError("readiness migration revision is inconsistent")
    if not isinstance(worker, dict) or worker.get("fresh") is not True:
        raise SmokeError("readiness worker heartbeat is stale")
    unsupported = worker.get("unsupported_active_kinds")
    if not isinstance(unsupported, list) or unsupported:
        raise SmokeError("readiness has active work without a capable worker")


def _compare_restart_snapshot(
    previous: Mapping[str, Any],
    current: Mapping[str, Any],
) -> None:
    """Ensure a restart did not change the migration identity."""

    previous_migrations = previous.get("migrations")
    current_migrations = current.get("migrations")
    if not isinstance(previous_migrations, dict) or not isinstance(
        current_migrations, dict
    ):
        raise SmokeError("restart snapshot is missing migration metadata")
    for field in ("revision", "expected_revision"):
        if previous_migrations.get(field) != current_migrations.get(field):
            raise SmokeError(f"restart changed migration {field}")


def _probe_legacy_policy(
    base_url: str,
    expected_mode: str,
    *,
    timeout_seconds: float,
) -> dict[str, Any]:
    if expected_mode not in {"enabled", "read_only", "disabled"}:
        raise SmokeError("expected legacy mode must be enabled, read_only, or disabled")
    if expected_mode == "read_only":
        # The policy middleware rejects this before body validation or provider work.
        result = _request(
            base_url,
            "/api/hunt",
            method="POST",
            body=b"{}",
            timeout_seconds=timeout_seconds,
        )
        expected_code = "legacy_read_only"
    else:
        result = _request(
            base_url,
            "/api/runs/deployment-smoke-missing",
            timeout_seconds=timeout_seconds,
        )
        expected_code = "legacy_disabled" if expected_mode == "disabled" else None

    mode = result.headers.get("x-legacy-hunt-mode")
    if mode != expected_mode:
        raise SmokeError("legacy response mode header does not match deployment policy")
    for header in ("deprecation", "sunset", "link", "x-request-id"):
        if not result.headers.get(header):
            raise SmokeError(f"legacy response is missing {header}")

    if expected_code is None:
        if result.status == 410:
            raise SmokeError("legacy API is blocked while enabled mode was expected")
    else:
        if result.status != 410:
            raise SmokeError("legacy write/read was not blocked with HTTP 410")
        problem = result.json_object()
        if problem.get("code") != expected_code or problem.get("retryable") is not False:
            raise SmokeError("legacy policy returned an unstable problem contract")
        if not result.headers.get("content-type", "").startswith(
            "application/problem+json"
        ):
            raise SmokeError("legacy rejection is not application/problem+json")
        if "no-store" not in result.headers.get("cache-control", "").lower():
            raise SmokeError("legacy rejection is cacheable")

    return {"mode": mode, "status": result.status}


def _request(
    base_url: str,
    path: str,
    *,
    method: str = "GET",
    body: bytes | None = None,
    timeout_seconds: float,
) -> HttpResult:
    request = Request(
        urljoin(base_url, path.lstrip("/")),
        method=method,
        data=body,
        headers={
            "Accept": "application/json",
            "Content-Type": "application/json",
            "User-Agent": "job-hunt-deployment-smoke/1",
        },
    )
    try:
        with urlopen(request, timeout=timeout_seconds) as response:  # noqa: S310
            return HttpResult(
                status=response.status,
                headers={key.lower(): value for key, value in response.headers.items()},
                body=response.read(),
            )
    except HTTPError as exc:
        return HttpResult(
            status=exc.code,
            headers={key.lower(): value for key, value in exc.headers.items()},
            body=exc.read(),
        )
    except (URLError, TimeoutError) as exc:
        raise SmokeError(f"cannot reach deployment endpoint: {exc}") from exc


def _read_snapshot(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SmokeError("restart snapshot is missing or invalid") from exc
    if not isinstance(value, dict):
        raise SmokeError("restart snapshot must be a JSON object")
    return value


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base-url", required=True)
    parser.add_argument("--timeout-seconds", type=float, default=10)
    parser.add_argument(
        "--expect-legacy-mode",
        choices=("enabled", "read_only", "disabled"),
    )
    parser.add_argument("--snapshot-out", type=Path)
    parser.add_argument("--compare-snapshot", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        previous = (
            _read_snapshot(args.compare_snapshot) if args.compare_snapshot else None
        )
        report = deployment_smoke(
            args.base_url,
            timeout_seconds=args.timeout_seconds,
            expect_legacy_mode=args.expect_legacy_mode,
            previous_snapshot=previous,
        )
        if args.snapshot_out:
            args.snapshot_out.write_text(
                json.dumps(report["readiness"], indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )
    except (OSError, SmokeError) as exc:
        print(f"deployment smoke failed: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(report, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
