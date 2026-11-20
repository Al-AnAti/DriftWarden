"""Drift transition matching, trigger labels and the drift_events migration."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

from shared.constants import BaselineSource, DriftStatus
from shared.models import BaselineInfo, DriftRecord, HostSnapshot, PendingAdoption
from warden.engine.state_projector import StateProjector, Transition, transition_trigger
from web.db.repository import Store
from web.db.session import Database

NOW = datetime.now(timezone.utc)


def pending(pr_number: int | None = 38, kind: str = "adopt") -> PendingAdoption:
    return PendingAdoption(
        id=1,
        kind=kind,
        container_id="d" * 64,
        container_name="rogue-web",
        service_name="rogue-web",
        pr_number=pr_number,
        created_at=NOW,
        expires_at=NOW + timedelta(hours=1),
    )


def record(
    status: DriftStatus,
    *,
    service: str | None = None,
    container_id: str | None = "d" * 64,
    buffered: PendingAdoption | None = None,
) -> DriftRecord:
    return DriftRecord(
        ref="rogue-web",
        drift_status=status,
        service_name=service,
        container_id=container_id,
        container_name="rogue-web" if container_id else None,
        status="running",
        detected_at=NOW,
        pending_adoption=buffered,
    )


def snapshot(*records: DriftRecord) -> HostSnapshot:
    return HostSnapshot(
        hostname="h",
        taken_at=NOW,
        trigger="t",
        mode="observe",
        baseline=BaselineInfo(source=BaselineSource.MOCK, available=True),
        records=list(records),
    )


def test_container_fallback_links_pending_adoption_to_awaiting_restart() -> None:
    projector = StateProjector("h", "observe")
    buffered = record(DriftStatus.PENDING_ADOPTION, buffered=pending())
    projector.publish(snapshot(buffered))
    awaiting = record(DriftStatus.AWAITING_RESTART, service="rogue-web")
    [change] = projector.transitions(snapshot(awaiting))
    assert change.previous is buffered
    assert change.previous_status is DriftStatus.PENDING_ADOPTION
    assert transition_trigger("baseline", change) == "Baseline Updated (PR #38)"
    assert transition_trigger("sweep", change) == "Baseline Updated (PR #38)"


def test_container_fallback_never_reuses_a_record_still_present() -> None:
    projector = StateProjector("h", "observe")
    managed = record(DriftStatus.MATCHED, service="web")
    projector.publish(snapshot(managed))
    # The same container id now also shows up keyed by itself: the service row still
    # exists, so the new row is a new entry, not a continuation of the service.
    rogue = record(DriftStatus.ROGUE)
    changes = projector.transitions(snapshot(managed, rogue))
    assert changes == [Transition(rogue, None)]


def test_trigger_labels() -> None:
    rogue = record(DriftStatus.ROGUE)
    buffered = record(DriftStatus.PENDING_ADOPTION, buffered=pending())
    assert transition_trigger("sweep", Transition(rogue, None)) == "Poller Evaluation"
    assert transition_trigger("event", Transition(rogue, None)) == "Docker Event"
    assert transition_trigger("startup", Transition(buffered, None)) == "Engine Startup"
    assert transition_trigger("adopt", Transition(buffered, rogue)) == "Adopt PR #38 Opened"
    removal = record(DriftStatus.PENDING_ADOPTION, buffered=pending(kind="remove"))
    assert transition_trigger("adopt-deletion", Transition(removal, None)) == (
        "Deletion PR #38 Opened"
    )
    assert transition_trigger("pr-closed", Transition(rogue, buffered)) == "Adopt PR #38 Closed"
    assert transition_trigger("revoke", Transition(rogue, buffered)) == (
        "Adoption Revoked (PR #38)"
    )
    assert transition_trigger("reverse", Transition(rogue, buffered)) == "Action Reversed"
    assert transition_trigger("sweep", Transition(rogue, buffered)) == "Adoption Ended (PR #38)"
    matched = record(DriftStatus.MATCHED, service="web")
    drifted = record(DriftStatus.DRIFTED, service="web")
    assert transition_trigger("revert", Transition(matched, drifted)) == "Enforce Recreate"
    assert transition_trigger("post-enforce", Transition(matched, drifted)) == "Enforce Recreate"
    assert transition_trigger("some-new-trigger", Transition(matched, drifted)) == (
        "Some New Trigger"
    )


def test_drift_events_migration_adds_trigger_and_reason(tmp_path: Path) -> None:
    path = tmp_path / "old.db"
    database = Database(f"sqlite:///{path.as_posix()}")
    with database.engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE drift_events (id INTEGER PRIMARY KEY, ref VARCHAR(255), "
            "drift_status VARCHAR(32), previous_status VARCHAR(32), service_name VARCHAR(255), "
            "container_name VARCHAR(255), details TEXT, hostname VARCHAR(255), "
            "detected_at DATETIME)"
        )
        connection.exec_driver_sql(
            "INSERT INTO drift_events (ref, drift_status, details, detected_at) "
            "VALUES ('web', 'drifted', 'old prose', '2026-01-01 00:00:00')"
        )
    try:
        database.init()
        store = Store(database, "h")
        store.record_drift_event(
            ref="web",
            drift_status=DriftStatus.MATCHED,
            previous_status=DriftStatus.DRIFTED,
            service_name="web",
            container_name="web",
            trigger="Enforce Recreate",
            reason="All fields match baseline",
        )
        new, old = store.list_drift_events()
        assert (new["trigger"], new["reason"], new["details"]) == (
            "Enforce Recreate",
            "All fields match baseline",
            None,
        )
        assert old["trigger"] is None and old["reason"] == "old prose"  # legacy rows still read
    finally:
        database.close()
