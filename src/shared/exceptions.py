"""Custom exception hierarchy for DriftWarden."""


class DriftWardenError(Exception):
    """Base class for expected DriftWarden application errors.

    These exceptions represent operational failures that can be caught globally
    by the FastAPI control plane and converted into structured error responses.
    """


class GitBaselineFetchError(DriftWardenError):
    """Raised when the declarative Docker Compose baseline cannot be fetched."""


class RogueContainerKillError(DriftWardenError):
    """Raised when enforcement cannot terminate a rogue container."""


class YAMLMutationError(DriftWardenError):
    """Raised when a Compose YAML abstract syntax tree cannot be mutated safely."""


class DockerConnectionError(DriftWardenError):
    """Raised when DriftWarden cannot connect to the Docker daemon."""


class DatabaseWriteError(DriftWardenError):
    """Raised when DriftWarden cannot persist state to its SQLite database."""
