"""Pure comparison logic between the Git baseline and live containers.

Nothing in this module touches Docker, GitHub or the database; it receives
already-inspected containers and a parsed baseline and returns structured
``DriftRecord`` rows. That keeps the classification rules unit-testable.
"""

import re
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import datetime, timezone

from loguru import logger

from shared.constants import (
    COMPOSE_LABEL_PREFIX,
    DIFF_FIELDS,
    WARDEN_IGNORE_LABEL,
    DriftStatus,
)
from shared.models import DriftRecord, FieldDiff, ParsedService, PendingAdoption
from warden.automation.yaml_mutator import sanitise_service_name
from warden.core.baseline_parser import Baseline, render_service_yaml
from warden.core.docker_watcher import LiveContainer

MISSING_STATES: frozenset[str] = frozenset({"exited", "created", "removing"})
"""Container states that count as the service not running (MISSING)."""

DRIFTED_STATES: frozenset[str] = frozenset({"paused", "restarting", "dead"})
"""Container states that count as a wrong runtime state (DRIFTED)."""

INTERNAL_LABEL_PREFIXES: tuple[str, ...] = (
    COMPOSE_LABEL_PREFIX,
    "warden.managed-by",
    "warden.action-id",
)
"""Labels injected by Compose or DriftWarden itself, never diffed or adopted."""

UNSET = "<unset>"


@dataclass(slots=True)
class WhitelistIndex:
    """In-memory view of the ``whitelisted_entities`` table.

    Attributes:
        containers: Whitelisted container ids and names.
        projects: Whitelisted Compose project names.
    """

    containers: set[str] = field(default_factory=set)
    projects: set[str] = field(default_factory=set)

    def matches(self, container: LiveContainer) -> str | None:
        """Return why a container is whitelisted, or ``None``."""
        if container.id in self.containers or container.name in self.containers:
            return "whitelisted container"
        if any(container.id.startswith(entry) for entry in self.containers if len(entry) >= 12):
            return "whitelisted container"
        project = container.compose_project
        if project and project in self.projects:
            return f"whitelisted project {project}"
        return None


@dataclass(slots=True)
class EvaluationContext:
    """Inputs the evaluator needs besides the containers themselves.

    Attributes:
        baseline: Parsed baseline, or ``None`` while unavailable.
        hostname: Friendly host name for messages.
        excluded_prefixes: Container name prefixes that are never evaluated.
        self_container_id: DriftWarden's own container id, if containerised.
        whitelist: Operator exemptions.
        pending: Active adoption buffer entries keyed by container id.
        image_environment: Lookup of image-default environment by image id.
        image_labels: Lookup of image-default labels by image id.
    """

    baseline: Baseline | None
    hostname: str
    excluded_prefixes: tuple[str, ...] = ()
    self_container_id: str | None = None
    whitelist: WhitelistIndex = field(default_factory=WhitelistIndex)
    pending: dict[str, PendingAdoption] = field(default_factory=dict)
    image_environment: Callable[[str], dict[str, str]] = lambda _: {}
    image_labels: Callable[[str], dict[str, str]] = lambda _: {}


@dataclass(slots=True)
class Evaluation:
    """Result of one evaluation pass.

    Attributes:
        records: Dashboard rows, managed services first.
        exempt: Names of containers skipped with the reason.
    """

    records: list[DriftRecord]
    exempt: list[str]


# --------------------------------------------------------------- normalisers


def normalise_image(reference: str) -> str:
    """Canonicalise an image reference for comparison.

    ``docker.io/library/redis`` and ``redis`` are the same image; a missing
    tag means ``latest``. Digest references are left untouched.

    Args:
        reference: Image reference as written or reported.

    Returns:
        ``repo:tag`` in canonical form.
    """
    ref = reference.strip()
    if ref.startswith("sha256:"):
        return ref
    for prefix in (
        "docker.io/library/",
        "docker.io/",
        "index.docker.io/library/",
        "index.docker.io/",
    ):
        if ref.startswith(prefix):
            ref = ref[len(prefix) :]
            break
    if "@" in ref:
        return ref
    last = ref.rsplit("/", 1)[-1]
    if ":" not in last:
        ref = f"{ref}:latest"
    return ref


