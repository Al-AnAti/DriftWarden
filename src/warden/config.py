"""Validated environment configuration for the DriftWarden Warden process."""

from functools import lru_cache
from typing import Literal

from pydantic import Field, SecretStr, ValidationInfo, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


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
        docker_socket_path: Local Docker daemon socket path.
        compose_namespace: Compose project namespace monitored by Warden.
        warden_mode: Guardrail mode; only ``observe`` and ``enforce`` are valid.
        drift_debounce_seconds: Quiet period used to coalesce Docker events.
        docker_event_reconnect_delay: Delay before reconnecting to Docker.
        adoption_buffer_ttl_seconds: Pending-adoption suppression duration.
        api_host: Host address used by the control plane.
        api_port: Port used by the control plane.
        environment: Deployment environment name.
        log_level: Logging threshold consumed by application startup.
        database_url: SQLite or compatible database URL.
        allow_mock_baseline: Explicitly permits mocked baseline loading locally.
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

    docker_socket_path: str = Field(
        default="/var/run/docker.sock",
        min_length=1,
        validation_alias="DOCKER_SOCKET_PATH",
    )
    compose_namespace: str | None = Field(
        default=None,
        validation_alias="COMPOSE_NAMESPACE",
    )
    warden_mode: Literal["observe", "enforce"] = Field(
        default="observe",
        validation_alias="WARDEN_MODE",
    )
    drift_debounce_seconds: int = Field(
        default=2,
        ge=0,
        validation_alias="DRIFT_DEBOUNCE_SECONDS",
    )
    docker_event_reconnect_delay: int = Field(
        default=5,
        ge=0,
        validation_alias="DOCKER_EVENT_RECONNECT_DELAY",
    )
    adoption_buffer_ttl_seconds: int = Field(
        default=180,
        ge=0,
        validation_alias="ADOPTION_BUFFER_TTL_SECONDS",
    )

    api_host: str = Field(default="127.0.0.1", min_length=1, validation_alias="API_HOST")
    api_port: int = Field(default=8000, ge=1, le=65535, validation_alias="API_PORT")
    environment: Literal["development", "production"] = Field(
        default="development",
        validation_alias="ENVIRONMENT",
    )
    log_level: str = Field(default="INFO", min_length=1, validation_alias="LOG_LEVEL")
    database_url: str = Field(
        default="sqlite:///./warden.db",
        min_length=1,
        validation_alias="DATABASE_URL",
    )
    allow_mock_baseline: bool = Field(
        default=False,
        validation_alias="ALLOW_MOCK_BASELINE",
    )

    @field_validator(
        "target_repo",
        "target_branch",
        "baseline_file_path",
        "docker_socket_path",
        "compose_namespace",
        "api_host",
        "log_level",
        "database_url",
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
