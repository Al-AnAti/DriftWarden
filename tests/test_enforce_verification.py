"""Enforce without false transitions, verified outcomes and secret-free details."""

import asyncio
from collections.abc import Iterator
from typing import Any

import pytest

from shared.constants import AuditAction, DriftStatus, EventResult, Severity
from shared.exceptions import ContainerRecreateError, DriftWardenError, RogueContainerKillError
from shared.models import BaselineInfo, BatchRequest, FieldDiff, ParsedService
from tests.conftest import PROJECT, make_container
from tests.fakes import FakeWatcher, container_from_service
from warden.config import WardenSettings
from warden.core.docker_watcher import LiveContainer
from warden.engine.event_details import (
    MASK,
    baseline_details,
    image_details,
    mask_diff,
    mask_pairs,
)
from warden.engine.reconciler import Reconciler
from warden.engine.state_projector import StateProjector
from warden.engine.verification import verify_recreated, verify_removed
from web.db.repository import Store
from web.db.session import Database

_databases: list[Database] = []


@pytest.fixture(autouse=True)
def _dispose_databases() -> Iterator[None]:
    yield
    while _databases:
        _databases.pop().close()


def build(settings: WardenSettings, watcher: FakeWatcher) -> tuple[Reconciler, Store]:
    database = Database(settings.database_url)
    database.init()
    _databases.append(database)
    store = Store(database, settings.hostname)
    projector = StateProjector(settings.hostname, settings.warden_mode)
    return Reconciler(settings, watcher, store, projector, None), store  # type: ignore[arg-type]


def timeline(store: Store, ref: str) -> list[tuple[str | None, str, str | None]]:
    """``(from, to, trigger)`` of an entry's transitions, oldest first."""
    return [
        (e["previous_status"], e["drift_status"], e["trigger"])  # type: ignore[misc]
        for e in reversed(store.list_drift_events())
        if e["ref"] == ref
    ]


def wrong_image(service: ParsedService, project: str) -> LiveContainer:
    """A recreate that comes out with the wrong image (verification must fail)."""
    container = container_from_service(service, project)
    container.image = "redis:7-bookworm"
    return container


# ------------------------------------------------------------------ the race


async def test_cycle_during_enforce_records_no_false_missing(
    settings: WardenSettings,
    web_container: LiveContainer,
    drifted_cache: LiveContainer,
    rogue_container: LiveContainer,
) -> None:
    """A reconcile that runs between removal and recreation keeps the entry DRIFTED.

    The fake's ``recreate`` has already dropped the old container when the hook
    runs, exactly like Docker between ``remove`` and ``create``; the hook then drives
    a full cycle on the loop (an unrelated rogue container appears at the same time,
    so genuine external changes must still be recorded).
    """
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, drifted_cache]
    reconciler, store = build(settings, watcher)
    loop = asyncio.get_running_loop()
    mid_cycle: dict[str, Any] = {}

    def recreate_with_concurrent_cycle(service: ParsedService, project: str) -> LiveContainer:
        assert all(c.name != drifted_cache.name for c in watcher.containers)  # really gone
        watcher.containers.append(rogue_container)
        future = asyncio.run_coroutine_threadsafe(reconciler.reconcile("event"), loop)
        mid_cycle["snapshot"] = future.result(timeout=5)
        return container_from_service(service, project)

    watcher.on_recreate = recreate_with_concurrent_cycle
    await reconciler.start()
    try:
        cache = reconciler.projector.snapshot.find(drifted_cache.name)
        assert cache is not None and cache.drift_status is DriftStatus.DRIFTED

        result = await reconciler.revert(drifted_cache.name)
        assert result.ok
        held = mid_cycle["snapshot"].find(drifted_cache.name)
        assert held is not None and held.drift_status is DriftStatus.DRIFTED  # not MISSING
        assert held.container_id == drifted_cache.id
        assert mid_cycle["snapshot"].find("rogue-db") is not None  # the real change shows
        await asyncio.sleep(0.05)  # the post-revert cycle

        assert timeline(store, drifted_cache.name) == [
            (None, "drifted", "Engine Startup"),
            ("drifted", "matched", "Enforce Recreate"),
        ]
        actions = [row.action for row in store.list_audit()]
        assert AuditAction.MISSING_DETECTED not in actions
        assert actions.count(AuditAction.DRIFT_DETECTED) == 1
        assert actions.count(AuditAction.ROGUE_DETECTED) == 1  # external event still logged
        assert actions.count(AuditAction.REVERT_COMPLETED) == 1
        assert list(store.open_incidents()) == ["container:" + rogue_container.id]
    finally:
        await reconciler.stop()