def exclusion_reason(container: LiveContainer, context: EvaluationContext) -> str | None:
    """Hard exclusions that apply before any baseline logic.

    Args:
        container: The inspected container.
        context: Evaluation inputs.

    Returns:
        A short reason, or ``None`` when the container must be evaluated.
    """
    if context.self_container_id and container.id == context.self_container_id:
        return "driftwarden itself"
    if container.labels.get(WARDEN_IGNORE_LABEL, "").lower() == "true":
        return f"label {WARDEN_IGNORE_LABEL}=true"
    for prefix in context.excluded_prefixes:
        if container.name.startswith(prefix):
            return f"name prefix {prefix}"
    return context.whitelist.matches(container)


def map_to_service(container: LiveContainer, baseline: Baseline) -> str | None:
    """Map a container to a baseline service: label first, name pattern second.

    Args:
        container: The inspected container.
        baseline: The parsed baseline.

    Returns:
        The service name, or ``None`` when no declared service matches.
    """
    label = container.compose_service
    if label is not None:
        return label if label in baseline.services else None
    project = re.escape(baseline.project_name)
    for service_name in baseline.services:
        pattern = rf"^{project}[-_]{re.escape(service_name)}[-_]\d+$"
        if re.match(pattern, container.name):
            return service_name
    for service in baseline.services.values():
        if service.container_name and service.container_name == container.name:
            return service.service_name
    return None


def custom_labels(container: LiveContainer, image_labels: dict[str, str]) -> dict[str, str]:
    """Labels an operator set explicitly, excluding Compose, Warden and image labels."""
    return {
        key: value
        for key, value in container.labels.items()
        if not key.startswith(INTERNAL_LABEL_PREFIXES) and image_labels.get(key) != value
    }


def custom_environment(container: LiveContainer, image_env: dict[str, str]) -> dict[str, str]:
    """Environment variables not inherited from the image."""
    return {k: v for k, v in container.environment.items() if image_env.get(k) != v}


def project_networks(container: LiveContainer, project: str | None) -> list[str]:
    """Live network names with the Compose project prefix stripped."""
    names: list[str] = []
    for network in container.networks:
        if project and network.startswith(f"{project}_"):
            names.append(network[len(project) + 1 :])
        else:
            names.append(network)
    return names


def observed_service(
    container: LiveContainer,
    service_name: str,
    project: str | None,
    context: EvaluationContext,
) -> ParsedService:
    """Render a live container as the Compose service it would be declared as.

    Args:
        container: The inspected container.
        service_name: Service key to use.
        project: Project used to strip network and volume prefixes.
        context: Provides image-default lookups.

    Returns:
        A canonical service record describing the live container.
    """
    image_env = context.image_environment(container.image_id)
    image_labels = context.image_labels(container.image_id)
    return ParsedService(
        service_name=service_name,
        image=container.image,
        ports=list(container.ports),
        labels=custom_labels(container, image_labels),
        environment=custom_environment(container, image_env),
        volumes=container.volumes(project),
        restart=None if container.restart == "no" else container.restart,
        networks=[n for n in project_networks(container, project) if n != "default"],
        container_name=container.name,
    )


# ------------------------------------------------------------------ compare


def _join(values: Iterable[str]) -> str:
    return ", ".join(values)


def _ports_match(expected: list[str], observed: list[str]) -> bool:
    """Compare port sets; an expected entry without host port matches any host port."""
    remaining = set(observed)
    for entry in expected:
        if entry in remaining:
            remaining.discard(entry)
            continue
        if ":" not in entry:
            candidates = [o for o in remaining if o.endswith(f":{entry}")]
            if candidates:
                remaining.discard(candidates[0])
                continue
        return False
    return not remaining


def _volumes_diff(expected: list[str], observed: list[str]) -> tuple[str, str, bool]:
    """Compare baseline-declared volumes by target."""
    by_target: dict[str, str] = {}
    for entry in observed:
        source, _, rest = entry.partition(":")
        target, _, mode = rest.rpartition(":")
        by_target[target] = f"{source}:{target}:{mode}"
    changed = False
    seen: list[str] = []
    for entry in expected:
        source, _, rest = entry.partition(":")
        target, _, mode = rest.rpartition(":")
        live = by_target.get(target)
        if live is None:
            seen.append(UNSET)
            changed = True
            continue
        live_source, _, live_rest = live.partition(":")
        _, _, live_mode = live_rest.rpartition(":")
        comparable_source = bool(source) and not source.startswith(".")
        if (comparable_source and live_source != source) or live_mode != mode:
            changed = True
        seen.append(live)
    return _join(expected), _join(seen), changed


