"""User-facing failure formatting for operational command-line tools."""

from __future__ import annotations

from pathlib import Path

import pytest

from scripts import import_legacy_hunts, migration_gate


def test_migration_gate_formats_data_bearing_downgrade_refusal(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def refuse_downgrade(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError(
            "Cannot downgrade 20260715_0018 without losing privacy data"
        )

    monkeypatch.setattr(migration_gate, "guarded_downgrade", refuse_downgrade)

    result = migration_gate.main(
        [
            "downgrade",
            "--database-url",
            "sqlite+pysqlite:///unused.db",
            "--verified-backup",
            "verified-backup.db",
            "--apply",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert captured.err == (
        "migration gate failed: "
        "Cannot downgrade 20260715_0018 without losing privacy data\n"
    )
    assert "Traceback" not in captured.err


def test_migration_gate_does_not_hide_unexpected_runtime_errors(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def crash(*_args: object, **_kwargs: object) -> object:
        raise RuntimeError("unexpected programming defect")

    monkeypatch.setattr(migration_gate, "guarded_downgrade", crash)

    with pytest.raises(RuntimeError, match="unexpected programming defect"):
        migration_gate.main(
            [
                "downgrade",
                "--database-url",
                "sqlite+pysqlite:///unused.db",
                "--verified-backup",
                "verified-backup.db",
                "--apply",
            ]
        )


def test_legacy_import_formats_invalid_database_url(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    source = tmp_path / "legacy.db"
    source.touch()
    monkeypatch.setenv("ENVIRONMENT", "development")
    monkeypatch.delenv("JOB_HUNT_DATA_KEYS", raising=False)

    result = import_legacy_hunts.main(
        [
            "--source",
            str(source),
            "--owner-id",
            "owner",
            "--database-url",
            "not a database URL",
            "--allow-development-key",
        ]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert "DATABASE_URL is invalid" in captured.err
    assert "Traceback" not in captured.err


def test_migration_check_formats_invalid_database_url(
    capsys: pytest.CaptureFixture[str],
) -> None:
    result = migration_gate.main(
        ["check", "--database-url", "not a database URL"]
    )

    captured = capsys.readouterr()
    assert result == 2
    assert captured.out == ""
    assert "DATABASE_URL is invalid" in captured.err
    assert "Traceback" not in captured.err
