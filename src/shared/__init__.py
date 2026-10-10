"""
DriftWarden Shared Boundary.

This package contains the Pydantic data models, state enumerations, and configuration 
schemas that form the strict API contract between the core monitoring daemon (Engine) 
and the web interface (Control Plane).
"""

from .constants import AuditAction, COMPOSE_PROJECT_LABEL, DriftStatus
from .exceptions import (
    DatabaseWriteError,
    DockerConnectionError,
    DriftWardenError,
    GitBaselineFetchError,
    RogueContainerKillError,
    YAMLMutationError,
)
from .models import (
    AdoptionRequest,
    AuditLogEntry,
    ContainerState,
    ParsedService,
    RawDockerEvent,
)

__all__ = [
    # Constants & Enums
    "COMPOSE_PROJECT_LABEL",
    "DriftStatus",
    "AuditAction",
    
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
    "ContainerState",
    "AdoptionRequest",
    "AuditLogEntry",
]