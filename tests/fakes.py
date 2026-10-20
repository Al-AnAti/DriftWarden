"""Test doubles for the Docker watcher and GitHub client."""

import asyncio
import threading
from collections.abc import Callable
from typing import Any

from shared.exceptions import DockerConnectionError
from shared.models import ParsedService, RawDockerEvent
from warden.automation.github_client import AdoptionPullRequest, FetchedBaseline
from warden.config import WardenSettings
from warden.core.docker_watcher import LiveContainer


def container_from_service(service: ParsedService, project: str) -> LiveContainer:
    """Build the container ``recreate`` would produce for a baseline service."""
    return LiveContainer(
        id=("r" + service.service_name).ljust(64, "0"),
        name=service.container_name or f"{project}-{service.service_name}-1",
        image=service.image or "",
        image_id="sha256:" + "e" * 64,
        state="running",
        created="2026-10-20T12:00:00Z",
        labels={
            **service.labels,
            "com.docker.compose.project": project,
            "com.docker.compose.service": service.service_name,
            "warden.managed-by": "driftwarden",
        },
        environment=dict(service.environment),
        ports=list(service.ports),
        restart=service.restart or "no",
        networks=[f"{project}_{n}" for n in service.networks] or [f"{project}_default"],
    )


class FakeWatcher:
    """In-memory stand-in for ``DockerWatcher``.

    Attributes:
        containers: What ``list_containers`` returns; mutate it between cycles.
        removed: Container ids passed to ``stop_and_remove``.
        recreated: ``(service_name, project, action_id)`` tuples from ``recreate``.
        fail_listing: Raise ``DockerConnectionError`` from ``list_containers``.
    """

    def __init__(self, settings: WardenSettings | None = None) -> None:
        self.settings = settings
        self.containers: list[LiveContainer] = []
        self.removed: list[str] = []
        self.recreated: list[tuple[str, str, str]] = []
        self.fail_listing = False
        self.self_container_id: str | None = None
        self.connected = True
        self.last_error: str | None = None
        self.on_recreate: Callable[[ParsedService, str], LiveContainer] | None = None

    def list_containers(self) -> list[LiveContainer]:
        if self.fail_listing:
            raise DockerConnectionError("daemon down")
        return list(self.containers)

    def inspect(self, id_or_name: str) -> LiveContainer | None:
        for container in self.containers:
            if container.id == id_or_name or container.name == id_or_name:
                return container
        return None

    def image_default_environment(self, image_ref: str) -> dict[str, str]:
        return {"PATH": "/usr/bin"}

    def image_default_labels(self, image_ref: str) -> dict[str, str]:
        return {}

    def stop_and_remove(self, container_id: str, timeout: int = 10) -> None:
        self.removed.append(container_id)
        self.containers = [c for c in self.containers if c.id != container_id]

    def recreate(
        self,
        service: ParsedService,
        project: str,
        *,
        action_id: str,
        declared_volumes: set[str] | None = None,
    ) -> str:
        self.recreated.append((service.service_name, project, action_id))
        name = service.container_name or f"{project}-{service.service_name}-1"
        self.containers = [c for c in self.containers if c.name != name]
        if self.on_recreate is not None:
            self.containers.append(self.on_recreate(service, project))
        return "new" + service.service_name.ljust(61, "0")

    async def pump_events(
        self, queue: "asyncio.Queue[RawDockerEvent]", stop_event: threading.Event
    ) -> None:
        while not stop_event.is_set():
            await asyncio.sleep(0.05)

    def close(self) -> None:
        self.connected = False


class FakeGitHub:
    """Stand-in for ``GitHubClient`` serving a baseline from memory."""

    def __init__(self, content: str, sha: str = "sha-1") -> None:
        self.content = content
        self.sha = sha
        self.fail = False
        self.fetches = 0
        self.pulls: list[dict[str, Any]] = []
        self.mutated: str | None = None

    def fetch_baseline(self, etag: str | None = None) -> FetchedBaseline | None:
        self.fetches += 1
        if self.fail:
            from shared.exceptions import GitBaselineFetchError

            raise GitBaselineFetchError("401 Bad credentials")
        if etag == self.sha:
            return None
        return FetchedBaseline(content=self.content, sha=self.sha, etag=self.sha)

    def find_open_adoption_pr(self, container_name: str) -> AdoptionPullRequest | None:
        return None

    def open_adoption_pr(
        self,
        *,
        container_name: str,
        service_name: str,
        mutate: Callable[[str], str],
        title: str,
        body: str,
    ) -> AdoptionPullRequest:
        self.mutated = mutate(self.content)
        self.pulls.append(
            {"container": container_name, "service": service_name, "title": title, "body": body}
        )
        return AdoptionPullRequest(
            number=len(self.pulls),
            url=f"https://github.test/pull/{len(self.pulls)}",
            branch="warden/adopt-x",
        )
