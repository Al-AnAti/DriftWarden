"""Inspect-JSON parsing and docker-py argument translation."""

import asyncio
import threading
from typing import Any

import pytest

from shared.models import ParsedService, RawDockerEvent
from warden.config import WardenSettings
from warden.core.docker_watcher import (
    STATE_CHANGING_EVENTS,
    DockerWatcher,
    container_from_attrs,
    ports_to_docker,
    restart_policy_to_docker,
    volumes_to_docker,
)


def inspect_payload() -> dict[str, Any]:
    """A trimmed ``docker inspect`` document for a published redis container."""
    return {
        "Id": "c" * 64,
        "Name": "/infrastructure-test-cache-1",
        "Image": "sha256:" + "1" * 64,
        "Created": "2026-10-20T10:00:00Z",
        "State": {"Status": "running"},
        "Config": {
            "Image": "redis:latest",
            "Env": ["PATH=/usr/bin", "REDIS_VERSION=7"],
            "Labels": {
                "com.docker.compose.project": "infrastructure-test",
                "com.docker.compose.service": "cache",
            },
            "Cmd": ["redis-server"],
        },
        "HostConfig": {
            "PortBindings": {
                "6379/tcp": [{"HostIp": "", "HostPort": "6378"}],
                "9000/udp": [{"HostIp": "127.0.0.1", "HostPort": "9000"}],
                "7000/tcp": [{"HostIp": "", "HostPort": ""}],
            },
            "RestartPolicy": {"Name": "on-failure", "MaximumRetryCount": 3},
        },
        "Mounts": [
            {
                "Type": "volume",
                "Name": "infrastructure-test_data",
                "Source": "/var/lib/docker/...",
                "Destination": "/data",
                "RW": True,
            },
            {"Type": "bind", "Source": "/srv/conf", "Destination": "/etc/redis", "RW": False},
        ],
        "NetworkSettings": {"Networks": {"bridge": {}, "infrastructure-test_default": {}}},
    }


def test_container_from_attrs_normalises_everything() -> None:
    container = container_from_attrs(inspect_payload())
    assert container.name == "infrastructure-test-cache-1"
    assert container.short_id == "c" * 12
    assert container.image == "redis:latest"
    assert container.state == "running"
    assert container.compose_project == "infrastructure-test"
    assert container.compose_service == "cache"
    assert container.environment == {"PATH": "/usr/bin", "REDIS_VERSION": "7"}
    assert container.ports == ["6378:6379/tcp", "7000/tcp", "127.0.0.1:9000:9000/udp"]
    assert container.restart == "on-failure:3"
    assert container.networks == ["bridge", "infrastructure-test_default"]
    assert container.command == "redis-server"
    assert container.volumes("infrastructure-test") == ["data:/data:rw", "/srv/conf:/etc/redis:ro"]
    assert container.volumes(None) == [
        "infrastructure-test_data:/data:rw",
        "/srv/conf:/etc/redis:ro",
    ]


def test_container_from_attrs_tolerates_sparse_payload() -> None:
    container = container_from_attrs({"Id": "x", "Name": "/bare"})
    assert container.name == "bare"
    assert container.state == "unknown"
    assert container.restart == "no"
    assert container.ports == []
    assert container.command is None


def test_ports_to_docker() -> None:
    assert ports_to_docker(["8080:80/tcp", "127.0.0.1:53:53/udp"]) == {
        "80/tcp": 8080,
        "53/udp": ("127.0.0.1", 53),
    }
    assert ports_to_docker(["80/tcp"]) == {"80/tcp": None}
    assert ports_to_docker(["8080:80/tcp", "8081:80/tcp"]) == {"80/tcp": [8080, 8081]}


@pytest.mark.parametrize(
    ("restart", "expected"),
    [
        (None, {"Name": "no"}),
        ("no", {"Name": "no"}),
        ("always", {"Name": "always"}),
        ("on-failure:5", {"Name": "on-failure", "MaximumRetryCount": 5}),
        ("unless-stopped", {"Name": "unless-stopped"}),
    ],
)
def test_restart_policy_to_docker(restart: str | None, expected: dict[str, Any]) -> None:
    assert restart_policy_to_docker(restart) == expected


def test_volumes_to_docker_prefixes_named_and_skips_relative() -> None:
    binds, anonymous = volumes_to_docker(
        ["data:/data:rw", "/srv/conf:/etc/redis:ro", "./local:/l:rw", ":/anon:rw"],
        "proj",
        {"data"},
    )
    assert binds == {
        "proj_data": {"bind": "/data", "mode": "rw"},
        "/srv/conf": {"bind": "/etc/redis", "mode": "ro"},
    }
    assert anonymous == ["/anon"]


def test_raw_event_extract_reads_actor_attributes() -> None:
    event = RawDockerEvent.extract(
        {"id": "abc", "Action": "start", "Actor": {"Attributes": {"name": "web", "image": "nginx"}}}
    )
    assert event.container_id == "abc"
    assert event.action == "start"
    assert event.name == "web"
    assert "exec_create" not in STATE_CHANGING_EVENTS and "die" in STATE_CHANGING_EVENTS


class FakeStream:
    """Stands in for docker's blocking event generator."""

    def __init__(self, payloads: list[Any]) -> None:
        self._payloads = payloads
        self.closed = False

    def __iter__(self) -> Any:
        yield from self._payloads

    def close(self) -> None:
        self.closed = True


class FakeClient:
    def __init__(self, payloads: list[Any]) -> None:
        self.stream = FakeStream(payloads)

    def events(self, **_: Any) -> FakeStream:
        return self.stream


async def test_pump_filters_events_and_hands_them_to_the_loop(settings: WardenSettings) -> None:
    watcher = DockerWatcher(settings)
    payloads = [
        {"id": "1", "Action": "start", "Actor": {"Attributes": {"name": "a"}}},
        {"id": "2", "Action": "exec_create: sh", "Actor": {"Attributes": {"name": "a"}}},
        "garbage",
        {"id": "3", "Action": "health_status: unhealthy", "Actor": {"Attributes": {}}},
        {"id": "4", "Action": "die", "Actor": {"Attributes": {"name": "b"}}},
    ]
    client = FakeClient(payloads)
    watcher.connect = lambda: client  # type: ignore[method-assign]
    queue: asyncio.Queue[RawDockerEvent] = asyncio.Queue()
    stop = threading.Event()
    loop = asyncio.get_running_loop()
    await loop.run_in_executor(None, watcher._stream_events, loop, queue, stop)
    await asyncio.sleep(0.05)
    received = [queue.get_nowait() for _ in range(queue.qsize())]
    assert [e.container_id for e in received] == ["1", "4"]
    assert client.stream.closed


def test_recreate_requires_an_image(settings: WardenSettings) -> None:
    from shared.exceptions import ContainerRecreateError

    watcher = DockerWatcher(settings)
    with pytest.raises(ContainerRecreateError):
        watcher.recreate(ParsedService(service_name="x"), "proj", action_id="a")