def compare(service: ParsedService, container: LiveContainer, project: str) -> list[FieldDiff]:
    """Produce the structured per-field diff for a managed service.

    Only keys declared in the baseline are compared for ``environment``,
    ``labels`` and ``volumes``; Compose-injected labels never count.

    Args:
        service: Canonical baseline service.
        container: Inspected live container.
        project: Compose project namespace.

    Returns:
        One ``FieldDiff`` per field in ``DIFF_FIELDS`` plus a ``state`` row when
        the container is not running.
    """
    diffs: list[FieldDiff] = []
    for name in DIFF_FIELDS:
        if name == "image":
            expected = normalise_image(service.image or "")
            observed = normalise_image(container.image)
            diffs.append(
                FieldDiff(
                    field=name, expected=expected, observed=observed, changed=expected != observed
                )
            )
        elif name == "ports":
            diffs.append(
                FieldDiff(
                    field=name,
                    expected=_join(service.ports) or "(none)",
                    observed=_join(container.ports) or "(none)",
                    changed=not _ports_match(service.ports, container.ports),
                )
            )
        elif name == "environment":
            expected_env = service.environment
            observed_env = {k: container.environment.get(k, UNSET) for k in expected_env}
            diffs.append(
                FieldDiff(
                    field=name,
                    expected=_join(f"{k}={v}" for k, v in expected_env.items()) or "(none)",
                    observed=_join(f"{k}={v}" for k, v in observed_env.items()) or "(none)",
                    changed=any(observed_env[k] != v for k, v in expected_env.items()),
                )
            )
        elif name == "volumes":
            expected_text, observed_text, changed = _volumes_diff(
                service.volumes, container.volumes(project)
            )
            diffs.append(
                FieldDiff(
                    field=name,
                    expected=expected_text or "(none)",
                    observed=observed_text or "(none)",
                    changed=changed,
                )
            )
        elif name == "labels":
            expected_labels = service.labels
            observed_labels = {k: container.labels.get(k, UNSET) for k in expected_labels}
            diffs.append(
                FieldDiff(
                    field=name,
                    expected=_join(f"{k}={v}" for k, v in expected_labels.items()) or "(none)",
                    observed=_join(f"{k}={v}" for k, v in observed_labels.items()) or "(none)",
                    changed=any(observed_labels[k] != v for k, v in expected_labels.items()),
                )
            )
        elif name == "restart":
            expected = service.restart or "no"
            diffs.append(
                FieldDiff(
                    field=name,
                    expected=expected,
                    observed=container.restart,
                    changed=expected != container.restart,
                )
            )
        elif name == "networks":
            expected_networks = sorted(service.networks) or ["default"]
            observed_networks = sorted(project_networks(container, project))
            diffs.append(
                FieldDiff(
                    field=name,
                    expected=_join(expected_networks),
                    observed=_join(observed_networks) or "(none)",
                    changed=expected_networks != observed_networks,
                )
            )
    if container.state != "running":
        diffs.append(
            FieldDiff(field="state", expected="running", observed=container.state, changed=True)
        )
    return diffs


# ----------------------------------------------------------------- evaluate


def _pick_primary(candidates: list[LiveContainer]) -> tuple[LiveContainer, list[LiveContainer]]:
    """Choose the evaluated instance when several containers claim one service."""
    ordered = sorted(candidates, key=lambda c: (c.state != "running", c.created), reverse=False)
    running = [c for c in ordered if c.state == "running"]
    primary = running[-1] if running else ordered[-1]
    return primary, [c for c in candidates if c is not primary]


def _rogue_record(
    container: LiveContainer, context: EvaluationContext, now: datetime, reason: str
) -> DriftRecord:
    project = container.compose_project
    name = sanitise_service_name(
        container.name, context.baseline.project_name if context.baseline else None
    )
    observed = observed_service(container, name, project, context)
    actions = ["adopt", "kill", "whitelist"]
    if project:
        actions.append("whitelist_project")
    return DriftRecord(
        ref=container.name,
        drift_status=DriftStatus.ROGUE,
        service_name=None,
        project=project,
        container_id=container.id,
        container_name=container.name,
        image=container.image,
        status=container.state,
        diffs=[],
        expected_yaml=None,
        observed_yaml=render_service_yaml(observed),
        message=(
            f"SHADOW IT: rogue process '{container.name}' is running on {context.hostname} "
            f"but was not found in the Git repository ({reason})."
        ),
        actions=actions,
        detected_at=now,
    )


