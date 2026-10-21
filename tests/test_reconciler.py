"""Engine behaviour with Docker and GitHub replaced by in-memory fakes."""

import asyncio
from collections.abc import AsyncIterator, Iterator
from pathlib import Path

import pytest

from shared.constants import AuditAction, BaselineSource, DriftStatus, WhitelistTargetType
from shared.exceptions import DriftWardenError
from shared.models import RawDockerEvent, WhitelistRequest
from tests.conftest import PROJECT, compose_labels, make_container
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
    """Close every SQLite engine a test opened so no connection leaks."""
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
    reconciler = Reconciler(settings, watcher, store, projector, github)  # type: ignore[arg-type]
    return reconciler, store


@pytest.fixture
async def started(
    settings: WardenSettings, web_container: LiveContainer, cache_container: LiveContainer
) -> AsyncIterator[tuple[Reconciler, Store, FakeWatcher]]:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, cache_container]
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        yield reconciler, store, watcher
    finally:
        await reconciler.stop()


async def test_startup_loads_baseline_and_observes(
    started: tuple[Reconciler, Store, FakeWatcher],
) -> None:
    reconciler, store, _ = started
    snapshot = reconciler.projector.snapshot
    assert snapshot.trigger == "startup"
    assert snapshot.baseline.available and snapshot.baseline.source is BaselineSource.MOCK
    assert snapshot.baseline.project_name == PROJECT
    assert snapshot.counts()["matched"] == 2
    assert snapshot.overall == "in_sync"
    actions = [row.action for row in store.list_audit()]
    assert AuditAction.ENGINE_STARTED in actions and AuditAction.BASELINE_FETCHED in actions


async def test_event_triggers_reconcile_and_records_transitions(
    started: tuple[Reconciler, Store, FakeWatcher], rogue_container: LiveContainer
) -> None:
    reconciler, store, watcher = started
    version = reconciler.projector.version
    watcher.containers.append(rogue_container)
    reconciler._queue.put_nowait(
        RawDockerEvent(
            container_id=rogue_container.id, action="start", attributes={"name": "rogue-db"}
        )
    )
    for _ in range(50):
        await asyncio.sleep(0.02)
        if reconciler.projector.version > version:
            break
    snapshot = reconciler.projector.snapshot
    assert snapshot.trigger == "event"
    assert snapshot.find("rogue-db") is not None
    assert snapshot.overall == "drift_detected"
    events = store.list_drift_events()
    assert events[0]["ref"] == "rogue-db" and events[0]["drift_status"] == "rogue"
    assert AuditAction.ROGUE_DETECTED in [row.action for row in store.list_audit()]


async def test_own_actions_do_not_trigger_reconcile(
    started: tuple[Reconciler, Store, FakeWatcher],
) -> None:
    reconciler, _, _ = started
    version = reconciler.projector.version
    reconciler._remember_action("self-id")
    reconciler._queue.put_nowait(RawDockerEvent(container_id="self-id", action="die"))
    await asyncio.sleep(0.2)
    assert reconciler.projector.version == version


async def test_manual_revert_recreates_drifted_and_missing(
    started: tuple[Reconciler, Store, FakeWatcher], drifted_cache: LiveContainer
) -> None:
    reconciler, store, watcher = started
    watcher.containers = [drifted_cache]  # web gone, cache spoofed
    await reconciler.reconcile("test")
    snapshot = reconciler.projector.snapshot
    web = snapshot.find("web")
    cache = snapshot.find(drifted_cache.name)
    assert web is not None and web.drift_status is DriftStatus.MISSING
    assert cache is not None and cache.drift_status is DriftStatus.DRIFTED

    result = await reconciler.revert(drifted_cache.name)
    assert result.ok and "cache" in result.message
    result = await reconciler.revert("web")
    assert result.ok
    assert [r[0] for r in watcher.recreated] == ["cache", "web"]
    actions = [row.action for row in store.list_audit()]
    assert actions.count(AuditAction.REVERT_REQUESTED) == 2
    assert actions.count(AuditAction.REVERT_COMPLETED) == 2
    with pytest.raises(DriftWardenError):
        await reconciler.revert("does-not-exist")


