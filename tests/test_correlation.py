"""Severity, result and incident correlation ids on audit rows and drift transitions."""

import asyncio
from collections.abc import Iterator
from pathlib import Path

import pytest

from shared.constants import (
    AuditAction,
    DriftStatus,
    EventResult,
    Severity,
    audit_severity,
    correlation_id_for,
    status_severity,
)
from tests.fakes import FakeGitHub, FakeWatcher, container_from_service
from warden.config import WardenSettings
from warden.core.docker_watcher import LiveContainer
from warden.engine.reconciler import Reconciler
from warden.engine.state_projector import StateProjector
from web.db.repository import Store
from web.db.session import Database

_databases: list[Database] = []


@pytest.fixture(autouse=True)
def _dispose_databases() -> Iterator[None]:
    yield
    while _databases:
        _databases.pop().close()


def build(
    settings: WardenSettings, watcher: FakeWatcher, github: FakeGitHub | None = None
) -> tuple[Reconciler, Store]:
    database = Database(settings.database_url)
    database.init()
    _databases.append(database)
    store = Store(database, settings.hostname)
    projector = StateProjector(settings.hostname, settings.warden_mode)
    return Reconciler(settings, watcher, store, projector, github), store  # type: ignore[arg-type]


def ids(store: Store, ref: str) -> list[tuple[str, str | None]]:
    """``(to_status, correlation_id)`` of an entry's transitions, oldest first."""
    return [
        (str(e["drift_status"]), e["correlation_id"])  # type: ignore[misc]
        for e in reversed(store.list_drift_events())
        if e["ref"] == ref
    ]


# ------------------------------------------------------------------ mapping


def test_severity_and_result_defaults() -> None:
    assert status_severity(DriftStatus.MISSING) is Severity.CRITICAL
    assert status_severity(DriftStatus.ROGUE) is Severity.HIGH
    assert status_severity(DriftStatus.DRIFTED) is Severity.MEDIUM
    assert status_severity(DriftStatus.MATCHED) is Severity.INFO
    assert all(isinstance(audit_severity(a), Severity) for a in AuditAction)
    assert audit_severity(AuditAction.REVERT_FAILED) is Severity.HIGH
    assert correlation_id_for(7) == "INC-007" and correlation_id_for(1234) == "INC-1234"


def test_store_results_and_pending_resolution(settings: WardenSettings) -> None:
    database = Database(settings.database_url)
    database.init()
    _databases.append(database)
    store = Store(database, "h")
    kill = store.record_audit(AuditAction.ENFORCE_KILL, "x")
    detected = store.record_audit(AuditAction.ROGUE_DETECTED, "x")
    opened = store.record_audit(AuditAction.ADOPT_PR_OPENED, "x", details_json={"pr_number": 3})
    failed = store.record_audit(AuditAction.REVERT_COMPLETED, "y", result=EventResult.FAILED)
    rows = {e.id: e for e in store.list_audit()}
    assert rows[kill].result is EventResult.SUCCESS and rows[kill].severity is Severity.MEDIUM
    assert rows[detected].result is None and rows[detected].severity is Severity.HIGH
    assert rows[opened].result is EventResult.PENDING
    assert rows[failed].result is EventResult.FAILED

    assert store.set_audit_result(opened, EventResult.SUCCESS, details_json={"merged": True})
    assert not store.set_audit_result(opened, EventResult.FAILED)  # resolved once only
    assert not store.set_audit_result(kill, EventResult.FAILED)  # never pending
    with pytest.raises(ValueError):
        store.set_audit_result(failed, EventResult.PENDING)
    entry = next(e for e in store.list_audit() if e.id == opened)
    assert entry.result is EventResult.SUCCESS
    assert entry.details_json == {"pr_number": 3, "merged": True}


def test_legacy_rows_migrate_with_safe_defaults(tmp_path: Path) -> None:
    database = Database(f"sqlite:///{(tmp_path / 'old.db').as_posix()}")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE audit_log (id INTEGER PRIMARY KEY, action VARCHAR(64), "
            "target VARCHAR(255), details TEXT, hostname VARCHAR(255), timestamp DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO audit_log (action, target, details, timestamp) "
            "VALUES ('revert_completed', 'web', 'old', '2026-01-01 00:00:00')"
        )
        connection.exec_driver_sql(
            "CREATE TABLE drift_events (id INTEGER PRIMARY KEY, ref VARCHAR(255), "
            "drift_status VARCHAR(32), previous_status VARCHAR(32), service_name VARCHAR(255), "
            "container_name VARCHAR(255), details TEXT, hostname VARCHAR(255), "
            "detected_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO drift_events (ref, drift_status, detected_at) "
            "VALUES ('web', 'missing', '2026-01-01 00:00:00')"
        )
    try:
        database.init()
        store = Store(database, "h")
        [legacy] = store.list_audit()
        assert legacy.result is None and legacy.correlation_id is None
        assert legacy.severity is Severity.MEDIUM and legacy.details_json is None
        [event] = store.list_drift_events()
        assert event["severity"] == "critical" and event["correlation_id"] is None
        assert event["result"] is None and event["details_json"] is None
    finally:
        database.close()


# ---------------------------------------------------------------- incidents


