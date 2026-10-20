"""Shared fixtures: fixture baseline, container factory, isolated settings."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest

from shared.constants import BaselineSource
from warden.config import WardenSettings, get_settings
from warden.core.baseline_parser import Baseline, parse_baseline
from warden.core.docker_watcher import LiveContainer, MountInfo

FIXTURES = Path(__file__).parent / "fixtures"
PROJECT = "infrastructure-test"


@pytest.fixture(autouse=True)
def isolated_settings(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Every test gets mock-baseline settings and a throwaway SQLite file."""
    for name in (
        "GITHUB_PAT",
        "TARGET_REPO",
        "TARGET_BRANCH",
        "COMPOSE_NAMESPACE",
        "DOCKER_BASE_URL",
        "WARDEN_HOSTNAME",
    ):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("ALLOW_MOCK_BASELINE", "true")
    monkeypatch.setenv("MOCK_BASELINE_PATH", str(FIXTURES / "docker-compose.test.yaml"))
    monkeypatch.setenv("WARDEN_MODE", "observe")
    monkeypatch.setenv("WARDEN_HOSTNAME", "test-host")
    monkeypatch.setenv("DATABASE_URL", f"sqlite:///{(tmp_path / 'warden-test.db').as_posix()}")
    monkeypatch.setenv("DRIFT_DEBOUNCE_SECONDS", "0")
    monkeypatch.setenv("ENFORCEMENT_GRACE_SECONDS", "0")
    monkeypatch.setenv("BASELINE_POLL_INTERVAL_SECONDS", "5")
    monkeypatch.setenv("RECONCILE_SWEEP_INTERVAL_SECONDS", "5")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


@pytest.fixture
def settings() -> WardenSettings:
    """Fresh settings built from the isolated environment."""
    return WardenSettings(_env_file=None)  # type: ignore[call-arg]


@pytest.fixture
def fixture_yaml() -> str:
    """Raw text of the fixture Compose file."""
    return (FIXTURES / "docker-compose.test.yaml").read_text(encoding="utf-8")


@pytest.fixture
def baseline(fixture_yaml: str) -> Baseline:
    """Parsed fixture baseline (project ``infrastructure-test``, services web and cache)."""
    return parse_baseline(
        fixture_yaml, fallback_namespace=None, sha="fixture-sha", source=BaselineSource.MOCK
    )


def make_container(**overrides: Any) -> LiveContainer:
    """Build a running container with sensible defaults."""
    base: dict[str, Any] = {
        "id": "0" * 64,
        "name": "container",
        "image": "busybox:latest",
        "image_id": "sha256:" + "f" * 64,
        "state": "running",
        "created": "2026-10-20T00:00:00Z",
        "labels": {},
        "environment": {},
        "ports": [],
        "mounts": [],
        "restart": "no",
        "networks": [f"{PROJECT}_default"],
    }
    base.update(overrides)
    return LiveContainer(**base)


def compose_labels(service: str, project: str = PROJECT) -> dict[str, str]:
    """Labels Compose puts on a service container."""
    return {
        "com.docker.compose.project": project,
        "com.docker.compose.service": service,
        "com.docker.compose.container-number": "1",
    }


@pytest.fixture
def web_container() -> LiveContainer:
    """A container matching the fixture ``web`` service exactly."""
    return make_container(
        id="a" * 64,
        name=f"{PROJECT}-web-1",
        image="nginx:alpine",
        labels={**compose_labels("web"), "warden.managed": "true", "warden.repo": "my-cs-project"},
        ports=["8080:80/tcp"],
    )


@pytest.fixture
def cache_container() -> LiveContainer:
    """A container matching the fixture ``cache`` service exactly."""
    return make_container(
        id="b" * 64,
        name=f"{PROJECT}-cache-1",
        image="redis:alpine",
        labels={
            **compose_labels("cache"),
            "warden.managed": "true",
            "warden.repo": "my-cs-project",
        },
        ports=["6379:6379/tcp"],
    )


@pytest.fixture
def drifted_cache() -> LiveContainer:
    """The demo's spoofed cache: wrong image, port and network."""
    return make_container(
        id="c" * 64,
        name=f"{PROJECT}-cache-1",
        image="redis:latest",
        labels={
            **compose_labels("cache"),
            "warden.managed": "true",
            "warden.repo": "my-cs-project",
        },
        ports=["6378:6378/tcp"],
        networks=["bridge"],
    )


@pytest.fixture
def rogue_container() -> LiveContainer:
    """The demo's unlabelled MariaDB."""
    return make_container(
        id="d" * 64,
        name="rogue-db",
        image="mariadb:latest",
        ports=["3306:3306/tcp"],
        networks=["bridge"],
        environment={"MARIADB_ROOT_PASSWORD": "rogue", "PATH": "/usr/bin"},
        mounts=[
            MountInfo(kind="volume", source="0" * 64, target="/var/lib/mysql", read_only=False)
        ],
    )