async def test_kill_only_applies_to_rogue(
    started: tuple[Reconciler, Store, FakeWatcher], rogue_container: LiveContainer
) -> None:
    reconciler, store, watcher = started
    with pytest.raises(DriftWardenError):
        await reconciler.kill(f"{PROJECT}-web-1")
    watcher.containers.append(rogue_container)
    await reconciler.reconcile("test")
    result = await reconciler.kill("rogue-db")
    assert result.ok and watcher.removed == [rogue_container.id]
    assert AuditAction.ENFORCE_KILL in [row.action for row in store.list_audit()]


async def test_whitelist_exempts_and_reload(
    started: tuple[Reconciler, Store, FakeWatcher], rogue_container: LiveContainer
) -> None:
    reconciler, _, watcher = started
    other = make_container(id="7" * 64, name="other-api-1", labels=compose_labels("api", "other"))
    watcher.containers += [rogue_container, other]
    await reconciler.reconcile("test")
    assert reconciler.projector.snapshot.counts()["rogue"] == 2
    await reconciler.whitelist(
        WhitelistRequest(
            target_type=WhitelistTargetType.CONTAINER, target_identifier="rogue-db", reason="x"
        )
    )
    entry = await reconciler.whitelist(
        WhitelistRequest(
            target_type=WhitelistTargetType.PROJECT, target_identifier="other", reason="y"
        )
    )
    await reconciler.reconcile("test")
    snapshot = reconciler.projector.snapshot
    assert snapshot.counts()["rogue"] == 0
    assert sorted(snapshot.exempt) == [
        "other-api-1 (whitelisted project other)",
        "rogue-db (whitelisted container)",
    ]
    assert await reconciler.unwhitelist(entry.id) is not None
    assert await reconciler.unwhitelist(999) is None
    await reconciler.reconcile("test")
    assert reconciler.projector.snapshot.counts()["rogue"] == 1


async def test_docker_outage_marks_snapshot(started: tuple[Reconciler, Store, FakeWatcher]) -> None:
    reconciler, _, watcher = started
    watcher.fail_listing = True
    snapshot = await reconciler.reconcile("test")
    assert not snapshot.docker_connected and snapshot.docker_error and snapshot.records == []
    assert snapshot.overall == "docker_unavailable"
    watcher.fail_listing = False
    snapshot = await reconciler.reconcile("test")
    assert snapshot.docker_connected and snapshot.counts()["matched"] == 2


async def test_enforce_mode_startup_is_observe_only_then_acts(
    monkeypatch: pytest.MonkeyPatch,
    drifted_cache: LiveContainer,
    rogue_container: LiveContainer,
) -> None:
    monkeypatch.setenv("WARDEN_MODE", "enforce")
    settings = WardenSettings(_env_file=None)  # type: ignore[call-arg]
    watcher = FakeWatcher(settings)
    watcher.containers = [drifted_cache, rogue_container]
    watcher.on_recreate = container_from_service
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        assert watcher.removed == [] and watcher.recreated == []  # startup pass never enforces
        assert reconciler.enforcement_active
        await reconciler.reconcile("sweep")
        await asyncio.sleep(0.1)  # post-enforce cycle
        assert rogue_container.id in watcher.removed
        assert sorted(r[0] for r in watcher.recreated) == ["cache", "web"]
        assert reconciler.projector.snapshot.overall == "in_sync"
        await reconciler.reconcile("sweep")
        assert len(watcher.recreated) == 2  # converged: nothing left to enforce
        actions = [row.action for row in store.list_audit()]
        assert (
            AuditAction.ENFORCE_KILL in actions and actions.count(AuditAction.REVERT_COMPLETED) == 2
        )
        assert all(
            "autonomous" in (row.details or "")
            for row in store.list_audit()
            if row.action is AuditAction.ENFORCE_KILL
        )
    finally:
        await reconciler.stop()


