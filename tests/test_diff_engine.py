"""Classification rules and structured diffs."""

from datetime import datetime, timedelta, timezone

from shared.constants import DriftStatus
from shared.models import DriftRecord, PendingAdoption
from tests.conftest import PROJECT, compose_labels, make_container
from warden.core.baseline_parser import Baseline
from warden.core.docker_watcher import LiveContainer, MountInfo
from warden.engine.diff_engine import (
    EvaluationContext,
    WhitelistIndex,
    compare,
    evaluate,
    map_to_service,
    normalise_image,
    observed_service,
)


def context(baseline: Baseline, **overrides: object) -> EvaluationContext:
    ctx = EvaluationContext(
        baseline=baseline,
        hostname="Host-Prod-01",
        excluded_prefixes=("warden-", "driftwarden", "k8s_"),
    )
    for key, value in overrides.items():
        setattr(ctx, key, value)
    return ctx


def statuses(records: list) -> dict[str, str]:  # type: ignore[type-arg]
    return {r.ref: r.drift_status.value for r in records}


def test_matched_services(
    baseline: Baseline, web_container: LiveContainer, cache_container: LiveContainer
) -> None:
    result = evaluate([web_container, cache_container], context(baseline))
    assert statuses(result.records) == {
        f"{PROJECT}-web-1": "matched",
        f"{PROJECT}-cache-1": "matched",
    }
    assert all(r.actions == [] for r in result.records)
    assert all(not r.changed_fields for r in result.records)


def test_demo_scenario(
    baseline: Baseline, drifted_cache: LiveContainer, rogue_container: LiveContainer
) -> None:
    result = evaluate([drifted_cache, rogue_container], context(baseline))
    by_ref = {r.ref: r for r in result.records}
    assert by_ref["web"].drift_status is DriftStatus.MISSING
    assert by_ref["web"].actions == ["revert", "adopt_deletion"]
    assert by_ref["web"].status == "absent"
    cache = by_ref[f"{PROJECT}-cache-1"]
    assert cache.drift_status is DriftStatus.DRIFTED
    assert cache.changed_fields == ["image", "ports", "networks"]
    assert cache.actions == ["revert", "adopt"]
    assert "redis:latest" in (cache.observed_yaml or "")
    rogue = by_ref["rogue-db"]
    assert rogue.drift_status is DriftStatus.ROGUE
    assert rogue.actions == ["adopt", "kill", "whitelist"]
    assert rogue.expected_yaml is None
    assert "SHADOW IT" in rogue.message and "Host-Prod-01" in rogue.message


def test_hard_exclusions_and_whitelist(baseline: Baseline, web_container: LiveContainer) -> None:
    me = make_container(id="e" * 64, name="driftwarden")
    ignored = make_container(id="f" * 64, name="sidecar", labels={"warden.ignore": "true"})
    other = make_container(id="1" * 64, name="other-api-1", labels=compose_labels("api", "other"))
    by_id = make_container(id="2" * 64, name="tool")
    self_container = make_container(id="3" * 64, name="whatever")
    ctx = context(
        baseline,
        whitelist=WhitelistIndex(containers={"2" * 64}, projects={"other"}),
        self_container_id="3" * 64,
    )
    result = evaluate([web_container, me, ignored, other, by_id, self_container], ctx)
    assert statuses(result.records) == {f"{PROJECT}-web-1": "matched", "cache": "missing"}
    assert sorted(result.exempt) == [
        "driftwarden (name prefix driftwarden)",
        "other-api-1 (whitelisted project other)",
        "sidecar (label warden.ignore=true)",
        "tool (whitelisted container)",
        "whatever (driftwarden itself)",
    ]


