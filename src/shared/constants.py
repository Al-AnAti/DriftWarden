"""Shared constants and enumerations for DriftWarden."""

from enum import Enum

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
"""Docker Compose label used to identify the owning Compose project."""

COMPOSE_SERVICE_LABEL = "com.docker.compose.service"
"""Docker Compose label naming the service a container was created from."""

COMPOSE_CONTAINER_NUMBER_LABEL = "com.docker.compose.container-number"
"""Docker Compose label carrying the replica index of a container."""

COMPOSE_ONEOFF_LABEL = "com.docker.compose.oneoff"
"""Docker Compose label distinguishing service containers from one-off runs."""

COMPOSE_LABEL_PREFIX = "com.docker.compose."
"""Prefix shared by every label Docker Compose injects on its own."""

WARDEN_IGNORE_LABEL = "warden.ignore"
"""Opt-out label: containers carrying ``warden.ignore=true`` are never evaluated."""

WARDEN_MANAGED_BY_LABEL = "warden.managed-by"
"""Label stamped on every container DriftWarden creates or recreates."""

WARDEN_MANAGED_BY_VALUE = "driftwarden"
"""Value written under :data:`WARDEN_MANAGED_BY_LABEL`."""

WARDEN_ACTION_LABEL = "warden.action-id"
"""Label carrying the enforcement action id that created a container."""

DEFAULT_EXCLUDED_NAME_PREFIXES: tuple[str, ...] = ("warden-", "driftwarden", "k8s_")
"""Container name prefixes that are always excluded from evaluation."""

DIFF_FIELDS: tuple[str, ...] = (
    "image",
    "ports",
    "environment",
    "volumes",
    "labels",
    "restart",
    "networks",
)
"""Service attributes compared by the diff engine, in display order."""


class DriftStatus(str, Enum):
    """Describes how a running or expected service relates to the Git baseline."""

    MATCHED = "matched"
    """The running container matches the declarative Git baseline."""

    ROGUE = "rogue"
    """The running container is not tracked in the declarative Git baseline."""

    MISSING = "missing"
    """The service exists in Git but is not currently running."""

    DRIFTED = "drifted"
    """The service exists in Git, but its live configuration differs from the baseline."""

    PENDING_ADOPTION = "pending_adoption"
    """The container is buffered while an adoption change is being deployed."""

    AWAITING_RESTART = "awaiting_restart"
    """An adopted rogue container is now declared in Git but still runs without the
    Compose labels Git would give it; it becomes MATCHED once Compose (or Enforce)
    recreates it."""


