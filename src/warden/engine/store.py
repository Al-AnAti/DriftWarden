"""Persistence contract the engine depends on.

The concrete implementation lives in ``web.db.repository.Store``; the engine
only sees this structural protocol so ``warden`` never imports ``web``.
"""

from typing import Protocol

from shared.constants import AuditAction, DriftStatus, WhitelistTargetType
from shared.models import PendingAdoption, WhitelistEntry


class StateStore(Protocol):
    """Durable state the reconciler reads and writes."""

    def record_audit(self, action: AuditAction, target: str, details: str | None = None) -> int:
        """Append an audit row and return its id."""
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
        details: str | None,
    ) -> None:
        """Persist a status transition."""
        ...