def test_zero_trust_labels(baseline: Baseline) -> None:
    spoofed = make_container(id="4" * 64, name="ghost", labels=compose_labels("ghost"))
    foreign = make_container(id="5" * 64, name="other-db-1", labels=compose_labels("db", "other"))
    result = evaluate([spoofed, foreign], context(baseline))
    by_ref = {r.ref: r for r in result.records}
    assert by_ref["ghost"].drift_status is DriftStatus.ROGUE
    assert "not declared" in by_ref["ghost"].message
    assert by_ref["other-db-1"].drift_status is DriftStatus.ROGUE
    assert "untracked Compose project 'other'" in by_ref["other-db-1"].message
    assert "whitelist_project" in by_ref["other-db-1"].actions


def test_name_pattern_fallback_and_container_name(baseline: Baseline) -> None:
    unlabelled = make_container(
        name=f"{PROJECT}_web_1", labels={"com.docker.compose.project": PROJECT}
    )
    assert map_to_service(unlabelled, baseline) == "web"
    assert (
        map_to_service(
            make_container(name="unrelated", labels={"com.docker.compose.project": PROJECT}),
            baseline,
        )
        is None
    )
    labelled_wrong = make_container(name=f"{PROJECT}-web-1", labels=compose_labels("nope"))
    assert map_to_service(labelled_wrong, baseline) is None
    baseline.services["web"].container_name = "pinned"
    assert (
        map_to_service(
            make_container(name="pinned", labels={"com.docker.compose.project": PROJECT}), baseline
        )
        == "web"
    )


def test_stopped_is_missing_and_paused_is_drifted(
    baseline: Baseline, web_container: LiveContainer, cache_container: LiveContainer
) -> None:
    web_container.state = "exited"
    cache_container.state = "paused"
    result = evaluate([web_container, cache_container], context(baseline))
    by_ref = {r.ref: r for r in result.records}
    web = by_ref[f"{PROJECT}-web-1"]
    assert web.drift_status is DriftStatus.MISSING
    assert web.actions == ["revert", "adopt_deletion"]
    cache = by_ref[f"{PROJECT}-cache-1"]
    assert cache.drift_status is DriftStatus.DRIFTED and cache.changed_fields == ["state"]


def test_duplicate_replica_is_rogue(
    baseline: Baseline, web_container: LiveContainer, cache_container: LiveContainer
) -> None:
    twin = make_container(
        id="9" * 64,
        name=f"{PROJECT}-web-2",
        image="nginx:alpine",
        labels={**compose_labels("web"), "warden.managed": "true", "warden.repo": "my-cs-project"},
        ports=["8080:80/tcp"],
        created="2027",
    )
    result = evaluate([web_container, twin, cache_container], context(baseline))
    by_ref = {r.ref: r for r in result.records}
    assert {by_ref[f"{PROJECT}-web-1"].drift_status, by_ref[f"{PROJECT}-web-2"].drift_status} == {
        DriftStatus.MATCHED,
        DriftStatus.ROGUE,
    }
    rogue = next(r for r in result.records if r.drift_status is DriftStatus.ROGUE)
    assert "duplicate replica" in rogue.message


def test_pending_adoption_is_exempt(
    baseline: Baseline, rogue_container: LiveContainer, drifted_cache: LiveContainer
) -> None:
    now = datetime.now(timezone.utc)
    pending = {
        rogue_container.id: PendingAdoption(
            id=1,
            container_id=rogue_container.id,
            container_name="rogue-db",
            service_name="rogue-db",
            pr_number=7,
            pr_url="https://x/7",
            branch="b",
            created_at=now,
            expires_at=now + timedelta(minutes=3),
        ),
        drifted_cache.id: PendingAdoption(
            id=2,
            container_id=drifted_cache.id,
            container_name=drifted_cache.name,
            service_name="cache",
            pr_number=8,
            pr_url="https://x/8",
            branch="b",
            created_at=now,
            expires_at=now + timedelta(minutes=3),
        ),
    }
    result = evaluate([rogue_container, drifted_cache], context(baseline, pending=pending))
    for record in result.records:
        if record.ref != "web":
            assert record.drift_status is DriftStatus.PENDING_ADOPTION
            assert record.actions == []
            assert record.pending_adoption is not None and "PR #" in record.message