async def test_detection_enforce_and_reverse_share_one_incident(
    settings: WardenSettings,
    rogue_container: LiveContainer,
    web_container: LiveContainer,
    cache_container: LiveContainer,
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, cache_container, rogue_container]
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        detected = next(e for e in store.list_audit() if e.action is AuditAction.ROGUE_DETECTED)
        assert detected.correlation_id == "INC-001" and detected.severity is Severity.HIGH
        assert ids(store, "rogue-db") == [("rogue", "INC-001")]

        await reconciler.kill("rogue-db")
        kill = next(e for e in store.list_audit() if e.action is AuditAction.ENFORCE_KILL)
        assert kill.correlation_id == "INC-001" and kill.result is EventResult.SUCCESS
        await reconciler.reconcile("kill")  # the container is gone: the incident closes
        assert store.open_incidents() == {}

        await reconciler.reverse(kill.id)
        reversal = store.list_audit()[0]
        assert reversal.action is AuditAction.REVERSAL_COMPLETED
        assert reversal.correlation_id == "INC-001"
        await asyncio.sleep(0.05)
        await reconciler.reconcile("reverse")
        restored = reconciler.projector.snapshot.find("rogue-db")
        assert restored is not None and restored.drift_status is DriftStatus.ROGUE
        assert ids(store, "rogue-db")[-1] == ("rogue", "INC-001")  # rejoined, not INC-002
        assert list(store.open_incidents().values()) == ["INC-001"]
    finally:
        await reconciler.stop()


async def test_incident_closes_on_matched_and_next_drift_opens_a_new_one(
    settings: WardenSettings,
    web_container: LiveContainer,
    cache_container: LiveContainer,
    drifted_cache: LiveContainer,
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, cache_container]
    watcher.on_recreate = container_from_service
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        watcher.containers = [web_container, drifted_cache]
        await reconciler.reconcile("sweep")
        await reconciler.revert(drifted_cache.name)
        await reconciler.reconcile("revert")
        trail = ids(store, drifted_cache.name)
        assert trail == [("drifted", "INC-001"), ("matched", "INC-001")]
        recreate = next(e for e in store.list_audit() if e.action is AuditAction.REVERT_COMPLETED)
        assert recreate.correlation_id == "INC-001"
        assert store.open_incidents() == {}

        watcher.containers = [web_container, drifted_cache]
        await reconciler.reconcile("sweep")
        assert ids(store, drifted_cache.name)[-1] == ("drifted", "INC-002")
    finally:
        await reconciler.stop()


async def test_adoption_carries_the_incident_through_merge(
    monkeypatch: pytest.MonkeyPatch,
    fixture_yaml: str,
    rogue_container: LiveContainer,
    web_container: LiveContainer,
    cache_container: LiveContainer,
) -> None:
    monkeypatch.setenv("ALLOW_MOCK_BASELINE", "false")
    monkeypatch.setenv("GITHUB_PAT", "ghp_test")
    monkeypatch.setenv("TARGET_REPO", "owner/infra")
    monkeypatch.setenv("TARGET_BRANCH", "main")
    settings = WardenSettings(_env_file=None)  # type: ignore[call-arg]
    watcher = FakeWatcher(settings)
    watcher.containers = [rogue_container]
    watcher.containers = [web_container, cache_container, rogue_container]
    github = FakeGitHub(fixture_yaml)
    reconciler, store = build(settings, watcher, github)
    await reconciler.start()
    try:
        await reconciler.adopt("rogue-db")
        await reconciler.reconcile("adopt")
        opened = next(e for e in store.list_audit() if e.action is AuditAction.ADOPT_PR_OPENED)
        assert opened.correlation_id == "INC-001" and opened.result is EventResult.PENDING
        github.content = github.mutated or ""
        github.sha = "sha-merged"
        github.pr_states[1] = "merged"
        await reconciler.poll_pull_requests()
        await asyncio.sleep(0.1)
        await reconciler.reconcile("sweep")
        merged = next(e for e in store.list_audit() if e.action is AuditAction.ADOPTION_MERGED)
        assert merged.correlation_id == "INC-001" and merged.result is EventResult.SUCCESS
        assert ids(store, "rogue-db") == [
            ("rogue", "INC-001"),
            ("pending_adoption", "INC-001"),
            ("awaiting_restart", "INC-001"),
        ]
        assert list(store.open_incidents()) == ["service:rogue-db"]  # followed the new key
    finally:
        await reconciler.stop()


async def test_open_incidents_survive_a_restart(
    settings: WardenSettings,
    rogue_container: LiveContainer,
    web_container: LiveContainer,
    cache_container: LiveContainer,
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, cache_container, rogue_container]
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    await reconciler.stop()
    assert list(store.open_incidents().values()) == ["INC-001"]

    restarted = FakeWatcher(settings)
    restarted.containers = [web_container, cache_container, rogue_container]
    projector = StateProjector(settings.hostname, settings.warden_mode)
    again = Reconciler(settings, restarted, store, projector, None)  # type: ignore[arg-type]
    await again.start()
    try:
        assert ids(store, "rogue-db") == [("rogue", "INC-001"), ("rogue", "INC-001")]
    finally:
        await again.stop()