async def test_grace_window_suppresses_enforcement(
    monkeypatch: pytest.MonkeyPatch, rogue_container: LiveContainer
) -> None:
    monkeypatch.setenv("WARDEN_MODE", "enforce")
    monkeypatch.setenv("ENFORCEMENT_GRACE_SECONDS", "3600")
    settings = WardenSettings(_env_file=None)  # type: ignore[call-arg]
    watcher = FakeWatcher(settings)
    watcher.containers = [rogue_container]
    reconciler, _ = build(settings, watcher)
    await reconciler.start()
    try:
        assert not reconciler.enforcement_active
        await reconciler.reconcile("sweep")
        assert watcher.removed == []
    finally:
        await reconciler.stop()


async def test_adopt_opens_pr_buffers_and_clears_on_baseline_change(
    monkeypatch: pytest.MonkeyPatch,
    fixture_yaml: str,
    rogue_container: LiveContainer,
    drifted_cache: LiveContainer,
) -> None:
    monkeypatch.setenv("ALLOW_MOCK_BASELINE", "false")
    monkeypatch.setenv("GITHUB_PAT", "ghp_test")
    monkeypatch.setenv("TARGET_REPO", "owner/infra")
    monkeypatch.setenv("TARGET_BRANCH", "main")
    monkeypatch.setenv("ADOPTION_BUFFER_TTL_SECONDS", "3600")
    settings = WardenSettings(_env_file=None)  # type: ignore[call-arg]
    watcher = FakeWatcher(settings)
    watcher.containers = [rogue_container, drifted_cache]
    github = FakeGitHub(fixture_yaml)
    reconciler, store = build(settings, watcher, github)
    await reconciler.start()
    try:
        assert reconciler.projector.snapshot.baseline.source is BaselineSource.GITHUB
        assert reconciler.projector.snapshot.baseline.sha == "sha-1"

        result = await reconciler.adopt("rogue-db", note="hotfix")
        assert result.ok and result.pr_url == "https://github.test/pull/1"
        assert github.pulls[0]["service"] == "rogue-db" and "hotfix" in github.pulls[0]["body"]
        assert github.mutated and "MARIADB_ROOT_PASSWORD=${MARIADB_ROOT_PASSWORD}" in github.mutated
        assert "rogue" not in github.mutated.split("MARIADB_ROOT_PASSWORD=")[1].split("\n")[0]

        github.content = github.mutated  # the next PR mutates on top of the previous one
        result = await reconciler.adopt(drifted_cache.name)
        assert result.ok and github.pulls[1]["service"] == "cache"
        assert "image: redis:latest" in (github.mutated or "")

        assert len(reconciler.pending) == 2
        await reconciler.reconcile("test")
        snapshot = reconciler.projector.snapshot
        assert snapshot.counts()["pending_adoption"] == 2
        with pytest.raises(DriftWardenError):
            await reconciler.revert(drifted_cache.name)  # exempt while buffered

        github.content = github.mutated or ""
        github.sha = "sha-2"
        assert await reconciler.refresh_baseline(force=True)
        await asyncio.sleep(0.1)
        assert [p.container_name for p in reconciler.pending] == []
        outcomes = {p.container_name: outcome for p, outcome in store.pending_history()}
        assert outcomes == {"rogue-db": "baseline_updated", drifted_cache.name: "baseline_updated"}
        assert AuditAction.ADOPTION_CLEARED in [row.action for row in store.list_audit()]
    finally:
        await reconciler.stop()