class AuditAction(str, Enum):
    """Identifies an auditable action performed by the control plane or engine."""

    ENFORCE_KILL = "enforce_kill"
    """A rogue container was terminated by enforcement."""

    ENFORCE_KILL_FAILED = "enforce_kill_failed"
    """Enforcement tried to terminate a rogue container and failed."""

    REVERT_REQUESTED = "revert_requested"
    """Legacy (no longer written): a manual Enforce was requested."""

    REVERT_COMPLETED = "revert_completed"
    """A service was recreated from the Git baseline successfully."""

    REVERT_FAILED = "revert_failed"
    """Recreating a service from the Git baseline failed."""

    ADOPT_REQUESTED = "adopt_requested"
    """Legacy (no longer written): an adoption was requested; ``adopt_pr_opened`` or
    ``adopt_failed`` now records the outcome alone."""

    ADOPT_PR_OPENED = "adopt_pr_opened"
    """A Reverse-GitOps pull request was opened for an adopted container."""

    ADOPT_FAILED = "adopt_failed"
    """An adoption request could not be turned into a pull request."""

    ADOPTION_CLEARED = "adoption_cleared"
    """Legacy (no longer written): replaced by ``adoption_merged``."""

    ADOPTION_EXPIRED = "adoption_expired"
    """A pending adoption without a pull request reached its fallback TTL."""

    ADOPTION_MERGED = "adoption_merged"
    """The adoption pull request was merged and the baseline now reflects it."""

    ADOPTION_CLOSED = "adoption_closed"
    """The adoption pull request was closed without merging; the entry left the buffer."""

    BASELINE_FETCHED = "baseline_fetched"
    """Legacy (no longer written): baseline loads go to the application log."""

    BASELINE_FETCH_FAILED = "baseline_fetch_failed"
    """The declarative baseline could not be fetched."""

    ROGUE_DETECTED = "rogue_detected"
    """A container with no declaration in the baseline appeared on the host."""

    DRIFT_DETECTED = "drift_detected"
    """A managed container diverged from its baseline definition."""

    MISSING_DETECTED = "missing_detected"
    """A baseline service has no running container."""

    STATE_RESTORED = "state_restored"
    """Legacy (no longer written): returns to MATCHED are drift transitions only."""

    WHITELIST_ADDED = "whitelist_added"
    """An operator exempted a container or Compose project from evaluation."""

    WHITELIST_REMOVED = "whitelist_removed"
    """An operator removed a whitelist exemption."""

    ENGINE_STARTED = "engine_started"
    """The reconciliation engine finished its startup pass."""

    REVERSAL_COMPLETED = "reversal_completed"
    """An operator reversed an earlier action from the audit log."""

    REVERSAL_FAILED = "reversal_failed"
    """Reversing an earlier action failed; it can be retried."""

    REVERSAL_REFUSED = "reversal_refused"
    """An earlier action can no longer be reversed (for example its PR was merged)."""

    ADOPTION_INVALIDATED = "adoption_invalidated"
    """The container instance an adoption was bound to no longer exists."""

    ADOPTION_REVOKED = "adoption_revoked"
    """An operator revoked a buffered adoption; its pull request was closed."""

    SETTINGS_CHANGED = "settings_changed"
    """An operator changed a runtime setting from the Cluster Config page."""

    BATCH_REQUESTED = "batch_requested"
    """An operator ran Enforce All or Adopt All over a scope of workloads."""


REVERSIBLE_ACTIONS: frozenset[AuditAction] = frozenset(
    {AuditAction.REVERT_COMPLETED, AuditAction.ENFORCE_KILL, AuditAction.ADOPT_PR_OPENED}
)
"""Audit actions that can be undone from the Events Log when they carry a snapshot."""


class Severity(str, Enum):
    """How urgently an event needs attention; shown as a badge in the Events Log."""

    CRITICAL = "critical"
    """A declared workload is down."""

    HIGH = "high"
    """Unmanaged workload on the host, or a remediation that failed."""

    MEDIUM = "medium"
    """A managed workload diverged from Git, or Enforce changed the host."""

    LOW = "low"
    """Bookkeeping that changes state but needs no attention."""

    INFO = "info"
    """Everything is as declared."""


class EventResult(str, Enum):
    """Outcome of an audited action: the RESULT column of the audit log."""

    SUCCESS = "success"
    """The action completed (and, where it applies, was verified)."""

    FAILED = "failed"
    """The action did not achieve its goal; the details say why."""

    PENDING = "pending"
    """The action started and its outcome is not known yet (an open pull request)."""


STATUS_SEVERITY: dict[DriftStatus, Severity] = {
    DriftStatus.MISSING: Severity.CRITICAL,
    DriftStatus.ROGUE: Severity.HIGH,
    DriftStatus.DRIFTED: Severity.MEDIUM,
    DriftStatus.AWAITING_RESTART: Severity.LOW,
    DriftStatus.PENDING_ADOPTION: Severity.INFO,
    DriftStatus.MATCHED: Severity.INFO,
}
"""Severity of a drift transition, by the status it moved to."""