def test_no_baseline_yields_no_records(web_container: LiveContainer) -> None:
    ctx = EvaluationContext(baseline=None, hostname="h", excluded_prefixes=("driftwarden",))
    result = evaluate([web_container, make_container(name="driftwarden-1")], ctx)
    assert result.records == []
    assert result.exempt == ["driftwarden-1 (name prefix driftwarden)"]


def test_compare_rules(baseline: Baseline) -> None:
    service = baseline.services["cache"]
    service.environment = {"REDIS_PASSWORD": "secret"}
    service.volumes = ["data:/data:rw", "./conf:/etc/redis:ro"]
    container = make_container(
        name="c",
        image="docker.io/library/redis:alpine",
        labels={
            **compose_labels("cache"),
            "warden.managed": "true",
            "warden.repo": "my-cs-project",
            "extra": "ignored",
        },
        ports=["6379:6379/tcp"],
        environment={"REDIS_PASSWORD": "secret", "PATH": "/bin"},
        mounts=[
            MountInfo("volume", f"{PROJECT}_data", "/data", False),
            MountInfo("bind", "/abs/conf", "/etc/redis", True),
            MountInfo("volume", "0" * 64, "/anon", False),
        ],
    )
    diffs = {d.field: d for d in compare(service, container, PROJECT)}
    assert not any(d.changed for d in diffs.values()), {
        k: (d.expected, d.observed) for k, d in diffs.items() if d.changed
    }
    assert diffs["image"].observed == "redis:alpine"

    container.environment["REDIS_PASSWORD"] = "changed"
    container.mounts[1] = MountInfo("bind", "/abs/conf", "/etc/redis", False)
    container.restart = "always"
    diffs = {d.field: d for d in compare(service, container, PROJECT)}
    assert (
        diffs["environment"].changed and diffs["environment"].observed == "REDIS_PASSWORD=changed"
    )
    assert diffs["volumes"].changed and "/abs/conf:/etc/redis:rw" in (
        diffs["volumes"].observed or ""
    )
    assert diffs["restart"].changed and diffs["restart"].observed == "always"


def test_ports_without_host_port_match_any_host_port(baseline: Baseline) -> None:
    service = baseline.services["web"]
    service.ports = ["80/tcp"]
    container = make_container(
        name="w",
        image="nginx:alpine",
        ports=["32768:80/tcp"],
        labels={"warden.managed": "true", "warden.repo": "my-cs-project"},
    )
    assert not next(d for d in compare(service, container, PROJECT) if d.field == "ports").changed
    container.ports = ["32768:80/tcp", "9:9/tcp"]
    assert next(d for d in compare(service, container, PROJECT) if d.field == "ports").changed


def test_normalise_image() -> None:
    assert normalise_image("redis") == "redis:latest"
    assert normalise_image("docker.io/library/redis:7") == "redis:7"
    assert normalise_image("ghcr.io/org/app") == "ghcr.io/org/app:latest"
    assert normalise_image("ghcr.io/org/app@sha256:abc") == "ghcr.io/org/app@sha256:abc"
    assert normalise_image("sha256:abc") == "sha256:abc"


def test_observed_service_strips_image_defaults(
    baseline: Baseline, rogue_container: LiveContainer
) -> None:
    ctx = context(
        baseline,
        image_environment=lambda _: {"PATH": "/usr/bin"},
        image_labels=lambda _: {"maintainer": "x"},
    )
    rogue_container.labels["maintainer"] = "x"
    rogue_container.labels["team"] = "db"
    observed = observed_service(rogue_container, "rogue-db", PROJECT, ctx)
    assert observed.environment == {"MARIADB_ROOT_PASSWORD": "${MARIADB_ROOT_PASSWORD}"}
    assert observed.labels == {"team": "db"}
    assert observed.volumes == []  # anonymous image volumes are never reconstructed
    assert observed.networks == []  # the implicit bridge network is never reconstructed
    assert observed.container_name is None
    assert observed.restart is None