async def test_cycle_during_kill_keeps_the_rogue_until_it_is_gone(
    settings: WardenSettings, web_container: LiveContainer, rogue_container: LiveContainer
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, rogue_container]
    reconciler, store = build(settings, watcher)
    loop = asyncio.get_running_loop()
    seen: list[DriftStatus | None] = []
    original = watcher.stop_and_remove

    def remove_with_concurrent_cycle(container_id: str, timeout: int = 10) -> None:
        original(container_id, timeout)
        snapshot = asyncio.run_coroutine_threadsafe(reconciler.reconcile("sweep"), loop).result(5)
        found = snapshot.find("rogue-db")
        seen.append(found.drift_status if found else None)

    watcher.stop_and_remove = remove_with_concurrent_cycle  # type: ignore[method-assign]
    await reconciler.start()
    try:
        await reconciler.kill("rogue-db")
        assert seen == [DriftStatus.ROGUE]  # still shown while Enforce holds it
        await asyncio.sleep(0.05)
        assert reconciler.projector.snapshot.find("rogue-db") is None
        assert timeline(store, "rogue-db") == [
            (None, "rogue", "Engine Startup"),
            ("rogue", "removed", "Enforce Remove"),
        ]
    finally:
        await reconciler.stop()


async def test_second_action_on_a_held_entry_is_refused(
    settings: WardenSettings, web_container: LiveContainer, drifted_cache: LiveContainer
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, drifted_cache]
    reconciler, _ = build(settings, watcher)
    await reconciler.start()
    try:
        record = reconciler.projector.snapshot.find(drifted_cache.name)
        assert record is not None
        reconciler._remediating["service:cache"] = record
        with pytest.raises(DriftWardenError, match="already being enforced"):
            await reconciler.revert(drifted_cache.name)
        reconciler._remediating.clear()
        assert (await reconciler.revert(drifted_cache.name)).ok
    finally:
        await reconciler.stop()


# -------------------------------------------------------------- verification


async def test_verified_recreate_resolves_the_audit_row_to_success(
    settings: WardenSettings, web_container: LiveContainer, drifted_cache: LiveContainer
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, drifted_cache]
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        result = await reconciler.revert(drifted_cache.name)
        assert result.ok and "verified" in result.message
        row = next(r for r in store.list_audit() if r.action is AuditAction.REVERT_COMPLETED)
        assert row.result is EventResult.SUCCESS and row.severity is Severity.MEDIUM
        assert row.details and "verified: container rcache" in row.details
        details = row.details_json or {}
        new_id = next(c.id for c in watcher.containers if c.name == drifted_cache.name)
        assert details["service_name"] == "cache"
        assert details["container_name"] == drifted_cache.name
        assert details["previous_container_id"] == drifted_cache.id
        assert details["new_container_id"] == new_id
        assert details["image"] == "redis:alpine" and details["image_tag"] == "alpine"
        assert details["previous_image"] == "redis:latest"
        assert details["baseline_source"] == "mock" and details["git_sha"]
        assert details["trigger_source"] == "manual Enforce"
        assert details["previous_status"] == "drifted"
        assert details["verified"] is True and details["container_state"] == "running"
        changed = {d["field"] for d in details["diff"]}
        assert {"image", "ports", "networks"} <= changed
        assert "verification_diff" not in details
        assert "autonomous" not in (row.details or "")
    finally:
        await reconciler.stop()


async def test_unverified_recreate_fails_the_row_and_keeps_the_entry_drifted(
    settings: WardenSettings, web_container: LiveContainer, drifted_cache: LiveContainer
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, drifted_cache]
    watcher.on_recreate = wrong_image
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        with pytest.raises(ContainerRecreateError, match="verification failed") as failure:
            await reconciler.revert(drifted_cache.name)
        assert "differs from the baseline on image" in str(failure.value)
        row = next(r for r in store.list_audit() if r.action is AuditAction.REVERT_COMPLETED)
        assert row.result is EventResult.FAILED
        assert row.details and "verification failed" in row.details
        details = row.details_json or {}
        assert details["verified"] is False
        assert [d["field"] for d in details["verification_diff"]] == ["image"]
        assert details["verification_diff"][0]["observed"] == "redis:7-bookworm"
        assert AuditAction.REVERT_FAILED not in [r.action for r in store.list_audit()]
        assert len(watcher.recreated) == 1  # no retry inside the action

        await asyncio.sleep(0.05)
        record = reconciler.projector.snapshot.find(drifted_cache.name)
        assert record is not None and record.drift_status is DriftStatus.DRIFTED
        assert timeline(store, drifted_cache.name) == [(None, "drifted", "Engine Startup")]
        assert list(store.open_incidents()) == ["service:cache"]  # the incident stays open
    finally:
        await reconciler.stop()


