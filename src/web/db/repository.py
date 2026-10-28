"""Typed data access over the SQLite tables.

Every method is synchronous; the engine wraps calls in ``asyncio.to_thread``
and FastAPI runs sync route handlers on its worker pool. Rows never leave this
module as ORM objects: callers get the Pydantic contracts from ``shared``.
"""

from datetime import datetime, timedelta, timezone

from loguru import logger
from sqlalchemy import delete, func, select, update

from shared.constants import AuditAction, DriftStatus, WhitelistTargetType
from shared.exceptions import DatabaseWriteError
from shared.models import AuditLogEntry, PendingAdoption, WhitelistEntry
from web.db.models_db import (
    AdoptedInstanceRow,
    AuditLogRow,
    DriftEventRow,
    PendingAdoptionRow,
    SettingOverrideRow,
    WhitelistedEntityRow,
)
from web.db.session import Database


def _aware(value: datetime | None) -> datetime:
    """SQLite drops tzinfo; everything stored is UTC."""
    if value is None:
        return datetime.now(timezone.utc)
    return value if value.tzinfo else value.replace(tzinfo=timezone.utc)


def _pending_from_row(row: PendingAdoptionRow) -> PendingAdoption:
    return PendingAdoption(
        id=row.id,
        kind=row.kind or "adopt",
        pr_state=row.pr_state or "open",
        container_id=row.container_id,
        container_name=row.container_name,
        service_name=row.service_name,
        pr_number=row.pr_number,
        pr_url=row.pr_url,
        branch=row.branch,
        created_at=_aware(row.created_at),
        expires_at=_aware(row.expires_at),
    )