def test_pending_deletion_marks_missing_service(baseline: Baseline) -> None:
    now = datetime.now(timezone.utc)
    removal = PendingAdoption(
        id=3,
        kind="remove",
        container_id="service:web",
        container_name="web",
        service_name="web",
        pr_number=9,
        pr_url="https://x/9",
        branch="b",
        created_at=now,
        expires_at=now + timedelta(minutes=3),
    )
    result = evaluate([], context(baseline, pending={"service:web": removal}))
    by_ref = {r.ref: r for r in result.records}
    assert by_ref["web"].drift_status is DriftStatus.PENDING_ADOPTION
    assert by_ref["web"].actions == [] and by_ref["web"].pending_adoption == removal
    assert by_ref["cache"].drift_status is DriftStatus.MISSING


def test_observed_yaml_keeps_sibling_keys_at_service_level(
    baseline: Baseline, rogue_container: LiveContainer
) -> None:
    rogue_container.labels["team"] = "db"
    rogue_container.restart = "always"
    result = evaluate([rogue_container], context(baseline))
    record = next(r for r in result.records if r.ref == "rogue-db")
    text = record.observed_yaml or ""
    lines = text.splitlines()
    assert lines[0] == "rogue-db:"
    keys = [
        ln.split(":")[0] for ln in lines[1:] if ln.startswith("  ") and not ln.startswith("   ")
    ]
    assert keys == ["  image", "  ports", "  environment", "  labels", "  restart"]
    assert "container_name" not in text
    assert "rogue" not in text.split("MARIADB_ROOT_PASSWORD")[1].splitlines()[0]
    assert "/var/lib/mysql" not in text and "bridge" not in text


def test_display_name_falls_back_to_baseline_identity(baseline: Baseline) -> None:
    baseline.services["cache"].container_name = "pinned-cache"
    result = evaluate([], context(baseline))
    by_ref = {r.ref: r for r in result.records}
    assert by_ref["web"].display_name == "web"
    assert by_ref["cache"].display_name == "pinned-cache"
    assert by_ref["web"].container_name is None


def test_awaiting_restart_is_strictly_post_adoption(
    baseline: Baseline, rogue_container: LiveContainer
) -> None:
    from shared.models import ParsedService

    # The merged baseline now declares a service named after the adopted container.
    baseline.services["rogue-db"] = ParsedService(
        service_name="rogue-db",
        image="mariadb:latest",
        ports=["3306:3306/tcp"],
        environment={"MARIADB_ROOT_PASSWORD": "rogue"},
    )
    adopted = {rogue_container.id: "rogue-db"}

    def service_row(records: list[DriftRecord]) -> DriftRecord:
        return next(r for r in records if r.service_name == "rogue-db")

    # Same name, declared service, but no adoption record: an unmanaged workload.
    result = evaluate([rogue_container], context(baseline))
    assert service_row(result.records).drift_status is DriftStatus.MISSING
    assert any(r.drift_status is DriftStatus.ROGUE for r in result.records)

    # The adopted instance with strict parity (only the bridge network differs).
    result = evaluate([rogue_container], context(baseline, adopted=adopted))
    record = service_row(result.records)
    assert record.drift_status is DriftStatus.AWAITING_RESTART
    assert record.container_id == rogue_container.id and record.actions == ["revert"]
    assert "Will join project network upon next restart" in record.message

    # Recreated by hand with a different host port: SHADOW IT, even with the old id.
    altered = make_container(
        id=rogue_container.id,
        name="rogue-db",
        image="mariadb:latest",
        ports=["3307:3306/tcp"],
        networks=["bridge"],
        environment={"MARIADB_ROOT_PASSWORD": "rogue", "PATH": "/usr/bin"},
    )
    result = evaluate([altered], context(baseline, adopted=adopted))
    assert service_row(result.records).drift_status is DriftStatus.MISSING
    rogue = next(r for r in result.records if r.container_id == altered.id)
    assert rogue.drift_status is DriftStatus.ROGUE
    assert "differs on ports" in rogue.message

    # A new container instance with the same name never inherits the adoption.
    newcomer = make_container(
        id="n" * 64,
        name="rogue-db",
        image="mariadb:latest",
        ports=["3306:3306/tcp"],
        networks=["bridge"],
        environment={"MARIADB_ROOT_PASSWORD": "rogue"},
    )
    result = evaluate([newcomer], context(baseline, adopted=adopted))
    rogue = next(r for r in result.records if r.container_id == newcomer.id)
    assert rogue.drift_status is DriftStatus.ROGUE

    # Once Compose recreated it with labels, the unlabelled leftover is plain rogue again.
    labelled = make_container(
        id="7" * 64,
        name=f"{PROJECT}-rogue-db-1",
        image="mariadb:latest",
        labels=compose_labels("rogue-db"),
        ports=["3306:3306/tcp"],
        environment={"MARIADB_ROOT_PASSWORD": "rogue"},
    )
    result = evaluate([rogue_container, labelled], context(baseline, adopted=adopted))
    by_ref = {r.ref: r for r in result.records}
    assert by_ref[f"{PROJECT}-rogue-db-1"].drift_status is DriftStatus.MATCHED
    assert by_ref["rogue-db"].drift_status is DriftStatus.ROGUE

    # A stopped adopted instance does not count as awaiting restart.
    rogue_container.state = "exited"
    result = evaluate([rogue_container], context(baseline, adopted=adopted))
    assert service_row(result.records).drift_status is DriftStatus.MISSING
    leftover = next(r for r in result.records if r.container_id == rogue_container.id)
    assert leftover.drift_status is DriftStatus.ROGUE


