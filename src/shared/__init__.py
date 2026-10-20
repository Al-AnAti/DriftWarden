"""
DriftWarden Shared Boundary.

This package contains the Pydantic data models, state enumerations, and configuration
schemas that form the strict API contract between the core monitoring daemon (Engine)
and the web interface (Control Plane).
"""

from .constants import (
    COMPOSE_PROJECT_LABEL,
    COMPOSE_SERVICE_LABEL,
    DIFF_FIELDS,
    WARDEN_IGNORE_LABEL,
    WARDEN_MANAGED_BY_LABEL,
    AuditAction,
    BaselineSource,
    DriftStatus,
    WhitelistTargetType,
)
from .exceptions import (
    DatabaseWriteError,
    DockerConnectionError,
    DriftWardenError,
    GitBaselineFetchError,
    RogueContainerKillError,
    YAMLMutationError,
)
from .models import (
    ActionResult,
    AdoptionRequest,
    AuditLogEntry,
    BaselineInfo,
    ContainerState,
    DriftRecord,
    FieldDiff,
    HostSnapshot,
    ParsedService,
    PendingAdoption,
    RawDockerEvent,
    WhitelistEntry,
    WhitelistRequest,
)

__all__ = [
    # Constants & Enums
    "COMPOSE_PROJECT_LABEL",
    "COMPOSE_SERVICE_LABEL",
    "DIFF_FIELDS",
    "WARDEN_IGNORE_LABEL",
    "WARDEN_MANAGED_BY_LABEL",
    "DriftStatus",
    "AuditAction",
    "BaselineSource",
    "WhitelistTargetType",
    # Exceptions
    "DriftWardenError",
    "GitBaselineFetchError",
    "RogueContainerKillError",
    "YAMLMutationError",
    "DockerConnectionError",
    "DatabaseWriteError",
    # Models
    "RawDockerEvent",
    "ParsedService",
    "FieldDiff",
    "ContainerState",
    "DriftRecord",
    "HostSnapshot",
    "BaselineInfo",
    "PendingAdoption",
    "AdoptionRequest",
    "WhitelistRequest",
    "WhitelistEntry",
    "AuditLogEntry",
    "ActionResult",
]
