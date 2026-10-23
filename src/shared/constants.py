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
    """A drifted or missing service was commanded to revert to the Git baseline."""

    REVERT_COMPLETED = "revert_completed"
    """A service was recreated from the Git baseline successfully."""

    REVERT_FAILED = "revert_failed"
    """Recreating a service from the Git baseline failed."""

    ADOPT_REQUESTED = "adopt_requested"
    """An adoption request was created for a rogue or drifted container."""

    ADOPT_PR_OPENED = "adopt_pr_opened"
    """A Reverse-GitOps pull request was opened for an adopted container."""

    ADOPT_FAILED = "adopt_failed"
    """An adoption request could not be turned into a pull request."""

    ADOPTION_CLEARED = "adoption_cleared"
    """A pending adoption was cleared because the baseline now declares the service."""

    ADOPTION_EXPIRED = "adoption_expired"
    """A pending adoption without a pull request reached its fallback TTL."""

    ADOPTION_MERGED = "adoption_merged"
    """The adoption pull request was merged; the baseline is being re-fetched."""

    ADOPTION_CLOSED = "adoption_closed"
    """The adoption pull request was closed without merging; the entry left the buffer."""

    BASELINE_FETCHED = "baseline_fetched"
    """The declarative baseline was fetched successfully."""

    BASELINE_FETCH_FAILED = "baseline_fetch_failed"
    """The declarative baseline could not be fetched."""

    ROGUE_DETECTED = "rogue_detected"
    """A container with no declaration in the baseline appeared on the host."""

    DRIFT_DETECTED = "drift_detected"
    """A managed container diverged from its baseline definition."""

    MISSING_DETECTED = "missing_detected"
    """A baseline service has no running container."""

    STATE_RESTORED = "state_restored"
    """A previously rogue, drifted or missing entry returned to the baseline state."""

    WHITELIST_ADDED = "whitelist_added"
    """An operator exempted a container or Compose project from evaluation."""

    WHITELIST_REMOVED = "whitelist_removed"
    """An operator removed a whitelist exemption."""

    ENGINE_STARTED = "engine_started"
    """The reconciliation engine finished its startup pass."""

    ADOPTION_REVOKED = "adoption_revoked"
    """An operator revoked a buffered adoption; its pull request was closed."""

    SETTINGS_CHANGED = "settings_changed"
    """An operator changed a runtime setting from the Cluster Config page."""

    BATCH_REQUESTED = "batch_requested"
    """An operator ran Enforce All or Adopt All over a scope of workloads."""


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
