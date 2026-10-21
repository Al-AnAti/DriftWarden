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


class PendingAdoptionRow(Base):
    """A container buffered while its adoption pull request deploys."""

    __tablename__ = "pending_adoptions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    kind: Mapped[str] = mapped_column(String(16), default="adopt", server_default="adopt")
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
    hostname: Mapped[str | None] = mapped_column(String(255), nullable=True)
    detected_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
