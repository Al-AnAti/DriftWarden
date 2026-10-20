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
    ) -> PendingAdoption:
        """Buffer a container after its adoption pull request was opened."""
        ...

    def active_pending(self) -> list[PendingAdoption]:
        """Buffer entries that are neither cleared nor expired."""
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