async def test_buffer_follows_pull_request_state(
    monkeypatch: pytest.MonkeyPatch, fixture_yaml: str, rogue_container: LiveContainer
) -> None:
    monkeypatch.setenv("ALLOW_MOCK_BASELINE", "false")
    monkeypatch.setenv("GITHUB_PAT", "ghp_test")
    monkeypatch.setenv("TARGET_REPO", "owner/infra")
    monkeypatch.setenv("TARGET_BRANCH", "main")
    monkeypatch.setenv("ADOPTION_BUFFER_TTL_SECONDS", "0")  # TTL must not matter with a PR
    settings = WardenSettings(_env_file=None)  # type: ignore[call-arg]
    watcher = FakeWatcher(settings)
    watcher.containers = [rogue_container]
    github = FakeGitHub(fixture_yaml)
    reconciler, store = build(settings, watcher, github)
    await reconciler.start()
    try:
        result = await reconciler.adopt("rogue-db")
        assert result.pr_url
        body = github.pulls[0]["body"]
        assert body.startswith("## DriftWarden Reverse-GitOps adoption")
        assert "**State:** ROGUE" in body and "**Finding:**" not in body
        assert "rogue process 'rogue-db'" in body.split("**State:** ROGUE")[1].splitlines()[1]
        await asyncio.sleep(0.05)
        await reconciler.reconcile("test")
        record = reconciler.projector.snapshot.find("rogue-db")
        assert record is not None and record.drift_status is DriftStatus.PENDING_ADOPTION

        github.pull_state_error = True  # GitHub hiccup: stay buffered
        await reconciler.poll_pull_requests()
        assert len(reconciler.pending) == 1
        github.pull_state_error = False
        await reconciler.poll_pull_requests()  # still open: stay buffered
        assert len(reconciler.pending) == 1 and reconciler.pending[0].pr_state == "open"

        github.pr_states[1] = "closed"
        await reconciler.poll_pull_requests()
        await asyncio.sleep(0.05)
        assert reconciler.pending == []
        assert store.pending_history()[0][1] == "pr_closed"
        record = reconciler.projector.snapshot.find("rogue-db")
        assert record is not None and record.drift_status is DriftStatus.ROGUE
        actions = [row.action for row in store.list_audit()]
        assert AuditAction.ADOPTION_CLOSED in actions
        assert AuditAction.ADOPTION_EXPIRED not in actions
    finally:
        await reconciler.stop()


async def test_merged_pull_request_refreshes_baseline_and_clears_buffer(
    monkeypatch: pytest.MonkeyPatch, fixture_yaml: str, rogue_container: LiveContainer
) -> None:
    monkeypatch.setenv("ALLOW_MOCK_BASELINE", "false")
    monkeypatch.setenv("GITHUB_PAT", "ghp_test")
    monkeypatch.setenv("TARGET_REPO", "owner/infra")
    monkeypatch.setenv("TARGET_BRANCH", "main")
    settings = WardenSettings(_env_file=None)  # type: ignore[call-arg]
    watcher = FakeWatcher(settings)
    watcher.containers = [rogue_container]
    github = FakeGitHub(fixture_yaml)
    reconciler, store = build(settings, watcher, github)
    await reconciler.start()
    try:
        await reconciler.adopt("rogue-db")
        github.content = github.mutated or ""
        github.sha = "sha-merged"
        github.pr_states[1] = "merged"
        await reconciler.poll_pull_requests()
        await asyncio.sleep(0.1)
        assert reconciler.pending == []
        assert store.pending_history()[0][1] == "baseline_updated"
        actions = [row.action for row in store.list_audit()]
        assert AuditAction.ADOPTION_MERGED in actions and AuditAction.ADOPTION_CLEARED in actions
        record = reconciler.projector.snapshot.find("rogue-db")
        assert record is not None and record.drift_status is not DriftStatus.PENDING_ADOPTION
        assert record.service_name == "rogue-db"  # now declared in the merged baseline
    finally:
        await reconciler.stop()


