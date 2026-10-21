"""Typed data contracts shared by the Warden engine and Web control plane.

Internal engine structures use ``@dataclass``; anything that crosses the
engine/web boundary, the database or the HTTP API is a Pydantic model.
"""

from dataclasses import dataclass, field
from typing import Any

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from shared.constants import (
    AuditAction,
    BaselineSource,
    DriftStatus,
    WhitelistTargetType,
)


@dataclass(slots=True, frozen=True)
class RawDockerEvent:
    """Represents an event received directly from the Docker event stream.

    Attributes:
        container_id: Docker identifier of the affected container.
        action: Docker event action, such as ``start`` or ``die``.
        attributes: String attributes supplied by Docker for the event.
    """

    container_id: str
    action: str
    attributes: dict[str, str] = field(default_factory=dict)

    @classmethod
    def extract(cls, payload: dict[str, Any]) -> "RawDockerEvent":
        """Instantiates an event securely from a raw Docker SDK dictionary.

        Args:
            payload: Decoded event dictionary from ``docker.events(decode=True)``.

        Returns:
            The immutable event record.
        """
        actor = payload.get("Actor") or {}
        attributes = actor.get("Attributes") or {}
        return cls(
            container_id=str(payload.get("id", "")),
            action=str(payload.get("Action", "")),
            attributes={str(k): str(v) for k, v in attributes.items()},
        )

    @property
    def name(self) -> str:
        """Container name reported by the event, when present."""
        return self.attributes.get("name", "")


@dataclass(slots=True)
class ParsedService:
    """Represents a normalized service extracted from a Compose YAML AST.

    Every collection is stored in one canonical shape regardless of the
    short or long Compose syntax used in the file:

    * ``ports`` as ``host_ip:host_port:container_port/proto`` (ip optional).
    * ``volumes`` as ``source:target:mode``.
    * ``labels`` and ``environment`` as plain string dictionaries.

    Attributes:
        service_name: Name of the service in the Compose document.
        image: Container image declared by the service, when present.
        ports: Port mappings declared by the service.
        labels: String labels declared by the service.
        environment: Environment variables declared by the service.
        volumes: Volume bindings declared by the service.
        restart: Restart policy declared by the service, when present.
        networks: Network names declared by the service.
        container_name: Explicit container name declared by the service.
    """

    service_name: str
    image: str | None = None
    ports: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)
    restart: str | None = None
    networks: list[str] = field(default_factory=list)
    container_name: str | None = None

    @classmethod
    def extract(cls, name: str, node: dict[str, Any]) -> "ParsedService":
        """Safely extracts an already-normalised service definition.

        Args:
            name: Service key under ``services:``.
            node: Mapping whose values are already in canonical shape.

        Returns:
            The service record.
        """
        return cls(
            service_name=name,
            image=node.get("image"),
            ports=list(node.get("ports", [])),
            labels=dict(node.get("labels", {})),
            environment=dict(node.get("environment", {})),
            volumes=list(node.get("volumes", [])),
            restart=node.get("restart"),
            networks=list(node.get("networks", [])),
            container_name=node.get("container_name"),
        )


class FieldDiff(BaseModel):
    """One compared attribute of a managed service.

    Attributes:
        field: Attribute name (``image``, ``ports`` ...).
        expected: Canonical value declared in the Git baseline.
        observed: Canonical value observed on the host.
        changed: Whether the two values differ.
    """

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    expected: str | None = None
    observed: str | None = None
    changed: bool = False


class ContainerState(BaseModel):
    """Represents a normalized container state exposed to API consumers.

    Attributes:
        id: Docker container identifier.
        name: Human-readable Docker container name.
        image: Image used to run the container.
        status: Runtime status reported by Docker.
        drift_status: Relationship between the container and Git baseline.
    """

    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    image: str = Field(min_length=1)
    status: str = Field(min_length=1)
    drift_status: DriftStatus