async def test_recreate_whose_container_never_appears_fails_verification(
    settings: WardenSettings, web_container: LiveContainer
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container]  # cache MISSING
    watcher.on_recreate = None  # the fake returns an id but no container exists
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        with pytest.raises(ContainerRecreateError, match="does not exist after recreate"):
            await reconciler.revert("cache")
        row = next(r for r in store.list_audit() if r.action is AuditAction.REVERT_COMPLETED)
        assert row.result is EventResult.FAILED
        await asyncio.sleep(0.05)
        record = reconciler.projector.snapshot.find("cache")
        assert record is not None and record.drift_status is DriftStatus.MISSING
    finally:
        await reconciler.stop()


async def test_autonomous_enforce_does_not_retry_a_failed_verification(
    monkeypatch: pytest.MonkeyPatch, web_container: LiveContainer, drifted_cache: LiveContainer
) -> None:
    monkeypatch.setenv("WARDEN_MODE", "enforce")
    settings = WardenSettings(_env_file=None)  # type: ignore[call-arg]
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, drifted_cache]
    watcher.on_recreate = wrong_image
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        await reconciler.reconcile("sweep")
        await asyncio.sleep(0.1)  # post-enforce cycle
        assert len(watcher.recreated) == 1
        record = reconciler.projector.snapshot.find(drifted_cache.name)
        assert record is not None and record.drift_status is DriftStatus.DRIFTED
        await reconciler.reconcile("sweep")  # inside the cooldown: no second attempt
        await asyncio.sleep(0.05)
        assert len(watcher.recreated) == 1
        rows = [r for r in store.list_audit() if r.action is AuditAction.REVERT_COMPLETED]
        assert len(rows) == 1 and rows[0].result is EventResult.FAILED
        assert (rows[0].details_json or {})["trigger_source"] == "autonomous enforcement"
        assert "autonomous" in (rows[0].details or "")
        assert timeline(store, drifted_cache.name) == [(None, "drifted", "Engine Startup")]
    finally:
        await reconciler.stop()


async def test_autonomous_enforce_verifies_and_converges(
    monkeypatch: pytest.MonkeyPatch, drifted_cache: LiveContainer, rogue_container: LiveContainer
) -> None:
    monkeypatch.setenv("WARDEN_MODE", "enforce")
    settings = WardenSettings(_env_file=None)  # type: ignore[call-arg]
    watcher = FakeWatcher(settings)
    watcher.containers = [drifted_cache, rogue_container]  # web MISSING
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        await reconciler.reconcile("sweep")
        await asyncio.sleep(0.1)
        assert reconciler.projector.snapshot.overall == "in_sync"
        rows = [
            r
            for r in store.list_audit()
            if r.action in (AuditAction.REVERT_COMPLETED, AuditAction.ENFORCE_KILL)
        ]
        assert len(rows) == 3 and all(r.result is EventResult.SUCCESS for r in rows)
        assert all((r.details_json or {})["verified"] is True for r in rows)
        assert timeline(store, drifted_cache.name)[-1] == ("drifted", "matched", "Enforce Recreate")
        assert timeline(store, "web") == [(None, "missing", "Engine Startup")]
        assert timeline(store, f"{PROJECT}-web-1") == [("missing", "matched", "Enforce Recreate")]
        assert timeline(store, "rogue-db")[-1] == ("rogue", "removed", "Enforce Remove")
        assert store.open_incidents() == {}
    finally:
        await reconciler.stop()


# ------------------------------------------------------------ rogue removal


