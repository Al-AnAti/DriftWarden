"""SQLAlchemy 2.0 declarative tables for DriftWarden's durable state.

SQLite holds only what must survive a restart: the audit log, the adoption
buffer, operator whitelist exemptions and the drift event history. The live
host snapshot stays in memory on the engine.
"""

from datetime import datetime, timezone

from sqlalchemy import DateTime, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


def utcnow() -> datetime:
    """Timezone-aware current UTC time."""
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    """Declarative base shared by every table."""


class AuditLogRow(Base):
    """One auditable action taken by the engine or an operator."""

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    action: Mapped[str] = mapped_column(String(64), index=True)
    target: Mapped[str] = mapped_column(String(255), index=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow, index=True)
    # Pre-action state needed to reverse it (JSON); never returned by the API or UI.
    snapshot: Mapped[str | None] = mapped_column(Text, nullable=True)
    # For a reversal entry: the audit entry it reverses. Original rows are never edited.
    reverses_id: Mapped[int | None] = mapped_column(Integer, nullable=True, index=True)
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """``Severity`` value; ``NULL`` on legacy rows (read as the action's default)."""
    result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """``EventResult`` value; ``NULL`` for detections and legacy rows."""
    correlation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    """Incident id (``INC-001``); ``NULL`` on legacy rows."""
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Structured metadata as JSON (shown in the expandable row details)."""


class PendingAdoptionRow(Base):
    """A container buffered while its adoption pull request deploys."""

    __tablename__ = "pending_adoptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), default="adopt", server_default="adopt")
    pr_state: Mapped[str] = mapped_column(String(16), default="open", server_default="open")
    container_id: Mapped[str] = mapped_column(String(128), index=True)
    container_name: Mapped[str] = mapped_column(String(255))
    service_name: Mapped[str] = mapped_column(String(255), index=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pr_url: Mapped[str | None] = mapped_column(String(512), nullable=True)
    branch: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    cleared_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    outcome: Mapped[str | None] = mapped_column(String(64), nullable=True)


class WhitelistedEntityRow(Base):
    """An operator exemption for a container or an entire Compose project."""

    __tablename__ = "whitelisted_entities"
    __table_args__ = (UniqueConstraint("target_type", "target_identifier", name="uq_whitelist"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    target_type: Mapped[str] = mapped_column(String(16))
    target_identifier: Mapped[str] = mapped_column(String(255))
    reason: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class DriftEventRow(Base):
    """A status transition observed for a service or container."""

    __tablename__ = "drift_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ref: Mapped[str] = mapped_column(String(255), index=True)
    drift_status: Mapped[str] = mapped_column(String(32), index=True)
    previous_status: Mapped[str | None] = mapped_column(String(32), nullable=True)
    service_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    container_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    details: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Legacy prose copied from the finding; new rows use ``trigger`` and ``reason``."""
    trigger: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """What caused the change: ``Poller Evaluation``, ``Baseline Updated (PR #38)`` ..."""
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Concise technical reason: ``Changed: image``, ``All fields match baseline`` ..."""
    severity: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """``Severity`` value; ``NULL`` on legacy rows (read from the status)."""
    result: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """``EventResult`` value when the transition is the outcome of an action."""
    correlation_id: Mapped[str | None] = mapped_column(String(32), nullable=True, index=True)
    """Incident id (``INC-001``); ``NULL`` on legacy rows."""
    details_json: Mapped[str | None] = mapped_column(Text, nullable=True)
    """Structured metadata as JSON (shown in the expandable row details)."""
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )


class IncidentRow(Base):
    """One incident: a workload's stretch away from its baseline state.

    The row id is the incident number (``INC-007`` is id 7). ``entity_key`` is the
    projector's record key (``service:<name>`` or ``container:<id>``) the incident is
    currently bound to; ``closed_at`` is set when the workload is back to MATCHED or
    gone, and cleared again if a reversal reopens it.
    """

    __tablename__ = "incidents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    correlation_id: Mapped[str] = mapped_column(String(32), unique=True, index=True)
    entity_key: Mapped[str] = mapped_column(String(255), index=True)
    ref: Mapped[str] = mapped_column(String(255))
    opened_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class SettingOverrideRow(Base):
    """A runtime setting changed by an operator; takes precedence over the environment."""

    __tablename__ = "setting_overrides"

    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AdoptedInstanceRow(Base):
    """A container instance whose adoption pull request merged.

    Bound to the exact container id inspected when the PR was generated; a new
    container with the same name is a different instance and never inherits it.
    """

    __tablename__ = "adopted_instances"

    container_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    service_name: Mapped[str] = mapped_column(String(255), index=True)
    pr_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    adopted_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    invalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