async def test_github_failure_starts_degraded_and_recovers(
    monkeypatch: pytest.MonkeyPatch, fixture_yaml: str, web_container: LiveContainer
) -> None:
    monkeypatch.setenv("ALLOW_MOCK_BASELINE", "false")
    monkeypatch.setenv("GITHUB_PAT", "ghp_bad")
    monkeypatch.setenv("TARGET_REPO", "owner/infra")
    monkeypatch.setenv("TARGET_BRANCH", "main")
    settings = WardenSettings(_env_file=None)  # type: ignore[call-arg]
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container]
    github = FakeGitHub(fixture_yaml)
    github.fail = True
    reconciler, store = build(settings, watcher, github)
    await reconciler.start()
    try:
        snapshot = reconciler.projector.snapshot
        assert not snapshot.baseline.available
        assert snapshot.overall == "baseline_unavailable"
        assert "401" in (snapshot.baseline.error or "")
        assert snapshot.records == []
        assert AuditAction.BASELINE_FETCH_FAILED in [row.action for row in store.list_audit()]

        github.fail = False
        assert await reconciler.refresh_baseline()
        await asyncio.sleep(0.1)
        snapshot = reconciler.projector.snapshot
        assert snapshot.baseline.available and snapshot.counts()["matched"] == 1
    finally:
        await reconciler.stop()


async def test_mock_baseline_reload_on_file_change(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, fixture_yaml: str
) -> None:
    path = tmp_path / "compose.yaml"
    path.write_text(fixture_yaml, encoding="utf-8")
    monkeypatch.setenv("MOCK_BASELINE_PATH", str(path))
    settings = WardenSettings(_env_file=None)  # type: ignore[call-arg]
    reconciler, _ = build(settings, FakeWatcher(settings))
    await reconciler.start()
    try:
        assert not await reconciler.refresh_baseline()
        path.write_text(
            fixture_yaml.rstrip() + "\n  extra:\n    image: busybox\n", encoding="utf-8"
        )
        assert await reconciler.refresh_baseline()
        assert reconciler.baseline is not None and "extra" in reconciler.baseline.service_names
    finally:
        await reconciler.stop()


async def test_adopt_deletion_opens_pr_and_clears_when_service_disappears(
    monkeypatch: pytest.MonkeyPatch, fixture_yaml: str, cache_container: LiveContainer
) -> None:
    monkeypatch.setenv("ALLOW_MOCK_BASELINE", "false")
    monkeypatch.setenv("GITHUB_PAT", "ghp_test")
    monkeypatch.setenv("TARGET_REPO", "owner/infra")
    monkeypatch.setenv("TARGET_BRANCH", "main")
    monkeypatch.setenv("WARDEN_MODE", "enforce")
    settings = WardenSettings(_env_file=None)  # type: ignore[call-arg]
    watcher = FakeWatcher(settings)
    watcher.containers = [cache_container]  # web is MISSING
    github = FakeGitHub(fixture_yaml)
    reconciler, store = build(settings, watcher, github)
    await reconciler.start()
    try:
        with pytest.raises(DriftWardenError):
            await reconciler.adopt_deletion(cache_container.name)  # not missing
        result = await reconciler.adopt_deletion("web", note="retired")
        assert result.ok and result.action == "adopt_deletion" and result.pr_url
        assert github.pulls[0]["service"] == "web" and "retired" in github.pulls[0]["body"]
        assert "web:" not in (github.mutated or "") and "cache:" in (github.mutated or "")
        assert [p.kind for p in reconciler.pending] == ["remove"]

        await reconciler.reconcile("sweep")  # enforcement must not recreate a buffered service
        assert watcher.recreated == []
        web = reconciler.projector.snapshot.find("web")
        assert web is not None and web.drift_status is DriftStatus.PENDING_ADOPTION

        github.content = github.mutated or ""
        github.sha = "sha-2"
        assert await reconciler.refresh_baseline(force=True)
        await asyncio.sleep(0.1)
        assert reconciler.pending == []
        assert reconciler.projector.snapshot.find("web") is None
        assert store.pending_history()[0][1] == "baseline_updated"
    finally:
        await reconciler.stop()