class PendingAdoption(BaseModel):
    """A container buffered while its adoption pull request deploys.

    Attributes:
        id: Database identifier of the buffer row.
        container_id: Docker identifier of the adopted container.
        container_name: Name of the adopted container.
        service_name: Compose service name written into the pull request.
        pr_number: Pull request number on the target repository.
        pr_url: Browser URL of the pull request.
        branch: Head branch of the pull request.
        created_at: When the adoption was requested.
        expires_at: When the buffer entry is dropped if the baseline never catches up.
        kind: ``adopt`` (add or sync a service) or ``remove`` (delete a service block).
        pr_state: Last observed pull request state: ``open``, ``merged`` or ``closed``.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1)
    kind: str = "adopt"
    pr_state: str = "open"
    container_id: str = Field(min_length=1)
    container_name: str = Field(min_length=1)
    service_name: str = Field(min_length=1)
    pr_number: int | None = None
    pr_url: str | None = None
    branch: str | None = None
    created_at: AwareDatetime
    expires_at: AwareDatetime


class DriftRecord(BaseModel):
    """One row of the reconciled host state shown on the dashboard.

    Managed services are keyed by their baseline service name; rogue containers
    by their container id. ``ref`` is the stable identifier used in URLs.

    Attributes:
        ref: URL-safe key: the container name when one exists, else the service name.
        display_name: Name shown in tables: the live container name, else the baseline's
            declared ``container_name``, else the service name.
        drift_status: Relationship between the entry and the Git baseline.
        service_name: Baseline service name, when the entry maps to one.
        project: Compose project the container claims to belong to, if any.
        container_id: Docker container identifier, when a container exists.
        container_name: Docker container name, when a container exists.
        image: Image used by the live container, or declared by the baseline.
        status: Docker runtime state (``running``, ``exited`` ...) or ``absent``.
        diffs: Structured per-field comparison for managed services.
        expected_yaml: Baseline service definition rendered as YAML.
        observed_yaml: Live container rendered as an equivalent Compose service.
        message: One-line human summary of the finding.
        actions: Operator actions that apply to this entry.
        detected_at: When this state was last computed.
        pending_adoption: Buffer entry while an adoption PR deploys.
    """

    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    ref: str = Field(min_length=1)
    display_name: str = ""
    drift_status: DriftStatus
    service_name: str | None = None
    project: str | None = None
    container_id: str | None = None
    container_name: str | None = None
    image: str | None = None
    status: str = Field(min_length=1)
    diffs: list[FieldDiff] = Field(default_factory=list)
    expected_yaml: str | None = None
    observed_yaml: str | None = None
    message: str = ""
    actions: list[str] = Field(default_factory=list)
    detected_at: AwareDatetime
    pending_adoption: PendingAdoption | None = None

    @property
    def changed_fields(self) -> list[str]:
        """Names of the fields that differ from the baseline."""
        return [diff.field for diff in self.diffs if diff.changed]


class BaselineInfo(BaseModel):
    """Metadata about the baseline currently held in memory.

    Attributes:
        source: Where the baseline came from.
        available: Whether a baseline has been loaded at least once.
        project_name: Resolved Compose project namespace.
        repo: Target repository (``owner/name``).
        branch: Branch the baseline is read from.
        path: Path of the Compose file inside the repository.
        sha: Git blob SHA of the loaded file.
        fetched_at: When the loaded content was last confirmed fresh.
        services: Service names declared in the baseline.
        error: Last fetch error, if the most recent poll failed.
        raw_yaml: The baseline file content as fetched.
    """

    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    source: BaselineSource
    available: bool
    project_name: str | None = None
    repo: str | None = None
    branch: str | None = None
    path: str | None = None
    sha: str | None = None
    fetched_at: AwareDatetime | None = None
    services: list[str] = Field(default_factory=list)
    error: str | None = None
    raw_yaml: str | None = None


class HostSnapshot(BaseModel):
    """The complete reconciled view of the host after one engine cycle.

    Attributes:
        hostname: Friendly name of the monitored host.
        taken_at: When the snapshot was computed.
        trigger: What started the cycle (``startup``, ``event``, ``sweep`` ...).
        mode: Configured guardrail mode.
        enforcement_active: Whether autonomous enforcement is currently armed.
        docker_connected: Whether the Docker daemon was reachable.
        docker_error: Last Docker connectivity error, if any.
        baseline: Baseline metadata.
        records: One entry per managed service or untracked container.
        exempt: Container names skipped because of exclusions or the whitelist.
    """

    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    hostname: str = Field(min_length=1)
    taken_at: AwareDatetime
    trigger: str = Field(min_length=1)
    mode: str = Field(min_length=1)
    enforcement_active: bool = False
    docker_connected: bool = False
    docker_error: str | None = None
    baseline: BaselineInfo
    records: list[DriftRecord] = Field(default_factory=list)
    exempt: list[str] = Field(default_factory=list)

    def counts(self) -> dict[str, int]:
        """Number of records per drift status, keyed by enum value."""
        totals = {status.value: 0 for status in DriftStatus}
        for record in self.records:
            totals[record.drift_status.value] += 1
        return totals

    @property
    def overall(self) -> str:
        """Headline status for the navigation pill."""
        if not self.baseline.available:
            return "baseline_unavailable"
        if not self.docker_connected:
            return "docker_unavailable"
        counts = self.counts()
        if counts[DriftStatus.ROGUE.value] or counts[DriftStatus.DRIFTED.value]:
            return "drift_detected"
        if counts[DriftStatus.MISSING.value]:
            return "missing"
        return "in_sync"

    def find(self, ref: str) -> DriftRecord | None:
        """Look a record up by ref, container name, service name or container id."""
        for record in self.records:
            if record.ref == ref:
                return record
        for record in self.records:
            if ref in (record.container_name, record.service_name, record.container_id):
                return record
        for record in self.records:
            if record.container_id and record.container_id.startswith(ref):
                return record
        return None


class AdoptionRequest(BaseModel):
    """Validates a request to adopt a live container into Git.

    The engine inspects the container itself and decides what to write, so the
    request only needs to identify the container.

    Attributes:
        container_id: Id or name of the live container to adopt.
        note: Optional operator note included in the pull request body.
    """

    model_config = ConfigDict(extra="forbid")

    container_id: str = Field(min_length=1)
    note: str | None = Field(default=None, max_length=1000)


class WhitelistRequest(BaseModel):
    """Validates a request to exempt a container or project from evaluation.

    Attributes:
        target_type: Whether the identifier names a container or a Compose project.
        target_identifier: Container id, container name or Compose project name.
        reason: Why the operator exempted it.
    """

    model_config = ConfigDict(extra="forbid")

    target_type: WhitelistTargetType
    target_identifier: str = Field(min_length=1, max_length=255)
    reason: str = Field(default="", max_length=1000)


class WhitelistEntry(BaseModel):
    """A persisted whitelist exemption.

    Attributes:
        id: Database identifier.
        target_type: Kind of entity exempted.
        target_identifier: Container id, container name or Compose project name.
        reason: Operator-supplied justification.
        created_at: When the exemption was recorded.
    """

    model_config = ConfigDict(extra="forbid")

    id: int = Field(ge=1)
    target_type: WhitelistTargetType
    target_identifier: str = Field(min_length=1)
    reason: str = ""
    created_at: AwareDatetime


class AuditLogEntry(BaseModel):
    """Represents an audit record persisted by SQLite and returned to the UI.

    Attributes:
        id: Database identifier for the audit record.
        action: Action recorded in the audit log.
        target: Resource affected by the action.
        timestamp: UTC time at which the action occurred.
        details: Free-form context (diff summary, PR link, error text).
        hostname: Host on which the action happened.
    """

    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    id: int = Field(ge=1)
    action: AuditAction
    target: str = Field(min_length=1)
    timestamp: AwareDatetime
    details: str | None = None
    hostname: str | None = None


class ActionResult(BaseModel):
    """Outcome of an operator action returned by the control plane.

    Attributes:
        ok: Whether the action succeeded.
        action: Action that was attempted.
        target: Resource the action targeted.
        message: Human-readable outcome.
        pr_url: Pull request URL for adoption actions.
    """

    model_config = ConfigDict(extra="forbid")

    ok: bool
    action: str = Field(min_length=1)
    target: str = Field(min_length=1)
    message: str = ""
    pr_url: str | None = None