def test_network_only_drift_after_adoption_is_awaiting_restart(
    baseline: Baseline, cache_container: LiveContainer
) -> None:
    from warden.engine.diff_engine import AWAITING_RESTART_MESSAGE

    cache_container.networks = ["bridge"]  # adopted and merged, never recreated by Compose
    adopted = {cache_container.id: "cache"}

    # Without an adoption record the network difference is ordinary drift.
    result = evaluate([cache_container], context(baseline))
    record = next(r for r in result.records if r.ref == cache_container.name)
    assert record.drift_status is DriftStatus.DRIFTED

    result = evaluate([cache_container], context(baseline, adopted=adopted))
    record = next(r for r in result.records if r.ref == cache_container.name)
    assert record.drift_status is DriftStatus.AWAITING_RESTART
    assert record.message == AWAITING_RESTART_MESSAGE
    assert record.actions == ["revert"] and record.changed_fields == ["networks"]

    # Anything else that differs is real drift, even with the bridge network.
    cache_container.ports = ["6378:6378/tcp"]
    result = evaluate([cache_container], context(baseline, adopted=adopted))
    record = next(r for r in result.records if r.ref == cache_container.name)
    assert record.drift_status is DriftStatus.DRIFTED

    # A custom network that is not the legacy bridge is drift too.
    cache_container.ports = ["6379:6379/tcp"]
    cache_container.networks = ["some-other-net"]
    result = evaluate([cache_container], context(baseline, adopted=adopted))
    record = next(r for r in result.records if r.ref == cache_container.name)
    assert record.drift_status is DriftStatus.DRIFTED


def test_unresolved_env_placeholders_are_not_drift(baseline: Baseline) -> None:
    service = baseline.services["cache"]
    service.environment = {"REDIS_PASSWORD": "", "REDIS_MODE": "cluster"}
    service.unresolved_env = {"REDIS_PASSWORD"}
    container = make_container(
        name="c",
        image="redis:alpine",
        labels={"warden.managed": "true", "warden.repo": "my-cs-project"},
        ports=["6379:6379/tcp"],
        environment={"REDIS_PASSWORD": "anything-at-all", "REDIS_MODE": "cluster"},
    )
    env = next(d for d in compare(service, container, PROJECT) if d.field == "environment")
    assert not env.changed and "REDIS_PASSWORD=${REDIS_PASSWORD}" in (env.expected or "")
    container.environment["REDIS_MODE"] = "standalone"
    env = next(d for d in compare(service, container, PROJECT) if d.field == "environment")
    assert env.changed
