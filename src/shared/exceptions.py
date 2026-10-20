"""Custom exception hierarchy for DriftWarden."""


class DriftWardenError(Exception):
    """Base class for expected DriftWarden application errors.

    These exceptions represent operational failures that can be caught globally
    by the FastAPI control plane and converted into structured error responses.
    """


class ConfigurationError(DriftWardenError):
    """Raised when required configuration cannot be resolved at runtime."""


class GitBaselineFetchError(DriftWardenError):
    """Raised when the declarative Docker Compose baseline cannot be fetched."""


class BaselineParseError(DriftWardenError):
    """Raised when the fetched baseline is not a valid Compose document."""


class RogueContainerKillError(DriftWardenError):
    """Raised when enforcement cannot terminate a rogue container."""


class ContainerRecreateError(DriftWardenError):
    """Raised when a service cannot be recreated from its baseline definition."""


class YAMLMutationError(DriftWardenError):
    """Raised when a Compose YAML abstract syntax tree cannot be mutated safely."""


class AdoptionError(DriftWardenError):
    """Raised when an adoption request cannot be turned into a pull request."""


class DockerConnectionError(DriftWardenError):
    """Raised when DriftWarden cannot connect to the Docker daemon."""


class DatabaseWriteError(DriftWardenError):
    """Raised when DriftWarden cannot persist state to its SQLite database."""
