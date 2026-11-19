"""Reversing Enforce and Adopt actions from the audit log."""

import asyncio
from collections.abc import Iterator

import pytest

from shared.constants import AuditAction, DriftStatus
from shared.exceptions import DriftWardenError
from shared.models import BatchRequest
from tests.conftest import PROJECT
from tests.fakes import FakeGitHub, FakeWatcher, container_from_service
from warden.config import WardenSettings
from warden.core.docker_watcher import LiveContainer, port_bindings_to_docker, spec_from_attrs
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


def github_settings(monkeypatch: pytest.MonkeyPatch) -> WardenSettings:
    monkeypatch.setenv("ALLOW_MOCK_BASELINE", "false")
    monkeypatch.setenv("GITHUB_PAT", "ghp_test")
    monkeypatch.setenv("TARGET_REPO", "owner/infra")
    monkeypatch.setenv("TARGET_BRANCH", "main")
    return WardenSettings(_env_file=None)  # type: ignore[call-arg]


def last_of(store: Store, action: AuditAction) -> int:
    return next(row.id for row in store.list_audit() if row.action is action)


# ------------------------------------------------------------------ store


def test_audit_reversal_state(settings: WardenSettings) -> None:
    _, store = build(settings, FakeWatcher(settings))
    plain = store.record_audit(AuditAction.ENFORCE_KILL, "old-row")  # no snapshot
    kill = store.record_audit(
        AuditAction.ENFORCE_KILL, "rogue", snapshot={"type": "enforce_remove", "previous": {}}
    )
    other = store.record_audit(AuditAction.ROGUE_DETECTED, "rogue")
    entries = {e.id: e for e in store.list_audit()}
    assert not entries[plain].reversible and "before reversal" in (
        entries[plain].not_reversible_reason or ""
    )
    assert entries[kill].reversible and entries[kill].reversal_warning
    assert not entries[other].reversible

    failed = store.record_audit(AuditAction.REVERSAL_FAILED, "rogue", "x", reverses_id=kill)
    assert store.list_audit()[0].reverses_id == kill
    assert {e.id: e for e in store.list_audit()}[kill].reversible  # a failure can be retried
    done = store.record_audit(AuditAction.REVERSAL_COMPLETED, "rogue", "y", reverses_id=kill)
    entry, snapshot = store.get_audit(kill) or (None, None)
    assert entry is not None and entry.reversed_by_id == done and not entry.reversible
    assert snapshot == {"type": "enforce_remove", "previous": {}}
    assert failed != done
    assert store.get_audit(9999) is None
    assert "snapshot" not in store.list_audit()[0].model_dump()


def test_adoption_entries_for_pr(settings: WardenSettings) -> None:
    _, store = build(settings, FakeWatcher(settings))
    a = store.record_audit(AuditAction.ADOPT_PR_OPENED, "a", snapshot={"pr_number": 7})
    b = store.record_audit(AuditAction.ADOPT_PR_OPENED, "b", snapshot={"pr_number": 7})
    store.record_audit(AuditAction.ADOPT_PR_OPENED, "c", snapshot={"pr_number": 8})
    assert [e.id for e in store.adoption_entries_for_pr(7)] == [a, b]
    store.record_audit(AuditAction.REVERSAL_COMPLETED, "a", "z", reverses_id=a)
    assert [e.id for e in store.adoption_entries_for_pr(7)] == [b]


# --------------------------------------------------------------- enforce


