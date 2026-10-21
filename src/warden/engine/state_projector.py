"""In-memory snapshot holder and live-update fan-out.

The projector keeps the latest ``HostSnapshot`` for the web layer (read via a
typed accessor on ``app.state``), computes status transitions between
consecutive snapshots, and pushes JSON payloads to Server-Sent Events
subscribers.
"""

import asyncio
import json
from datetime import datetime, timezone
from typing import Any

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

    def transitions(self, new: HostSnapshot) -> list[tuple[DriftRecord, DriftStatus | None]]:
        """Records whose status differs from the previous snapshot.

        Args:
            new: Snapshot about to be published.

        Returns:
            ``(record, previous_status)`` pairs; ``previous_status`` is ``None``
            for entries that did not exist before.
        """
        previous = {record_key(r): r for r in self._snapshot.records}
        changed: list[tuple[DriftRecord, DriftStatus | None]] = []
        for record in new.records:
            before = previous.get(record_key(record))
            if before is None:
                if record.drift_status is not DriftStatus.MATCHED:
                    changed.append((record, None))
            elif before.drift_status is not record.drift_status:
                changed.append((record, before.drift_status))
        return changed

    def publish(
        self,
        snapshot: HostSnapshot,
        transitions: list[tuple[DriftRecord, DriftStatus | None]] | None = None,
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
                        "previous": previous.value if previous else None,
                        "message": record.message,
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
