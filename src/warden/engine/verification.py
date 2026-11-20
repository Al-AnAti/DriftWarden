"""Post-remediation verification: did Enforce actually achieve what it set out to do?

Pure functions over already-inspected state. After a recreate the new container is
compared with its baseline service through the same diff engine the dashboard
uses, so "verified" means exactly what MATCHED means; after a removal the
container must be gone.
"""

from dataclasses import dataclass, field

from shared.models import FieldDiff, ParsedService
from warden.core.docker_watcher import LiveContainer
from warden.engine.diff_engine import compare


@dataclass(slots=True)
class Verification:
    """Outcome of checking the host after an Enforce step.

    Attributes:
        ok: Whether the host now matches what Enforce intended.
        reason: One line saying why (shown in the audit details).
        container: The container that was inspected, if it exists.
        diffs: Per-field comparison of the inspected container with its service
            (empty for removals or when the container is gone).
    """

    ok: bool
    reason: str
    container: LiveContainer | None = None
    diffs: list[FieldDiff] = field(default_factory=list)

    @property
    def changed_fields(self) -> list[str]:
        """Fields that still differ from the baseline."""
        return [d.field for d in self.diffs if d.changed]


def verify_recreated(
    container: LiveContainer | None,
    service: ParsedService,
    project: str,
    *,
    expected_name: str,
    expected_id: str,
) -> Verification:
    """Check that a container Enforce created runs the baseline service as declared.

    Args:
        container: The new container as inspected after ``recreate``, or ``None``.
        service: Baseline service it was created from.
        project: Compose project namespace.
        expected_name: Name ``recreate`` was asked to give it.
        expected_id: Id ``recreate`` returned.

    Returns:
        ``ok`` only when the container exists, carries the expected name and id,
        is running and differs from the service on no compared field.
    """
    short = expected_id[:12]
    if container is None:
        return Verification(False, f"container {short} does not exist after recreate")
    if container.id != expected_id:
        return Verification(
            False,
            f"inspected container {container.short_id} is not the one Enforce created ({short})",
            container,
        )
    if container.name != expected_name:
        return Verification(
            False,
            f"container {short} is named {container.name}, expected {expected_name}",
            container,
        )
    diffs = compare(service, container, project)
    changed = [d.field for d in diffs if d.changed]
    if changed:
        return Verification(
            False,
            f"recreated container {short} still differs from the baseline on {', '.join(changed)}",
            container,
            diffs,
        )
    return Verification(True, f"container {short} matches the baseline", container, diffs)


def verify_removed(container: LiveContainer | None, container_id: str) -> Verification:
    """Check that a container Enforce removed is gone from the host.

    Args:
        container: Result of inspecting ``container_id`` after the removal.
        container_id: The removed container.

    Returns:
        ``ok`` when the container no longer exists.
    """
    short = container_id[:12]
    if container is not None:
        return Verification(
            False, f"container {short} still exists after removal ({container.state})", container
        )
    return Verification(True, f"container {short} no longer exists on the host")
