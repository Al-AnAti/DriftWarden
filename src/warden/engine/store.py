"""Persistence contract the engine depends on.

The concrete implementation lives in ``web.db.repository.Store``; the engine
only sees this structural protocol so ``warden`` never imports ``web``.
"""

from typing import Any, Protocol

from shared.constants import AuditAction, DriftStatus, WhitelistTargetType
from shared.models import AuditLogEntry, PendingAdoption, WhitelistEntry


class StateStore(Protocol):
    """Durable state the reconciler reads and writes."""

    def record_audit(
        self,
        action: AuditAction,
        target: str,
        details: str | None = None,
        *,
        snapshot: dict[str, Any] | None = None,
        reverses_id: int | None = None,
    ) -> int:
        """Append an audit row (optionally with a reversal snapshot) and return its id."""
        ...

    def get_audit(self, entry_id: int) -> tuple[AuditLogEntry, dict[str, Any] | None] | None:
        """One audit entry with its reversal state and snapshot."""
        ...

    def adoption_entries_for_pr(self, pr_number: int) -> list[AuditLogEntry]:
        """Unreversed adoption entries that share a pull request."""
        ...

    def add_pending(
        self,
        *,
        container_id: str,
        container_name: str,
        service_name: str,
        ttl_seconds: int,
        pr_number: int | None,
        pr_url: str | None,
        branch: str | None,
        kind: str = "adopt",
    ) -> PendingAdoption:
        """Buffer a container or service removal after its pull request was opened."""
        ...

    def active_pending(self) -> list[PendingAdoption]:
        """Buffer entries that are neither cleared nor expired."""
        ...

    def set_pending_state(self, entry_id: int, pr_state: str) -> None:
        """Record the last observed pull request state of a buffer entry."""
        ...

    def clear_pending(self, entry_id: int, outcome: str) -> None:
        """Close a buffer entry with an outcome."""
        ...

    def list_whitelist(self) -> list[WhitelistEntry]:
        """All operator exemptions."""
        ...

    def add_whitelist(
        self, target_type: WhitelistTargetType, target_identifier: str, reason: str
    ) -> WhitelistEntry:
        """Record an exemption."""
        ...

    def remove_whitelist(self, entry_id: int) -> WhitelistEntry | None:
        """Delete an exemption by id."""
        ...

    def add_adopted_instance(
        self, container_id: str, service_name: str, pr_number: int | None
    ) -> None:
        """Remember an adopted container instance."""
        ...

    def list_adopted_instances(self) -> dict[str, str]:
        """Active adopted instances: container id -> service name."""
        ...

    def invalidate_adopted_instance(self, container_id: str) -> None:
        """Forget an adopted instance that no longer exists."""
        ...

    def list_settings(self) -> dict[str, str]:
        """Operator setting overrides."""
        ...

    def set_setting(self, key: str, value: str) -> None:
        """Persist a setting override."""
        ...

    def delete_setting(self, key: str) -> None:
        """Remove a setting override."""
        ...

    def record_drift_event(
        self,
        *,
        ref: str,
        drift_status: DriftStatus,
        previous_status: DriftStatus | None,
        service_name: str | None,
        container_name: str | None,
        trigger: str | None = None,
        reason: str | None = None,
        details: str | None = None,
    ) -> None:
        """Persist a status transition with its cause and concise reason."""
        ...