AUDIT_SEVERITY: dict[AuditAction, Severity] = {
    AuditAction.MISSING_DETECTED: Severity.CRITICAL,
    AuditAction.ROGUE_DETECTED: Severity.HIGH,
    AuditAction.ENFORCE_KILL_FAILED: Severity.HIGH,
    AuditAction.REVERT_FAILED: Severity.HIGH,
    AuditAction.ADOPT_FAILED: Severity.HIGH,
    AuditAction.REVERSAL_FAILED: Severity.HIGH,
    AuditAction.BASELINE_FETCH_FAILED: Severity.HIGH,
    AuditAction.DRIFT_DETECTED: Severity.MEDIUM,
    AuditAction.ENFORCE_KILL: Severity.MEDIUM,
    AuditAction.REVERT_COMPLETED: Severity.MEDIUM,
    AuditAction.REVERSAL_COMPLETED: Severity.MEDIUM,
    AuditAction.ADOPTION_INVALIDATED: Severity.MEDIUM,
    AuditAction.ADOPT_PR_OPENED: Severity.LOW,
    AuditAction.ADOPTION_CLOSED: Severity.LOW,
    AuditAction.ADOPTION_EXPIRED: Severity.LOW,
    AuditAction.ADOPTION_REVOKED: Severity.LOW,
    AuditAction.REVERSAL_REFUSED: Severity.LOW,
    AuditAction.WHITELIST_ADDED: Severity.LOW,
    AuditAction.WHITELIST_REMOVED: Severity.LOW,
    AuditAction.SETTINGS_CHANGED: Severity.LOW,
    AuditAction.BATCH_REQUESTED: Severity.LOW,
    AuditAction.ADOPTION_MERGED: Severity.INFO,
    AuditAction.ENGINE_STARTED: Severity.INFO,
    AuditAction.REVERT_REQUESTED: Severity.INFO,
    AuditAction.ADOPT_REQUESTED: Severity.INFO,
    AuditAction.ADOPTION_CLEARED: Severity.INFO,
    AuditAction.BASELINE_FETCHED: Severity.INFO,
    AuditAction.STATE_RESTORED: Severity.INFO,
}
"""Default severity of each audit action (legacy actions included, for old rows)."""

AUDIT_RESULT: dict[AuditAction, EventResult] = {
    AuditAction.ENFORCE_KILL: EventResult.SUCCESS,
    AuditAction.REVERT_COMPLETED: EventResult.SUCCESS,
    AuditAction.ADOPTION_MERGED: EventResult.SUCCESS,
    AuditAction.REVERSAL_COMPLETED: EventResult.SUCCESS,
    AuditAction.ENFORCE_KILL_FAILED: EventResult.FAILED,
    AuditAction.REVERT_FAILED: EventResult.FAILED,
    AuditAction.ADOPT_FAILED: EventResult.FAILED,
    AuditAction.REVERSAL_FAILED: EventResult.FAILED,
    AuditAction.REVERSAL_REFUSED: EventResult.FAILED,
    AuditAction.ADOPT_PR_OPENED: EventResult.PENDING,
}
"""Result written by default for new rows of these actions; detections and settings
rows have no result. A caller can override it (for example after verification)."""


def status_severity(status: DriftStatus) -> Severity:
    """Severity of a transition into ``status``."""
    return STATUS_SEVERITY[status]


def audit_severity(action: AuditAction) -> Severity:
    """Default severity of an audit action."""
    return AUDIT_SEVERITY.get(action, Severity.INFO)


def default_result(action: AuditAction) -> EventResult | None:
    """Result recorded for a new row of ``action`` when the caller gives none."""
    return AUDIT_RESULT.get(action)


CORRELATION_PREFIX = "INC-"
"""Prefix of incident correlation ids (``INC-001``)."""


def correlation_id_for(number: int) -> str:
    """Format an incident number as a correlation id: ``7`` -> ``INC-007``."""
    return f"{CORRELATION_PREFIX}{number:03d}"


class WhitelistTargetType(str, Enum):
    """Kinds of entity an operator can exempt from drift evaluation."""

    CONTAINER = "CONTAINER"
    """A single container, matched by id or name."""

    PROJECT = "PROJECT"
    """An entire Compose project, matched by its ``com.docker.compose.project`` label."""


class BaselineSource(str, Enum):
    """Where the currently cached baseline came from."""

    GITHUB = "github"
    """Fetched from the configured GitHub repository."""

    MOCK = "mock"
    """Loaded from the local fixture because ``ALLOW_MOCK_BASELINE`` is set."""

    UNAVAILABLE = "unavailable"
    """No baseline has been loaded yet; the engine runs in degraded observe mode."""
