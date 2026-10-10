"""Typed data contracts shared by the Warden engine and Web control plane."""

from dataclasses import dataclass, field

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from shared.constants import AuditAction, DriftStatus


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
    def extract(cls, payload: dict) -> "RawDockerEvent":
        """Instantiates an event securely from a raw Docker SDK dictionary."""
        return cls(
            container_id=payload.get("id", ""),
            action=payload.get("Action", ""),
            attributes=payload.get("Actor", {}).get("Attributes", {}),
        )


@dataclass(slots=True)
class ParsedService:
    """Represents a normalized service extracted from a Compose YAML AST.

    Attributes:
        service_name: Name of the service in the Compose document.
        image: Container image declared by the service, when present.
        ports: Port mappings declared by the service.
        labels: String labels declared by the service.
    """

    service_name: str
    image: str | None = None
    ports: list[str] = field(default_factory=list)
    labels: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    volumes: list[str] = field(default_factory=list)

    @classmethod
    def extract(cls, name: str, node: dict) -> "ParsedService":
        """Safely extracts service definitions from a ruamel.yaml AST node."""
        return cls(
            service_name=name,
            image=node.get("image"),
            ports=node.get("ports", []),
            labels=node.get("labels", {}),
            environment=node.get("environment", {}),
            volumes=node.get("volumes", []),
        )


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


class AdoptionRequest(BaseModel):
    """Validates a request to adopt a live container into Git.

    Attributes:
        container_name: Name of the live container to adopt.
        image: Container image to record in the Compose service.
        target_repo: Repository in which the adoption change will be created.
        target_branch: Branch from which the adoption change is based.
    """

    model_config = ConfigDict(extra="forbid")

    container_name: str = Field(min_length=1)
    image: str = Field(min_length=1)
    target_repo: str = Field(min_length=1)
    target_branch: str = Field(min_length=1)


class AuditLogEntry(BaseModel):
    """Represents an audit record persisted by SQLite and returned to the UI.

    Attributes:
        id: Database identifier for the audit record.
        action: Action recorded in the audit log.
        target: Resource affected by the action.
        timestamp: UTC time at which the action occurred.
    """

    model_config = ConfigDict(use_enum_values=False, extra="forbid")

    id: int = Field(ge=1)
    action: AuditAction
    target: str = Field(min_length=1)
    timestamp: AwareDatetime
