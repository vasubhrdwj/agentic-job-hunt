"""Tests for the hosted web startup wrapper."""

from __future__ import annotations

import sys

import pytest

from scripts import start_web


class WebExec(RuntimeError):
    pass


def test_start_web_migrates_before_replacing_the_process(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: list[tuple[str, object]] = []
    monkeypatch.setenv("MIGRATE_ON_START", "1")
    monkeypatch.setenv("PORT", "8123")
    monkeypatch.setattr(
        start_web.subprocess,
        "run",
        lambda args, *, check: calls.append(("migrate", (args, check))),
    )

    def fake_execv(executable: str, args: object) -> None:
        calls.append(("exec", (executable, args)))
        raise WebExec

    monkeypatch.setattr(start_web.os, "execv", fake_execv)

    with pytest.raises(WebExec):
        start_web.main()

    assert calls[0] == (
        "migrate",
        ([sys.executable, "-m", "alembic", "upgrade", "head"], True),
    )
    assert calls[1] == (
        "exec",
        (
            sys.executable,
            (
                sys.executable,
                "-m",
                "uvicorn",
                "job_hunt_agent.api:app",
                "--host",
                "0.0.0.0",
                "--port",
                "8123",
            ),
        ),
    )


def test_start_web_skips_migration_unless_explicitly_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("MIGRATE_ON_START", raising=False)
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.setattr(
        start_web.subprocess,
        "run",
        lambda *_args, **_kwargs: pytest.fail("migration was not requested"),
    )
    monkeypatch.setattr(
        start_web.os,
        "execv",
        lambda *_args: (_ for _ in ()).throw(WebExec()),
    )

    with pytest.raises(WebExec):
        start_web.main()


@pytest.mark.parametrize("port", ["0", "65536", "not-a-port"])
def test_start_web_rejects_invalid_ports(
    monkeypatch: pytest.MonkeyPatch,
    port: str,
) -> None:
    monkeypatch.setenv("PORT", port)

    with pytest.raises(RuntimeError, match="PORT must"):
        start_web._uvicorn_args()