async def test_reverse_removal_recreates_the_rogue_container(
    settings: WardenSettings, rogue_container: LiveContainer
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [rogue_container]
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        await reconciler.kill("rogue-db")
        assert all(c.name != "rogue-db" for c in watcher.containers)
        kill_id = last_of(store, AuditAction.ENFORCE_KILL)

        result = await reconciler.reverse(kill_id)
        assert result.ok and "recreated rogue-db" in result.message
        assert "filesystem outside volumes" in result.message
        restored = next(c for c in watcher.containers if c.name == "rogue-db")
        assert restored.image == rogue_container.image
        assert restored.environment["MARIADB_ROOT_PASSWORD"] == "rogue"
        assert restored.ports == rogue_container.ports
        reversal = store.list_audit()[0]
        assert reversal.action is AuditAction.REVERSAL_COMPLETED
        assert reversal.reverses_id == kill_id
        with pytest.raises(DriftWardenError, match="cannot be reversed again"):
            await reconciler.reverse(kill_id)
        await asyncio.sleep(0.05)
        record = reconciler.projector.snapshot.find("rogue-db")
        assert record is not None and record.drift_status is DriftStatus.ROGUE
    finally:
        await reconciler.stop()


async def test_reverse_recreate_brings_back_the_drifted_container(
    settings: WardenSettings, drifted_cache: LiveContainer, web_container: LiveContainer
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container, drifted_cache]
    watcher.on_recreate = container_from_service
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        await reconciler.revert(drifted_cache.name)
        recreated = next(c for c in watcher.containers if c.name == drifted_cache.name)
        assert recreated.image == "redis:alpine" and recreated.id != drifted_cache.id
        revert_id = last_of(store, AuditAction.REVERT_COMPLETED)

        result = await reconciler.reverse(revert_id)
        assert result.ok and "removed the container Enforce created" in result.message
        back = next(c for c in watcher.containers if c.name == drifted_cache.name)
        assert back.image == drifted_cache.image and back.ports == drifted_cache.ports
        assert recreated.id in watcher.removed
        await asyncio.sleep(0.05)
        record = reconciler.projector.snapshot.find(drifted_cache.name)
        assert record is not None and record.drift_status is DriftStatus.DRIFTED
    finally:
        await reconciler.stop()


async def test_reverse_of_missing_service_enforce_just_removes_it(
    settings: WardenSettings, web_container: LiveContainer
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [web_container]  # cache is MISSING
    watcher.on_recreate = container_from_service
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        await reconciler.revert("cache")
        assert any(c.name == f"{PROJECT}-cache-1" for c in watcher.containers)
        result = await reconciler.reverse(last_of(store, AuditAction.REVERT_COMPLETED))
        assert "nothing ran before Enforce" in result.message
        assert all(c.name != f"{PROJECT}-cache-1" for c in watcher.containers)
    finally:
        await reconciler.stop()


async def test_reverse_fails_cleanly_when_the_image_is_gone(
    settings: WardenSettings, rogue_container: LiveContainer
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [rogue_container]
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        await reconciler.kill("rogue-db")
        kill_id = last_of(store, AuditAction.ENFORCE_KILL)
        watcher.missing_images.add(rogue_container.image)
        with pytest.raises(DriftWardenError, match="no longer available"):
            await reconciler.reverse(kill_id)
        assert store.list_audit()[0].action is AuditAction.REVERSAL_FAILED
        entry, _ = store.get_audit(kill_id) or (None, None)
        assert entry is not None and entry.reversible  # can be retried once the image is back
        watcher.missing_images.clear()
        assert (await reconciler.reverse(kill_id)).ok
    finally:
        await reconciler.stop()


async def test_reverse_refuses_name_collision_and_enforce_mode(
    monkeypatch: pytest.MonkeyPatch, settings: WardenSettings, rogue_container: LiveContainer
) -> None:
    watcher = FakeWatcher(settings)
    watcher.containers = [rogue_container]
    reconciler, store = build(settings, watcher)
    await reconciler.start()
    try:
        await reconciler.kill("rogue-db")
        kill_id = last_of(store, AuditAction.ENFORCE_KILL)
        watcher.containers.append(rogue_container)  # someone ran it again by hand
        with pytest.raises(DriftWardenError, match="already exists"):
            await reconciler.reverse(kill_id)
        watcher.containers = []
        reconciler.settings.warden_mode = "enforce"
        with pytest.raises(DriftWardenError, match="observe"):
            await reconciler.reverse(kill_id)
        reconciler.settings.warden_mode = "observe"
        with pytest.raises(DriftWardenError, match="cannot be reversed"):
            await reconciler.reverse(last_of(store, AuditAction.ENGINE_STARTED))
        with pytest.raises(DriftWardenError, match="No audit entry"):
            await reconciler.reverse(123456)
    finally:
        await reconciler.stop()


# ----------------------------------------------------------------- adopt


async def test_reverse_adoption_closes_pr_deletes_branch_and_unbuffers(
    monkeypatch: pytest.MonkeyPatch, fixture_yaml: str, rogue_container: LiveContainer
) -> None:
    settings = github_settings(monkeypatch)
    watcher = FakeWatcher(settings)
    watcher.containers = [rogue_container]
    github = FakeGitHub(fixture_yaml)
    reconciler, store = build(settings, watcher, github)
    await reconciler.start()
    try:
        await reconciler.adopt("rogue-db")
        assert len(reconciler.pending) == 1
        adopt_id = last_of(store, AuditAction.ADOPT_PR_OPENED)
        result = await reconciler.reverse(adopt_id)
        assert result.ok and "closed PR #1" in result.message
        assert github.closed and "Adoption reversed by operator" in github.closed[0][1]
        assert github.deleted_branches == ["warden/adopt-x"]
        assert reconciler.pending == []
        await asyncio.sleep(0.05)
        record = reconciler.projector.snapshot.find("rogue-db")
        assert record is not None and record.drift_status is DriftStatus.ROGUE
        assert store.pending_history()[0][1] == "reversed"
    finally:
        await reconciler.stop()


async def test_reverse_batch_adoption_covers_every_entry_and_refuses_merged(
    monkeypatch: pytest.MonkeyPatch,
    fixture_yaml: str,
    rogue_container: LiveContainer,
    drifted_cache: LiveContainer,
) -> None:
    settings = github_settings(monkeypatch)
    watcher = FakeWatcher(settings)
    watcher.containers = [rogue_container, drifted_cache]
    github = FakeGitHub(fixture_yaml)
    reconciler, store = build(settings, watcher, github)
    await reconciler.start()
    try:
        await reconciler.batch(BatchRequest(operation="adopt", scope="all"))
        opened = [r for r in store.list_audit() if r.action is AuditAction.ADOPT_PR_OPENED]
        assert len(opened) == 3  # rogue, drifted cache and the missing web service
        result = await reconciler.reverse(opened[-1].id)
        assert "2 other entries on the same PR" in result.message
        assert len(github.closed) == 1 and "covers 3 workloads" in github.closed[0][1]
        reversals = [r for r in store.list_audit() if r.action is AuditAction.REVERSAL_COMPLETED]
        assert {r.reverses_id for r in reversals} == {r.id for r in opened}
        assert reconciler.pending == []

        # A merged PR cannot be reversed here; every entry on it is marked refused.
        await reconciler.reconcile("test")
        await reconciler.adopt("rogue-db")
        adopt_id = last_of(store, AuditAction.ADOPT_PR_OPENED)
        github.pr_states[2] = "merged"
        with pytest.raises(DriftWardenError, match="already merged"):
            await reconciler.reverse(adopt_id)
        assert store.list_audit()[0].action is AuditAction.REVERSAL_REFUSED
        entry, _ = store.get_audit(adopt_id) or (None, None)
        assert entry is not None and not entry.reversible
        assert "no longer" in (entry.not_reversible_reason or "")
    finally:
        await reconciler.stop()


async def test_reverse_adoption_with_pr_already_closed(
    monkeypatch: pytest.MonkeyPatch, fixture_yaml: str, rogue_container: LiveContainer
) -> None:
    settings = github_settings(monkeypatch)
    watcher = FakeWatcher(settings)
    watcher.containers = [rogue_container]
    github = FakeGitHub(fixture_yaml)
    reconciler, store = build(settings, watcher, github)
    await reconciler.start()
    try:
        await reconciler.adopt("rogue-db")
        adopt_id = last_of(store, AuditAction.ADOPT_PR_OPENED)
        github.pr_states[1] = "closed"
        result = await reconciler.reverse(adopt_id)
        assert "already closed" in result.message and github.closed == []
        assert reconciler.pending == []
    finally:
        await reconciler.stop()


# --------------------------------------------------------------- helpers


def test_spec_from_attrs_and_port_bindings() -> None:
    attrs = {
        "Id": "abc",
        "Name": "/rogue-web",
        "Image": "sha256:1",
        "Config": {
            "Image": "nginx:alpine",
            "Env": ["A=1"],
            "Labels": {"x": "y"},
            "Cmd": ["nginx", "-g", "daemon off;"],
            "Entrypoint": "/docker-entrypoint.sh",
        },
        "HostConfig": {
            "PortBindings": {
                "80/tcp": [
                    {"HostIp": "", "HostPort": "8088"},
                    {"HostIp": "::", "HostPort": "8088"},
                ],
                "53/udp": [{"HostIp": "127.0.0.1", "HostPort": "53"}],
                "9000/tcp": [],
            },
            "RestartPolicy": {"Name": "always"},
        },
        "Mounts": [{"Type": "volume", "Name": "data", "Destination": "/data", "RW": True}],
        "NetworkSettings": {"Networks": {"bridge": {}}},
    }
    spec = spec_from_attrs(attrs)
    assert spec["name"] == "rogue-web" and spec["image"] == "nginx:alpine"
    assert spec["entrypoint"] == ["/docker-entrypoint.sh"] and spec["cmd"][0] == "nginx"
    assert spec["mounts"][0] == {
        "type": "volume",
        "name": "data",
        "source": "",
        "target": "/data",
        "read_only": False,
    }
    assert spec["restart_policy"] == {"Name": "always", "MaximumRetryCount": 0}
    assert port_bindings_to_docker(spec["port_bindings"]) == {
        "80/tcp": 8088,
        "53/udp": ("127.0.0.1", 53),
        "9000/tcp": None,
    }
