"""In-memory snapshot holder and live-update fan-out.

The projector keeps the latest ``HostSnapshot`` for the web layer (read via a
typed accessor on ``app.state``), computes status transitions between
consecutive snapshots, and pushes JSON payloads to Server-Sent Events
subscribers.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any, NamedTuple

from loguru import logger

from shared.constants import BaselineSource, DriftStatus
from shared.models import BaselineInfo, DriftRecord, HostSnapshot


def record_key(record: DriftRecord) -> str:
    """Stable identity of a record across cycles.

    Managed services are keyed by service name; rogue containers by id.
    """
    if record.service_name:
        return f"service:{record.service_name}"
    return f"container:{record.container_id or record.ref}"


class Transition(NamedTuple):
    """A record whose status changed since the previous snapshot.

    Attributes:
        record: The entry as it is now.
        previous: The same entry in the previous snapshot, or ``None`` when it is new.
    """

    record: DriftRecord
    previous: DriftRecord | None

    @property
    def previous_status(self) -> DriftStatus | None:
        """Status before the change, ``None`` for a new entry."""
        return self.previous.drift_status if self.previous is not None else None


TRIGGER_LABELS: dict[str, str] = {
    "startup": "Engine Startup",
    "sweep": "Poller Evaluation",
    "event": "Docker Event",
    "manual": "Manual Refresh",
    "baseline": "Baseline Updated",
    "pr-merged": "Baseline Updated",
    "pr-closed": "Adopt PR Closed",
    "post-enforce": "Enforce Recreate",
    "revert": "Enforce Recreate",
    "kill": "Enforce Remove",
    "adopt": "Adopt PR Opened",
    "batch-adopt": "Adopt PR Opened",
    "adopt-deletion": "Deletion PR Opened",
    "revoke": "Adoption Revoked",
    "reverse": "Action Reversed",
    "batch": "Batch Action",
    "settings": "Settings Changed",
    "whitelist": "Whitelist Changed",
}
"""Short trigger label per reconcile trigger."""


def transition_trigger(trigger: str, change: Transition) -> str:
    """Structured cause of a status change, for the drift transitions timeline.

    The adoption buffer is described from the records themselves (which PR opened or
    ended it), so the label is right whichever cycle observes the change; everything
    else is named after the reconcile trigger.

    Args:
        trigger: What started the reconcile cycle (``sweep``, ``event``, ``revert`` ...).
        change: The transition.

    Returns:
        A short label such as ``Poller Evaluation`` or ``Baseline Updated (PR #38)``.
    """
    record, previous = change
    if previous is None and trigger == "startup":
        return TRIGGER_LABELS["startup"]  # nothing to compare with yet
    if record.drift_status is DriftStatus.PENDING_ADOPTION and record.pending_adoption:
        pending = record.pending_adoption
        verb = "Deletion" if pending.kind == "remove" else "Adopt"
        if pending.pr_number is None:
            return f"{verb} Buffered"
        return f"{verb} PR #{pending.pr_number} Opened"
    if previous is not None and previous.drift_status is DriftStatus.PENDING_ADOPTION:
        number = previous.pending_adoption.pr_number if previous.pending_adoption else None
        pr = f"PR #{number}" if number is not None else "PR"
        if record.drift_status in (DriftStatus.AWAITING_RESTART, DriftStatus.MATCHED):
            return f"Baseline Updated ({pr})"
        if trigger == "pr-closed":
            return f"Adopt {pr} Closed"
        if trigger == "revoke":
            return f"Adoption Revoked ({pr})"
        if trigger != "reverse":
            return f"Adoption Ended ({pr})"
    return TRIGGER_LABELS.get(trigger, trigger.replace("-", " ").title())


def snapshot_payload(snapshot: HostSnapshot) -> dict[str, Any]:
    """JSON-ready representation with derived counts and headline status."""
    payload = snapshot.model_dump(mode="json", exclude={"baseline": {"raw_yaml"}})
    payload["counts"] = snapshot.counts()
    payload["overall"] = snapshot.overall
    return payload


class StateProjector:
    """Owns the current snapshot and broadcasts changes.

    Args:
        hostname: Friendly host name.
        mode: Configured guardrail mode.
    """

    def __init__(self, hostname: str, mode: str) -> None:
        self._snapshot = HostSnapshot(
            hostname=hostname,
            taken_at=datetime.now(timezone.utc),
            trigger="init",
            mode=mode,
            baseline=BaselineInfo(source=BaselineSource.UNAVAILABLE, available=False),
        )
        self._subscribers: set[asyncio.Queue[str]] = set()
        self.version = 0
        self.mode = mode

    @property
    def snapshot(self) -> HostSnapshot:
        """The most recent reconciled snapshot."""
        return self._snapshot

    def transitions(self, new: HostSnapshot) -> list[Transition]:
        """Records whose status differs from the previous snapshot.

        An entry is matched by :func:`record_key`; when that finds nothing, by its
        container id, so a rogue container that becomes a declared service (its key
        moves from the container to the service, as when an adoption PR merges) keeps
        its history: ``PENDING_ADOPTION`` then ``AWAITING_RESTART``, not a new entry.

        Args:
            new: Snapshot about to be published.

        Returns:
            One :class:`Transition` per changed or new (non-matched) entry.
        """
        previous = {record_key(r): r for r in self._snapshot.records}
        by_container = {r.container_id: r for r in self._snapshot.records if r.container_id}
        new_keys = {record_key(r) for r in new.records}
        changed: list[Transition] = []
        for record in new.records:
            before = previous.get(record_key(record))
            if before is None and record.container_id:
                candidate = by_container.get(record.container_id)
                if candidate is not None and record_key(candidate) not in new_keys:
                    before = candidate
            if before is None:
                if record.drift_status is not DriftStatus.MATCHED:
                    changed.append(Transition(record, None))
            elif before.drift_status is not record.drift_status:
                changed.append(Transition(record, before))
        return changed

    def publish(
        self,
        snapshot: HostSnapshot,
        transitions: list[Transition] | None = None,
    ) -> None:
        """Replace the current snapshot and notify every subscriber.

        Args:
            snapshot: The new snapshot.
            transitions: Status changes since the previous snapshot, pushed to
                subscribers so the UI can toast them.
        """
        self._snapshot = snapshot
        self.version += 1
        if not self._subscribers:
            return
        message = json.dumps(
            {
                "version": self.version,
                "snapshot": snapshot_payload(snapshot),
                "transitions": [
                    {
                        "ref": record.ref,
                        "display_name": record.display_name or record.ref,
                        "status": record.drift_status.value,
                        "previous": previous.drift_status.value if previous else None,
                        "message": record.message,
                        "reason": record.reason,
                    }
                    for record, previous in (transitions or [])
                ],
            }
        )
        for queue in list(self._subscribers):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                logger.debug("Dropping SSE update for a slow subscriber")

    def subscribe(self) -> asyncio.Queue[str]:
        """Register a Server-Sent Events subscriber."""
        queue: asyncio.Queue[str] = asyncio.Queue(maxsize=32)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[str]) -> None:
        """Remove a subscriber."""
        self._subscribers.discard(queue)

    @property
    def subscriber_count(self) -> int:
        """Number of live SSE connections."""
        return len(self._subscribers)
