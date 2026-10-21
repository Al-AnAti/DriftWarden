"""Validated environment configuration for the DriftWarden process.

``WardenSettings`` is the single settings object for the engine and the web
control plane alike; nothing else reads environment variables directly.
"""

import socket
from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from shared.constants import DEFAULT_EXCLUDED_NAME_PREFIXES

DEFAULT_MOCK_BASELINE_PATH = "tests/fixtures/docker-compose.test.yaml"
"""Local fixture loaded when ``ALLOW_MOCK_BASELINE=true``."""


class WardenSettings(BaseSettings):
    """Application settings loaded from environment variables and ``.env``.

    GitHub integration is mandatory during normal operation. Local tests that
    replace baseline loading with a mock may explicitly set
    ``ALLOW_MOCK_BASELINE=true`` to bypass those GitHub checks.

    Attributes:
        github_pat: GitHub personal access token, masked by Pydantic.
        target_repo: GitHub repository containing the Compose baseline.
        target_branch: Branch containing the baseline.
        baseline_file_path: Repository-relative path to the Compose file.
        baseline_poll_interval_seconds: How often the baseline is re-polled.
        docker_socket_path: Local Docker daemon socket path.
        docker_base_url: Explicit Docker API URL overriding ``docker.from_env()``.
        compose_namespace: Fallback Compose namespace when the baseline has no name.
        warden_mode: Guardrail mode; only ``observe`` and ``enforce`` are valid.
        drift_debounce_seconds: Global quiet period used to coalesce Docker events.
        docker_event_reconnect_delay: Delay before reconnecting to Docker.
        reconcile_sweep_interval_seconds: Periodic full reconcile interval.
        enforcement_grace_seconds: Enforcement suppression after a baseline change.
        adoption_buffer_ttl_seconds: Fallback buffer TTL for entries with no pull
            request to follow; entries backed by a PR stay buffered until it is
            merged or closed.
        excluded_name_prefixes: Container name prefixes never evaluated.
        api_host: Host address used by the control plane.
        api_port: Port used by the control plane.
        environment: Deployment environment name.
        log_level: Logging threshold consumed by application startup.
        warden_hostname: Friendly host name shown in the UI and audit rows.
        database_url: SQLite or compatible database URL.
        allow_mock_baseline: Explicitly permits mocked baseline loading locally.
        mock_baseline_path: Fixture path used when the baseline is mocked.
    """

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
        validate_assignment=True,
    )

    github_pat: SecretStr | None = Field(default=None, validation_alias="GITHUB_PAT")
    target_repo: str | None = Field(default=None, validation_alias="TARGET_REPO")
    target_branch: str | None = Field(default=None, validation_alias="TARGET_BRANCH")
    baseline_file_path: str = Field(
        default="docker-compose.yml",
        min_length=1,
        validation_alias="BASELINE_FILE_PATH",
    )
    baseline_poll_interval_seconds: int = Field(
        default=30,
        ge=5,
        validation_alias="BASELINE_POLL_INTERVAL_SECONDS",
    )

    docker_socket_path: str = Field(
        default="/var/run/docker.sock",
        min_length=1,
        validation_alias="DOCKER_SOCKET_PATH",
    )
    docker_base_url: str | None = Field(default=None, validation_alias="DOCKER_BASE_URL")
    compose_namespace: str | None = Field(
        default=None,
        validation_alias="COMPOSE_NAMESPACE",
    )
    warden_mode: Literal["observe", "enforce"] = Field(
        default="observe",
        validation_alias="WARDEN_MODE",
    )
    drift_debounce_seconds: float = Field(
        default=2,
        ge=0,
        validation_alias="DRIFT_DEBOUNCE_SECONDS",
    )
    docker_event_reconnect_delay: int = Field(
        default=5,
        ge=0,
        validation_alias="DOCKER_EVENT_RECONNECT_DELAY",
    )
    reconcile_sweep_interval_seconds: int = Field(
        default=60,
        ge=5,
        validation_alias="RECONCILE_SWEEP_INTERVAL_SECONDS",
    )
    enforcement_grace_seconds: int = Field(
        default=30,
        ge=0,
        validation_alias="ENFORCEMENT_GRACE_SECONDS",
    )
    adoption_buffer_ttl_seconds: int = Field(
        default=180,
        ge=0,
        validation_alias="ADOPTION_BUFFER_TTL_SECONDS",
    )
    excluded_name_prefixes: str = Field(
        default=",".join(DEFAULT_EXCLUDED_NAME_PREFIXES),
        validation_alias="EXCLUDED_NAME_PREFIXES",
    )

    api_host: str = Field(default="127.0.0.1", min_length=1, validation_alias="API_HOST")
    api_port: int = Field(default=8000, ge=1, le=65535, validation_alias="API_PORT")
    environment: Literal["development", "production"] = Field(
        default="development",
        validation_alias="ENVIRONMENT",
    )
    log_level: str = Field(default="INFO", min_length=1, validation_alias="LOG_LEVEL")
    warden_hostname: str | None = Field(default=None, validation_alias="WARDEN_HOSTNAME")
    database_url: str = Field(
        default="sqlite:///./warden.db",
        min_length=1,
        validation_alias="DATABASE_URL",
    )
    allow_mock_baseline: bool = Field(
        default=False,
        validation_alias="ALLOW_MOCK_BASELINE",
    )
    mock_baseline_path: str = Field(
        default=DEFAULT_MOCK_BASELINE_PATH,
        min_length=1,
        validation_alias="MOCK_BASELINE_PATH",
    )

    @field_validator(
        "target_repo",
        "target_branch",
        "baseline_file_path",
        "docker_socket_path",
        "docker_base_url",
        "compose_namespace",
        "api_host",
        "log_level",
        "warden_hostname",
        "database_url",
        "mock_baseline_path",
        mode="before",
    )
    @classmethod
    def reject_blank_strings(cls, value: str | None, info: ValidationInfo) -> str | None:
        """Reject whitespace-only configuration values.

        Args:
            value: Raw value supplied by the environment.
            info: Pydantic validation context for the field being validated.

        Returns:
            The original non-blank value.

        Raises:
            ValueError: If a supplied string contains no non-whitespace text.
        """
        if value is not None and not value.strip():
            raise ValueError(f"{info.field_name} must not be blank")
        return value

    @model_validator(mode="after")
    def validate_github_configuration(self) -> "WardenSettings":
        """Require GitHub settings unless baseline loading is explicitly mocked.

        Returns:
            The validated settings instance.

        Raises:
            ValueError: If required GitHub settings are absent in normal mode.
        """
        if self.allow_mock_baseline:
            return self

        missing: list[str] = []
        if self.github_pat is None:
            missing.append("GITHUB_PAT")
        if self.target_repo is None:
            missing.append("TARGET_REPO")
        if self.target_branch is None:
            missing.append("TARGET_BRANCH")

        if missing:
            missing_variables = ", ".join(missing)
            raise ValueError(
                f"Missing required GitHub configuration: {missing_variables}. "
                "Set ALLOW_MOCK_BASELINE=true only when baseline loading is mocked."
            )
        return self

    @property
    def hostname(self) -> str:
        """Friendly host name: ``WARDEN_HOSTNAME`` or the machine hostname."""
        return self.warden_hostname or socket.gethostname()

    @property
    def excluded_prefixes(self) -> tuple[str, ...]:
        """Container name prefixes that are never evaluated."""
        return tuple(p.strip() for p in self.excluded_name_prefixes.split(",") if p.strip())

    @property
    def enforce(self) -> bool:
        """Whether autonomous enforcement is configured."""
        return self.warden_mode == "enforce"

    def __str__(self) -> str:
        """Return a secret-safe human-readable settings summary."""
        return (
            f"WardenSettings(warden_mode={self.warden_mode!r}, "
            f"target_repo={self.target_repo!r}, target_branch={self.target_branch!r}, "
            f"baseline_file_path={self.baseline_file_path!r}, "
            f"allow_mock_baseline={self.allow_mock_baseline!r})"
        )

    def __repr__(self) -> str:
        """Return a secret-safe representation suitable for logs and debugging."""
        return self.__str__()


@lru_cache(maxsize=1)
def get_settings() -> WardenSettings:
    """Load and cache validated Warden settings.

    Returns:
        The process-wide validated settings instance.

    Raises:
        pydantic.ValidationError: If environment configuration is invalid.
    """
    return WardenSettings()