async def test_kill_records_rogue_to_removed_once_and_closes_the_incident(
    settings: WardenSettings,
    web_container: LiveContainer,
    cache_container: LiveContainer,
    rogue_container: LiveContainer,
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, cache_container, rogue_container]
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        result = await reconciler.kill("rogue-db")
        assert result.ok
        kill = next(r for r in store.list_audit() if r.action is AuditAction.ENFORCE_KILL)
        assert kill.result is EventResult.SUCCESS and kill.correlation_id == "INC-001"
        assert kill.details and "verified: container dddddddddddd no longer exists" in kill.details
        details = kill.details_json or {}
        assert details["action"] == "remove" and details["trigger_source"] == "manual Enforce"
        assert details["container_name"] == "rogue-db"
        assert details["previous_container_id"] == rogue_container.id
        assert details["image"] == "mariadb:latest" and details["image_tag"] == "latest"
        assert "diff" not in details and "MARIADB_ROOT_PASSWORD" not in str(details)

        await asyncio.sleep(0.05)
        await reconciler.reconcile("sweep")  # nothing more to say about it
        rows = [e for e in reversed(store.list_drift_events()) if e["ref"] == "rogue-db"]
        assert [(e["previous_status"], e["drift_status"]) for e in rows] == [
            (None, "rogue"),
            ("rogue", "removed"),
        ]
        removed = rows[-1]
        assert removed["trigger"] == "Enforce Remove"
        assert removed["reason"] == "Stopped and removed by Enforce"
        assert removed["severity"] == "info" and removed["result"] == "success"
        assert removed["correlation_id"] == "INC-001"
        assert removed["details_json"]["previous_status"] == "rogue"  # type: ignore[index]
        assert store.open_incidents() == {}
    finally:
        await reconciler.stop()


async def test_kill_that_leaves_the_container_behind_fails_verification(
    settings: WardenSettings,
    web_container: LiveContainer,
    cache_container: LiveContainer,
    rogue_container: LiveContainer,
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, cache_container, rogue_container]

    def remove_does_nothing(container_id: str, timeout: int = 10) -> None:
        watcher.removed.append(container_id)

    watcher.stop_and_remove = remove_does_nothing  # type: ignore[method-assign]
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        with pytest.raises(RogueContainerKillError, match="still exists"):
            await reconciler.kill("rogue-db")
        kill = next(r for r in store.list_audit() if r.action is AuditAction.ENFORCE_KILL)
        assert kill.result is EventResult.FAILED
        assert (kill.details_json or {})["verified"] is False
        assert AuditAction.ENFORCE_KILL_FAILED not in [r.action for r in store.list_audit()]
        await asyncio.sleep(0.05)
        record = reconciler.projector.snapshot.find("rogue-db")
        assert record is not None and record.drift_status is DriftStatus.ROGUE
        assert all(e["drift_status"] != "removed" for e in store.list_drift_events())
        assert list(store.open_incidents().values()) == ["INC-001"]
    finally:
        await reconciler.stop()