def _managed_record(
    service: ParsedService,
    container: LiveContainer | None,
    context: EvaluationContext,
    now: datetime,
) -> DriftRecord:
    assert context.baseline is not None
    project = context.baseline.project_name
    expected_yaml = render_service_yaml(service)
    if container is None:
        return DriftRecord(
            ref=service.service_name,
            drift_status=DriftStatus.MISSING,
            service_name=service.service_name,
            project=project,
            image=service.image,
            status="absent",
            diffs=[FieldDiff(field="state", expected="running", observed="absent", changed=True)],
            expected_yaml=expected_yaml,
            observed_yaml=None,
            message=(
                f"Service '{service.service_name}' is declared in Git but no container "
                f"is running on {context.hostname}."
            ),
            actions=["revert"],
            detected_at=now,
        )

    diffs = compare(service, container, project)
    observed_yaml = render_service_yaml(
        observed_service(container, service.service_name, project, context)
    )
    changed = [d.field for d in diffs if d.changed]
    pending = context.pending.get(container.id)
    if container.state in MISSING_STATES:
        status = DriftStatus.MISSING
        message = (
            f"Container '{container.name}' for service '{service.service_name}' "
            f"is {container.state}."
        )
        actions = ["revert", "kill"]
    elif pending is not None:
        status = DriftStatus.PENDING_ADOPTION
        message = f"'{container.name}' is buffered while adoption PR #{pending.pr_number} deploys."
        actions = []
    elif changed:
        status = DriftStatus.DRIFTED
        message = (
            f"DRIFT: '{container.name}' differs from baseline service '{service.service_name}' "
            f"on {', '.join(changed)}."
        )
        actions = ["revert", "adopt"]
    else:
        status = DriftStatus.MATCHED
        message = f"'{container.name}' matches baseline service '{service.service_name}'."
        actions = []
    return DriftRecord(
        ref=container.name,
        drift_status=status,
        service_name=service.service_name,
        project=project,
        container_id=container.id,
        container_name=container.name,
        image=container.image,
        status=container.state,
        diffs=diffs,
        expected_yaml=expected_yaml,
        observed_yaml=observed_yaml,
        message=message,
        actions=actions,
        detected_at=now,
        pending_adoption=pending,
    )


def evaluate(containers: list[LiveContainer], context: EvaluationContext) -> Evaluation:
    """Classify every container on the host against the baseline.

    Evaluation order per container: hard exclusions, whitelist, managed
    namespace (MATCHED / DRIFTED / MISSING / ROGUE), everything else ROGUE.
    Baseline services with no container become MISSING rows.

    Args:
        containers: Every container on the host.
        context: Baseline, exemptions and lookups.

    Returns:
        Dashboard rows and the list of exempt containers.
    """
    now = datetime.now(timezone.utc)
    exempt: list[str] = []
    candidates: list[LiveContainer] = []
    for container in containers:
        reason = exclusion_reason(container, context)
        if reason:
            exempt.append(f"{container.name} ({reason})")
        else:
            candidates.append(container)

    baseline = context.baseline
    if baseline is None:
        logger.debug("No baseline loaded; {} containers left unclassified", len(candidates))
        return Evaluation(records=[], exempt=exempt)

    by_service: dict[str, list[LiveContainer]] = {name: [] for name in baseline.services}
    rogues: list[tuple[LiveContainer, str]] = []
    for container in candidates:
        project = container.compose_project
        if project == baseline.project_name:
            service_name = map_to_service(container, baseline)
            if service_name is None:
                rogues.append((container, "labelled for the managed project but not declared"))
            else:
                by_service[service_name].append(container)
        elif project:
            rogues.append((container, f"belongs to untracked Compose project '{project}'"))
        else:
            rogues.append((container, "no Compose project labels"))

    records: list[DriftRecord] = []
    for service_name, service in baseline.services.items():
        instances = by_service[service_name]
        if not instances:
            records.append(_managed_record(service, None, context, now))
            continue
        primary, extras = _pick_primary(instances)
        records.append(_managed_record(service, primary, context, now))
        for extra in extras:
            rogues.append(
                (extra, f"duplicate replica of '{service_name}'; only one instance is allowed")
            )

    for container, reason in rogues:
        pending = context.pending.get(container.id)
        record = _rogue_record(container, context, now, reason)
        if pending is not None:
            record = record.model_copy(
                update={
                    "drift_status": DriftStatus.PENDING_ADOPTION,
                    "pending_adoption": pending,
                    "actions": [],
                    "message": (
                        f"'{container.name}' is buffered while adoption PR "
                        f"#{pending.pr_number} deploys."
                    ),
                }
            )
        records.append(record)
    return Evaluation(records=records, exempt=exempt)
