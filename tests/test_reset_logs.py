"""The standalone reset_logs script clears history through the project's own engine."""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

from shared.constants import AuditAction, DriftStatus
from web.db.repository import Store
from web.db.session import Database

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "reset_logs.py"


def seeded_database(path: Path) -> tuple[Database, Store]:
    database = Database(f"sqlite:///{path.as_posix()}")
    database.init()
    store = Store(database, "test-host")
    store.record_audit(AuditAction.ENGINE_STARTED, "host")
    store.record_audit(AuditAction.ROGUE_DETECTED, "rogue-db")
    store.record_drift_event(
        ref="rogue-db",
        drift_status=DriftStatus.ROGUE,
        previous_status=None,
        service_name=None,
        container_name="rogue-db",
        details="x",
    )
    store.add_whitelist  # noqa: B018 - whitelist and buffer tables must survive untouched
    return database, store


def test_store_clear_logs_truncates_only_history_tables(tmp_path: Path) -> None:
    database, store = seeded_database(tmp_path / "w.db")
    from shared.constants import WhitelistTargetType

    store.add_whitelist(WhitelistTargetType.CONTAINER, "keep-me", "reason")
    try:
        assert store.clear_logs() == (1, 2)
        assert store.counts() == {"audit": 0, "events": 0, "pending": 0, "whitelist": 1}
        assert store.clear_logs() == (0, 0)
    finally:
        database.close()


def test_script_main_prints_confirmation(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    db_path = tmp_path / "w.db"
    database, _ = seeded_database(db_path)
    database.close()

    spec = importlib.util.spec_from_file_location("reset_logs", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    assert module.main([f"sqlite:///{db_path.as_posix()}"]) == 0
    out = capsys.readouterr().out
    assert out.splitlines()[0] == "[✓] Drift and audit log history cleared successfully."
    assert "1 drift events and 2 audit rows removed" in out

    verify = Database(f"sqlite:///{db_path.as_posix()}")
    try:
        assert Store(verify, "h").counts()["audit"] == 0
    finally:
        verify.close()


def test_script_resolves_database_url_from_environment(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db_path = tmp_path / "env.db"
    database, _ = seeded_database(db_path)
    database.close()
    env = {"DATABASE_URL": f"sqlite:///{db_path.as_posix()}", "PYTHONIOENCODING": "utf-8"}
    completed = subprocess.run(
        [sys.executable, str(SCRIPT)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        env={**dict(__import__("os").environ), **env},
        cwd=str(tmp_path),
        check=False,
    )
    assert completed.returncode == 0, completed.stderr
    assert "cleared successfully" in completed.stdout