class Store:
    """Repository facade over the audit, buffer, whitelist and event tables.

    Args:
        database: Engine/session owner.
        hostname: Host name stamped on every audit and event row.
    """

    def __init__(self, database: Database, hostname: str) -> None:
        self._db = database
        self._hostname = hostname

    # ---------------------------------------------------------------- audit

    def record_audit(self, action: AuditAction, target: str, details: str | None = None) -> int:
        """Append an audit row.

        Args:
            action: What happened.
            target: Container, service or resource affected.
            details: Optional free text.

        Returns:
            The new row id.

        Raises:
            DatabaseWriteError: If the insert fails.
        """
        try:
            with self._db.write() as session:
                row = AuditLogRow(
                    action=action.value,
                    target=target,
                    details=details,
                    hostname=self._hostname,
                    timestamp=datetime.now(timezone.utc),
                )
                session.add(row)
                session.flush()
                row_id = row.id
        except Exception as exc:
            raise DatabaseWriteError(f"Cannot write audit row: {exc}") from exc
        logger.info("AUDIT {} target={} {}", action.value, target, details or "")
        return row_id

    def list_audit(self, limit: int = 200) -> list[AuditLogEntry]:
        """Most recent audit rows, newest first."""
        with self._db.session() as session:
            rows = session.scalars(
                select(AuditLogRow).order_by(AuditLogRow.id.desc()).limit(limit)
            ).all()
        return [
            AuditLogEntry(
                id=row.id,
                action=AuditAction(row.action),
                target=row.target,
                timestamp=_aware(row.timestamp),
                details=row.details,
                hostname=row.hostname,
            )
            for row in rows
        ]

    # --------------------------------------------------------------- buffer

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
        """Buffer a container (or a service removal) after its PR was opened.

        An active entry for the same container is replaced.
        """
        now = datetime.now(timezone.utc)
        try:
            with self._db.write() as session:
                session.execute(
                    update(PendingAdoptionRow)
                    .where(
                        PendingAdoptionRow.container_id == container_id,
                        PendingAdoptionRow.cleared_at.is_(None),
                    )
                    .values(cleared_at=now, outcome="superseded")
                )
                row = PendingAdoptionRow(
                    kind=kind,
                    container_id=container_id,
                    container_name=container_name,
                    service_name=service_name,
                    pr_number=pr_number,
                    pr_url=pr_url,
                    branch=branch,
                    created_at=now,
                    expires_at=now + timedelta(seconds=ttl_seconds),
                )
                session.add(row)
                session.flush()
                return _pending_from_row(row)
        except Exception as exc:
            raise DatabaseWriteError(f"Cannot buffer adoption: {exc}") from exc

    def active_pending(self) -> list[PendingAdoption]:
        """Buffer entries that are neither cleared nor expired."""
        with self._db.session() as session:
            rows = session.scalars(
                select(PendingAdoptionRow)
                .where(PendingAdoptionRow.cleared_at.is_(None))
                .order_by(PendingAdoptionRow.id.desc())
            ).all()
        return [_pending_from_row(row) for row in rows]

    def pending_history(self, limit: int = 100) -> list[tuple[PendingAdoption, str | None]]:
        """Every buffer entry with its outcome, newest first."""
        with self._db.session() as session:
            rows = session.scalars(
                select(PendingAdoptionRow).order_by(PendingAdoptionRow.id.desc()).limit(limit)
            ).all()
        return [(_pending_from_row(row), row.outcome) for row in rows]

    def set_pending_state(self, entry_id: int, pr_state: str) -> None:
        """Record the last observed pull request state of a buffer entry."""
        try:
            with self._db.write() as session:
                session.execute(
                    update(PendingAdoptionRow)
                    .where(PendingAdoptionRow.id == entry_id)
                    .values(pr_state=pr_state)
                )
        except Exception as exc:
            raise DatabaseWriteError(f"Cannot update adoption {entry_id}: {exc}") from exc

    def clear_pending(self, entry_id: int, outcome: str) -> None:
        """Close a buffer entry with an outcome (``baseline_updated``, ``expired`` ...)."""
        try:
            with self._db.write() as session:
                session.execute(
                    update(PendingAdoptionRow)
                    .where(PendingAdoptionRow.id == entry_id)
                    .values(cleared_at=datetime.now(timezone.utc), outcome=outcome)
                )
        except Exception as exc:
            raise DatabaseWriteError(f"Cannot clear adoption {entry_id}: {exc}") from exc

    # ------------------------------------------------------------ whitelist

    def list_whitelist(self) -> list[WhitelistEntry]:
        """All exemptions, oldest first."""
        with self._db.session() as session:
            rows = session.scalars(
                select(WhitelistedEntityRow).order_by(WhitelistedEntityRow.id.asc())
            ).all()
        return [
            WhitelistEntry(
                id=row.id,
                target_type=WhitelistTargetType(row.target_type),
                target_identifier=row.target_identifier,
                reason=row.reason,
                created_at=_aware(row.created_at),
            )
            for row in rows
        ]

    def add_whitelist(
        self, target_type: WhitelistTargetType, target_identifier: str, reason: str
    ) -> WhitelistEntry:
        """Record an exemption; re-adding an existing one returns it unchanged."""
        try:
            with self._db.write() as session:
                existing = session.scalars(
                    select(WhitelistedEntityRow).where(
                        WhitelistedEntityRow.target_type == target_type.value,
                        WhitelistedEntityRow.target_identifier == target_identifier,
                    )
                ).first()
                if existing is None:
                    existing = WhitelistedEntityRow(
                        target_type=target_type.value,
                        target_identifier=target_identifier,
                        reason=reason,
                        created_at=datetime.now(timezone.utc),
                    )
                    session.add(existing)
                    session.flush()
                return WhitelistEntry(
                    id=existing.id,
                    target_type=WhitelistTargetType(existing.target_type),
                    target_identifier=existing.target_identifier,
                    reason=existing.reason,
                    created_at=_aware(existing.created_at),
                )
        except Exception as exc:
            raise DatabaseWriteError(f"Cannot whitelist {target_identifier}: {exc}") from exc

    def remove_whitelist(self, entry_id: int) -> WhitelistEntry | None:
        """Delete an exemption by id and return what was removed."""
        try:
            with self._db.write() as session:
                row = session.get(WhitelistedEntityRow, entry_id)
                if row is None:
                    return None
                entry = WhitelistEntry(
                    id=row.id,
                    target_type=WhitelistTargetType(row.target_type),
                    target_identifier=row.target_identifier,
                    reason=row.reason,
                    created_at=_aware(row.created_at),
                )
                session.execute(
                    delete(WhitelistedEntityRow).where(WhitelistedEntityRow.id == entry_id)
                )
                return entry
        except Exception as exc:
            raise DatabaseWriteError(f"Cannot remove whitelist entry {entry_id}: {exc}") from exc

    # --------------------------------------------------------------- events

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
        """Persist a status transition for the Events Log page."""
        try:
            with self._db.write() as session:
                session.add(
                    DriftEventRow(
                        ref=ref,
                        drift_status=drift_status.value,
                        previous_status=previous_status.value if previous_status else None,
                        service_name=service_name,
                        container_name=container_name,
                        details=details,
                        hostname=self._hostname,
                        detected_at=datetime.now(timezone.utc),
                    )
                )
        except Exception as exc:
            raise DatabaseWriteError(f"Cannot write drift event: {exc}") from exc

    def list_drift_events(self, limit: int = 200) -> list[dict[str, object]]:
        """Most recent transitions, newest first, as plain rows for templates."""
        with self._db.session() as session:
            rows = session.scalars(
                select(DriftEventRow).order_by(DriftEventRow.id.desc()).limit(limit)
            ).all()
        return [
            {
                "id": row.id,
                "ref": row.ref,
                "drift_status": row.drift_status,
                "previous_status": row.previous_status,
                "service_name": row.service_name,
                "container_name": row.container_name,
                "details": row.details,
                "hostname": row.hostname,
                "detected_at": _aware(row.detected_at),
            }
            for row in rows
        ]

    # ---------------------------------------------------- adopted instances

    def add_adopted_instance(
        self, container_id: str, service_name: str, pr_number: int | None
    ) -> None:
        """Remember that this exact container instance was adopted (PR merged)."""
        try:
            with self._db.write() as session:
                row = session.get(AdoptedInstanceRow, container_id)
                if row is None:
                    session.add(
                        AdoptedInstanceRow(
                            container_id=container_id,
                            service_name=service_name,
                            pr_number=pr_number,
                            adopted_at=datetime.now(timezone.utc),
                        )
                    )
                else:
                    row.service_name = service_name
                    row.pr_number = pr_number
                    row.adopted_at = datetime.now(timezone.utc)
                    row.invalidated_at = None
        except Exception as exc:
            raise DatabaseWriteError(f"Cannot record adopted instance: {exc}") from exc

    def list_adopted_instances(self) -> dict[str, str]:
        """Active adopted instances: container id -> service name."""
        with self._db.session() as session:
            rows = session.scalars(
                select(AdoptedInstanceRow).where(AdoptedInstanceRow.invalidated_at.is_(None))
            ).all()
        return {row.container_id: row.service_name for row in rows}

    def invalidate_adopted_instance(self, container_id: str) -> None:
        """The instance is gone; a same-named newcomer must not inherit the adoption."""
        try:
            with self._db.write() as session:
                session.execute(
                    update(AdoptedInstanceRow)
                    .where(AdoptedInstanceRow.container_id == container_id)
                    .values(invalidated_at=datetime.now(timezone.utc))
                )
        except Exception as exc:
            raise DatabaseWriteError(f"Cannot invalidate adopted instance: {exc}") from exc

    # ------------------------------------------------------------- settings

    def list_settings(self) -> dict[str, str]:
        """Operator overrides keyed by settings attribute name."""
        with self._db.session() as session:
            rows = session.scalars(select(SettingOverrideRow)).all()
        return {row.key: row.value for row in rows}

    def set_setting(self, key: str, value: str) -> None:
        """Insert or replace an override."""
        try:
            with self._db.write() as session:
                row = session.get(SettingOverrideRow, key)
                if row is None:
                    session.add(
                        SettingOverrideRow(
                            key=key, value=value, updated_at=datetime.now(timezone.utc)
                        )
                    )
                else:
                    row.value = value
                    row.updated_at = datetime.now(timezone.utc)
        except Exception as exc:
            raise DatabaseWriteError(f"Cannot save setting {key}: {exc}") from exc

    def delete_setting(self, key: str) -> None:
        """Drop an override so the environment value applies again."""
        try:
            with self._db.write() as session:
                session.execute(delete(SettingOverrideRow).where(SettingOverrideRow.key == key))
        except Exception as exc:
            raise DatabaseWriteError(f"Cannot delete setting {key}: {exc}") from exc

    def clear_logs(self) -> tuple[int, int]:
        """Truncate the drift event and audit log tables in one transaction.

        Returns:
            ``(drift events removed, audit rows removed)``.

        Raises:
            DatabaseWriteError: If the transaction fails; nothing is removed then.
        """
        try:
            with self._db.write() as session:
                events = session.scalar(select(func.count(DriftEventRow.id))) or 0
                audit = session.scalar(select(func.count(AuditLogRow.id))) or 0
                session.execute(delete(DriftEventRow))
                session.execute(delete(AuditLogRow))
        except Exception as exc:
            raise DatabaseWriteError(f"Cannot clear log history: {exc}") from exc
        logger.info("Cleared log history: {} drift events, {} audit rows", events, audit)
        return events, audit

    def counts(self) -> dict[str, int]:
        """Row counts for the Cluster Config page."""
        with self._db.session() as session:
            audit = session.scalar(select(func.count(AuditLogRow.id))) or 0
            events = session.scalar(select(func.count(DriftEventRow.id))) or 0
            pending = (
                session.scalar(
                    select(func.count(PendingAdoptionRow.id)).where(
                        PendingAdoptionRow.cleared_at.is_(None)
                    )
                )
                or 0
            )
            whitelist = session.scalar(select(func.count(WhitelistedEntityRow.id))) or 0
        return {"audit": audit, "events": events, "pending": pending, "whitelist": whitelist}