async def test_batch_enforce_marks_its_source_and_reports_unverified_entries(
    settings: WardenSettings, drifted_cache: LiveContainer, rogue_container: LiveContainer
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [drifted_cache, rogue_container]
    watcher.on_recreate = wrong_image
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        outcome = await reconciler.batch(BatchRequest(operation="enforce", scope="all"))
        by_target = {r.target: r for r in outcome.results}
        assert by_target["rogue-db"].ok
        assert not by_target[drifted_cache.name].ok and not by_target["web"].ok
        assert "verification failed" in by_target["web"].message
        assert outcome.succeeded == 1 and outcome.failed == 2
        sources = {
            (r.details_json or {}).get("trigger_source")
            for r in store.list_audit()
            if r.action in (AuditAction.REVERT_COMPLETED, AuditAction.ENFORCE_KILL)
        }
        assert sources == {"batch Enforce"}
    finally:
        await reconciler.stop()


# ------------------------------------------------------- details and masking


def test_environment_values_are_masked_but_placeholders_survive() -> None:
    text = "PASSWORD=hunter2, PATH=/usr/bin, KEY=${KEY}, GONE=<unset>"
    assert mask_pairs(text, mask_all=True) == (
        f"PASSWORD={MASK}, PATH={MASK}, KEY=${{KEY}}, GONE=<unset>"
    )
    assert mask_pairs(text, mask_all=False) == (
        f"PASSWORD={MASK}, PATH=/usr/bin, KEY=${{KEY}}, GONE=<unset>"
    )
    assert mask_pairs("(none)", mask_all=True) == "(none)"
    assert mask_pairs(None, mask_all=True) is None

    env = FieldDiff(field="environment", expected="A=1", observed="A=2", changed=True)
    assert mask_diff(env)["expected"] == f"A={MASK}" and mask_diff(env)["observed"] == f"A={MASK}"
    labels = FieldDiff(
        field="labels", expected="tier=web, api_token=abc", observed="tier=db", changed=True
    )
    assert mask_diff(labels)["expected"] == f"tier=web, api_token={MASK}"
    ports = FieldDiff(field="ports", expected="8080:80/tcp", observed="8081:80/tcp", changed=True)
    assert mask_diff(ports)["observed"] == "8081:80/tcp"


async def test_detection_rows_and_transitions_carry_masked_details(
    settings: WardenSettings, web_container: LiveContainer, rogue_container: LiveContainer
) -> None:
    drifted = make_container(
        id="c" * 64,
        name=f"{PROJECT}-cache-1",
        image="redis:alpine",
        labels=dict(web_container.labels, **{"com.docker.compose.service": "cache"}),
        ports=["6379:6379/tcp"],
        environment={"REDIS_PASSWORD": "s3cret"},
    )
    yaml_with_env = (
        "services:\n  cache:\n    image: redis:alpine\n    ports: ['6379:6379']\n"
        "    environment:\n      REDIS_PASSWORD: baseline-secret\n"
    )
    watcher = FakeWatcher(settings)
    watcher.containers = [drifted, rogue_container]
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    assert reconciler.baseline is not None
    from warden.core.baseline_parser import parse_baseline

    reconciler.baseline = parse_baseline(
        yaml_with_env, fallback_namespace=PROJECT, sha="x", source=reconciler.baseline.source
    )
    try:
        await reconciler.reconcile("sweep")
        audit = next(r for r in store.list_audit() if r.action is AuditAction.DRIFT_DETECTED)
        details = audit.details_json or {}
        assert details["service_name"] == "cache" and details["trigger_source"]
        [env] = [d for d in details["diff"] if d["field"] == "environment"]
        assert env == {
            "field": "environment",
            "expected": f"REDIS_PASSWORD={MASK}",
            "observed": f"REDIS_PASSWORD={MASK}",
            "changed": True,
        }
        dumped = str(store.list_drift_events()) + str(store.list_audit())
        assert "s3cret" not in dumped and "baseline-secret" not in dumped
        event = next(e for e in store.list_drift_events() if e["ref"] == drifted.name)
        assert event["details_json"]["previous_status"] == "matched"  # type: ignore[index]
        rogue = next(r for r in store.list_audit() if r.action is AuditAction.ROGUE_DETECTED)
        assert (rogue.details_json or {})["container_id"] == rogue_container.id
        assert "rogue" not in str((rogue.details_json or {}).get("diff", ""))
    finally:
        await reconciler.stop()


def test_image_and_baseline_details() -> None:
    assert image_details("nginx") == {
        "image": "nginx",
        "image_repository": "nginx",
        "image_tag": "latest",
    }
    assert image_details("ghcr.io/org/app:1.2") == {
        "image": "ghcr.io/org/app:1.2",
        "image_repository": "ghcr.io/org/app",
        "image_tag": "1.2",
    }
    assert image_details("redis@sha256:" + "a" * 64) == {"image": "redis@sha256:" + "a" * 64}
    assert image_details(None) == {}
    from shared.constants import BaselineSource

    github = BaselineInfo(
        source=BaselineSource.GITHUB,
        available=True,
        repo="owner/infra",
        branch="main",
        path="docker-compose.yml",
        sha="abc123",
    )
    assert baseline_details(github) == {
        "baseline_source": "github",
        "git_repo": "owner/infra",
        "git_branch": "main",
        "baseline_path": "docker-compose.yml",
        "git_sha": "abc123",
    }
    assert baseline_details(BaselineInfo(source=BaselineSource.UNAVAILABLE, available=False)) == {
        "baseline_source": "unavailable"
    }


def test_verification_functions(baseline: Any) -> None:
    service = baseline.services["cache"]
    good = container_from_service(service, PROJECT)
    assert verify_recreated(good, service, PROJECT, expected_name=good.name, expected_id=good.id).ok
    assert not verify_recreated(None, service, PROJECT, expected_name="x", expected_id="y").ok
    renamed = verify_recreated(good, service, PROJECT, expected_name="other", expected_id=good.id)
    assert not renamed.ok and "is named" in renamed.reason
    swapped = verify_recreated(good, service, PROJECT, expected_name=good.name, expected_id="z")
    assert not swapped.ok and "not the one Enforce created" in swapped.reason
    stopped = container_from_service(service, PROJECT)
    stopped.state = "exited"
    outcome = verify_recreated(
        stopped, service, PROJECT, expected_name=stopped.name, expected_id=stopped.id
    )
    assert not outcome.ok and outcome.changed_fields == ["state"]
    assert verify_removed(None, "d" * 64).ok
    assert not verify_removed(good, good.id).ok
