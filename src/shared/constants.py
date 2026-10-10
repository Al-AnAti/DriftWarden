"""Shared constants and enumerations for DriftWarden."""

from enum import Enum

COMPOSE_PROJECT_LABEL = "com.docker.compose.project"
"""Docker Compose label used to identify the owning Compose project."""


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


class AuditAction(str, Enum):
    """Identifies an auditable action performed by the control plane."""

    ENFORCE_KILL = "enforce_kill"
    """A rogue container was terminated by enforcement."""

    REVERT_REQUESTED = "revert_requested"
    """A drifted container was manually commanded to revert to the Git baseline."""

    ADOPT_REQUESTED = "adopt_requested"
    """An adoption request was created for a drifted container."""

    BASELINE_FETCHED = "baseline_fetched"
    """The declarative baseline was fetched successfully."""
