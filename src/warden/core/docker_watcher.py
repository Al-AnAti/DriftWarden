"""Docker daemon access: event stream, container inspection and remediation.

The blocking ``docker.events`` generator runs on a worker thread via
``run_in_executor`` and hands state-changing events to an ``asyncio.Queue``
on the main loop with ``call_soon_threadsafe``. Everything else is a plain
synchronous call that the engine wraps in ``asyncio.to_thread``.
"""

import asyncio
import os
import re
import threading
from dataclasses import dataclass, field
from typing import Any

import docker
from docker.errors import APIError, DockerException, ImageNotFound, NotFound
from loguru import logger

from shared.constants import (
    COMPOSE_CONTAINER_NUMBER_LABEL,
    COMPOSE_PROJECT_LABEL,
    COMPOSE_SERVICE_LABEL,
    WARDEN_ACTION_LABEL,
    WARDEN_MANAGED_BY_LABEL,
    WARDEN_MANAGED_BY_VALUE,
)
from shared.exceptions import ContainerRecreateError, DockerConnectionError, RogueContainerKillError
from shared.models import ParsedService, RawDockerEvent
from warden.config import WardenSettings

STATE_CHANGING_EVENTS: frozenset[str] = frozenset(
    {
        "create",
        "start",
        "restart",
        "die",
        "stop",
        "kill",
        "oom",
        "pause",
        "unpause",
        "destroy",
        "rename",
        "update",
    }
)
"""Docker container event actions that can change reconciled state."""

_CONTAINER_ID_RE = re.compile(r"([0-9a-f]{64})")


@dataclass(slots=True, frozen=True)
class MountInfo:
    """One mount of a live container.

    Attributes:
        kind: ``bind``, ``volume`` or ``tmpfs``.
        source: Host path for binds, volume name for named volumes.
        target: Mount point inside the container.
        read_only: Whether the mount is read-only.
    """

    kind: str
    source: str
    target: str
    read_only: bool

    def canonical(self, project: str | None) -> str:
        """Render as ``source:target:mode`` with the project prefix stripped."""
        source = self.source
        if self.kind == "volume" and project and source.startswith(f"{project}_"):
            source = source[len(project) + 1 :]
        if self.kind == "volume" and len(source) == 64 and _CONTAINER_ID_RE.fullmatch(source):
            source = ""  # anonymous volume
        return f"{source}:{self.target}:{'ro' if self.read_only else 'rw'}"


@dataclass(slots=True)
class LiveContainer:
    """Fully inspected container state used by the diff engine.

    Attributes:
        id: Full container id.
        name: Container name without the leading slash.
        image: Image reference the container was created from.
        image_id: Image content id.
        state: Docker state (``running``, ``exited``, ``paused`` ...).
        created: Creation timestamp as reported by Docker.
        labels: All container labels.
        environment: Environment variables from ``Config.Env``.
        ports: Canonical published port bindings.
        mounts: All mounts.
        restart: Restart policy name (``no`` when unset).
        networks: Names of the networks the container is attached to.
        command: Command line, for display only.
    """

    id: str
    name: str
    image: str
    image_id: str
    state: str
    created: str
    labels: dict[str, str] = field(default_factory=dict)
    environment: dict[str, str] = field(default_factory=dict)
    ports: list[str] = field(default_factory=list)
    mounts: list[MountInfo] = field(default_factory=list)
    restart: str = "no"
    networks: list[str] = field(default_factory=list)
    command: str | None = None

    @property
    def compose_project(self) -> str | None:
        """Compose project label, if any."""
        return self.labels.get(COMPOSE_PROJECT_LABEL)

    @property
    def compose_service(self) -> str | None:
        """Compose service label, if any."""
        return self.labels.get(COMPOSE_SERVICE_LABEL)

    @property
    def short_id(self) -> str:
        """Twelve-character id used in logs and the UI."""
        return self.id[:12]

    def volumes(self, project: str | None) -> list[str]:
        """Canonical volume strings for the diff engine."""
        return [m.canonical(project) for m in self.mounts if m.kind in ("bind", "volume")]


def container_from_attrs(attrs: dict[str, Any]) -> LiveContainer:
    """Build a ``LiveContainer`` from ``docker inspect`` JSON.

    Args:
        attrs: The inspect payload.

    Returns:
        The normalised container record.
    """
    config = attrs.get("Config") or {}
    host_config = attrs.get("HostConfig") or {}
    state = attrs.get("State") or {}
    network_settings = attrs.get("NetworkSettings") or {}

    environment: dict[str, str] = {}
    for entry in config.get("Env") or []:
        key, _, value = str(entry).partition("=")
        environment[key] = value

    ports: list[str] = []
    for container_port, bindings in sorted((host_config.get("PortBindings") or {}).items()):
        port, _, proto = str(container_port).partition("/")
        proto = proto or "tcp"
        if not bindings:
            ports.append(f"{port}/{proto}")
            continue
        for binding in bindings:
            host_ip = str(binding.get("HostIp") or "")
            host_port = str(binding.get("HostPort") or "")
            if host_ip in ("0.0.0.0", "::"):
                host_ip = ""
            if not host_port:
                ports.append(f"{port}/{proto}")
            elif host_ip:
                ports.append(f"{host_ip}:{host_port}:{port}/{proto}")
            else:
                ports.append(f"{host_port}:{port}/{proto}")

    mounts = [
        MountInfo(
            kind=str(m.get("Type") or "bind"),
            source=str(m.get("Name") or m.get("Source") or ""),
            target=str(m.get("Destination") or ""),
            read_only=not bool(m.get("RW", True)),
        )
        for m in attrs.get("Mounts") or []
    ]

    restart_policy = host_config.get("RestartPolicy") or {}
    restart = str(restart_policy.get("Name") or "no")
    retries = int(restart_policy.get("MaximumRetryCount") or 0)
    if restart == "on-failure" and retries:
        restart = f"on-failure:{retries}"

    command_parts = [str(part) for part in (config.get("Cmd") or [])]
    return LiveContainer(
        id=str(attrs.get("Id", "")),
        name=str(attrs.get("Name", "")).lstrip("/"),
        image=str(config.get("Image") or ""),
        image_id=str(attrs.get("Image") or ""),
        state=str(state.get("Status") or "unknown"),
        created=str(attrs.get("Created") or ""),
        labels={str(k): str(v) for k, v in (config.get("Labels") or {}).items()},
        environment=environment,
        ports=ports,
        mounts=mounts,
        restart=restart,
        networks=sorted(str(n) for n in (network_settings.get("Networks") or {})),
        command=" ".join(command_parts) if command_parts else None,
    )


def detect_self_container_id() -> str | None:
    """Find the id of the container DriftWarden itself runs in, if any.

    Returns:
        The 64-character container id, or ``None`` when running on the host.
    """
    for path in ("/proc/self/mountinfo", "/proc/self/cgroup", "/proc/1/cpuset"):
        try:
            with open(path, encoding="utf-8") as handle:
                match = _CONTAINER_ID_RE.search(handle.read())
        except OSError:
            continue
        if match:
            return match.group(1)
    return None


def ports_to_docker(ports: list[str]) -> dict[str, Any]:
    """Convert canonical port strings to the ``docker-py`` ``ports`` mapping.

    Args:
        ports: Canonical ``[host_ip:]host:container/proto`` strings.

    Returns:
        Mapping of ``container/proto`` to host binding(s).
    """
    result: dict[str, Any] = {}
    for entry in ports:
        text, _, proto = entry.rpartition("/")
        if not text:
            text, proto = entry, "tcp"
        parts = text.split(":")
        key = f"{parts[-1]}/{proto or 'tcp'}"
        binding: Any
        if len(parts) == 1:
            binding = None
        elif len(parts) == 2:
            binding = int(parts[0]) if parts[0].isdigit() else parts[0]
        else:
            host_port = parts[-2]
            binding = (":".join(parts[:-2]), int(host_port) if host_port.isdigit() else host_port)
        if key in result:
            existing = result[key]
            result[key] = (
                existing + [binding] if isinstance(existing, list) else [existing, binding]
            )
        else:
            result[key] = binding
    return result


def restart_policy_to_docker(restart: str | None) -> dict[str, Any]:
    """Convert a Compose restart string to the ``docker-py`` restart policy.

    Args:
        restart: ``no``, ``always``, ``unless-stopped``, ``on-failure[:N]`` or ``None``.

    Returns:
        A ``restart_policy`` mapping.
    """
    if not restart or restart == "no":
        return {"Name": "no"}
    name, _, retries = restart.partition(":")
    policy: dict[str, Any] = {"Name": name}
    if name == "on-failure" and retries.isdigit():
        policy["MaximumRetryCount"] = int(retries)
    return policy


def volumes_to_docker(
    volumes: list[str], project: str, declared_volumes: set[str]
) -> tuple[dict[str, dict[str, str]], list[str]]:
    """Convert canonical volume strings to ``docker-py`` arguments.

    Relative bind sources cannot be resolved without the Compose file
    directory and are skipped with a warning.

    Args:
        volumes: Canonical ``source:target:mode`` strings.
        project: Compose project used to prefix named volumes.
        declared_volumes: Top-level volume keys declared in the baseline.

    Returns:
        The ``volumes`` mapping and the list of anonymous volume targets.
    """
    binds: dict[str, dict[str, str]] = {}
    anonymous: list[str] = []
    for entry in volumes:
        source, _, rest = entry.partition(":")
        target, _, mode = rest.rpartition(":")
        if not target:
            target, mode = rest, "rw"
        if not source:
            anonymous.append(target)
            continue
        is_path = source.startswith(("/", "~")) or (len(source) > 1 and source[1] == ":")
        if source.startswith("."):
            logger.warning("Skipping relative bind mount {} (no Compose directory)", entry)
            continue
        if not is_path and (source in declared_volumes or "/" not in source):
            source = f"{project}_{source}"
        binds[source] = {"bind": target, "mode": mode or "rw"}
    return binds, anonymous


class DockerWatcher:
    """Owns the Docker client, the event pump and remediation primitives.

    Args:
        settings: Validated application settings.
    """

    def __init__(self, settings: WardenSettings) -> None:
        self._settings = settings
        self._client: docker.DockerClient | None = None
        self._stream: Any = None
        self._lock = threading.Lock()
        self.connected = False
        self.last_error: str | None = None
        self.self_container_id: str | None = detect_self_container_id()
        if self.self_container_id:
            logger.info("Running inside container {}", self.self_container_id[:12])

    # ------------------------------------------------------------------ client

    def connect(self) -> docker.DockerClient:
        """Return a connected Docker client, creating it on first use.

        Returns:
            The ``docker-py`` client.

        Raises:
            DockerConnectionError: If the daemon cannot be reached.
        """
        with self._lock:
            if self._client is None:
                try:
                    if self._settings.docker_base_url:
                        client = docker.DockerClient(base_url=self._settings.docker_base_url)
                    elif os.path.exists(self._settings.docker_socket_path):
                        client = docker.DockerClient(
                            base_url=f"unix://{self._settings.docker_socket_path}"
                        )
                    else:
                        client = docker.from_env()
                    client.ping()
                except DockerException as exc:
                    self.connected = False
                    self.last_error = str(exc)
                    raise DockerConnectionError(f"Cannot reach the Docker daemon: {exc}") from exc
                self._client = client
                logger.info("Connected to Docker daemon")
            self.connected = True
            self.last_error = None
            return self._client

    def _reset(self, error: str) -> None:
        """Drop the client after a failure so the next call reconnects."""
        with self._lock:
            self._client = None
            self.connected = False
            self.last_error = error

    def close(self) -> None:
        """Stop the event stream and close the client."""
        stream = self._stream
        if stream is not None:
            try:
                stream.close()
            except Exception:  # best effort shutdown
                logger.debug("Event stream close raised; ignoring")
        with self._lock:
            if self._client is not None:
                try:
                    self._client.close()
                except Exception:
                    logger.debug("Docker client close raised; ignoring")
                self._client = None
            self.connected = False

    # ------------------------------------------------------------- inspection

    def list_containers(self) -> list[LiveContainer]:
        """Inspect every container on the host, running or not.

        Returns:
            Fully inspected containers, excluding DriftWarden's own container.

        Raises:
            DockerConnectionError: If the daemon cannot be reached.
        """
        try:
            client = self.connect()
            summaries = client.api.containers(all=True)
            containers: list[LiveContainer] = []
            for summary in summaries:
                container_id = str(summary.get("Id", ""))
                if self.self_container_id and container_id == self.self_container_id:
                    continue
                try:
                    attrs = client.api.inspect_container(container_id)
                except NotFound:
                    continue  # removed between list and inspect
                containers.append(container_from_attrs(attrs))
            return containers
        except DockerConnectionError:
            raise
        except DockerException as exc:
            self._reset(str(exc))
            raise DockerConnectionError(f"Docker listing failed: {exc}") from exc

    def inspect(self, id_or_name: str) -> LiveContainer | None:
        """Inspect one container.

        Args:
            id_or_name: Container id (full or prefix) or name.

        Returns:
            The container, or ``None`` if it does not exist.

        Raises:
            DockerConnectionError: If the daemon cannot be reached.
        """
        try:
            return container_from_attrs(self.connect().api.inspect_container(id_or_name))
        except NotFound:
            return None
        except DockerConnectionError:
            raise
        except DockerException as exc:
            self._reset(str(exc))
            raise DockerConnectionError(f"Docker inspect failed: {exc}") from exc

    def image_default_environment(self, image_ref: str) -> dict[str, str]:
        """Environment variables baked into an image.

        Args:
            image_ref: Image id or reference.

        Returns:
            Mapping of ``KEY`` to value from the image config; empty on failure.
        """
        try:
            attrs = self.connect().images.get(image_ref).attrs
        except (DockerException, DockerConnectionError):
            return {}
        result: dict[str, str] = {}
        for entry in (attrs.get("Config") or {}).get("Env") or []:
            key, _, value = str(entry).partition("=")
            result[key] = value
        return result

    def image_default_labels(self, image_ref: str) -> dict[str, str]:
        """Labels baked into an image.

        Args:
            image_ref: Image id or reference.

        Returns:
            Mapping of label to value from the image config; empty on failure.
        """
        try:
            attrs = self.connect().images.get(image_ref).attrs
        except (DockerException, DockerConnectionError):
            return {}
        labels = (attrs.get("Config") or {}).get("Labels") or {}
        return {str(k): str(v) for k, v in labels.items()}

    # ------------------------------------------------------------ remediation

    def stop_and_remove(self, container_id: str, timeout: int = 10) -> None:
        """Stop (gracefully, then forcibly) and remove a container.

        Args:
            container_id: Container id or name.
            timeout: Seconds to wait for a graceful stop before killing.

        Raises:
            RogueContainerKillError: If the container cannot be removed.
        """
        try:
            container = self.connect().containers.get(container_id)
        except NotFound:
            logger.info("Container {} already gone", container_id[:12])
            return
        except DockerException as exc:
            raise RogueContainerKillError(f"Cannot look up {container_id[:12]}: {exc}") from exc
        try:
            try:
                container.stop(timeout=timeout)
            except APIError as exc:
                logger.warning("Graceful stop of {} failed ({}); forcing", container.name, exc)
            container.remove(force=True, v=False)
        except NotFound:
            return
        except DockerException as exc:
            raise RogueContainerKillError(
                f"Cannot remove container {container.name}: {exc}"
            ) from exc
        logger.info("Stopped and removed container {} ({})", container.name, container_id[:12])

    def recreate(
        self,
        service: ParsedService,
        project: str,
        *,
        action_id: str,
        declared_volumes: set[str] | None = None,
    ) -> str:
        """Create a container from a baseline service, replacing any with the same name.

        Args:
            service: Canonical baseline service.
            project: Compose project namespace.
            action_id: Enforcement action id stamped as a label for loop suppression.
            declared_volumes: Top-level volumes declared in the baseline.

        Returns:
            The new container id.

        Raises:
            ContainerRecreateError: If the image cannot be pulled or the run fails.
        """
        if not service.image:
            raise ContainerRecreateError(f"Service {service.service_name} declares no image")
        client = self.connect()
        name = service.container_name or f"{project}-{service.service_name}-1"
        try:
            existing = client.containers.get(name)
            existing.remove(force=True)
            logger.info("Removed existing container {} before recreate", name)
        except NotFound:
            pass
        except DockerException as exc:
            raise ContainerRecreateError(f"Cannot remove old {name}: {exc}") from exc

        try:
            try:
                client.images.get(service.image)
            except ImageNotFound:
                logger.info("Pulling image {}", service.image)
                client.images.pull(service.image)
        except DockerException as exc:
            raise ContainerRecreateError(f"Cannot pull image {service.image}: {exc}") from exc

        labels = dict(service.labels)
        labels.update(
            {
                COMPOSE_PROJECT_LABEL: project,
                COMPOSE_SERVICE_LABEL: service.service_name,
                COMPOSE_CONTAINER_NUMBER_LABEL: "1",
                WARDEN_MANAGED_BY_LABEL: WARDEN_MANAGED_BY_VALUE,
                WARDEN_ACTION_LABEL: action_id,
            }
        )
        networks = [f"{project}_{n}" for n in service.networks] or [f"{project}_default"]
        binds, anonymous = volumes_to_docker(service.volumes, project, declared_volumes or set())
        try:
            for network_name in networks:
                self._ensure_network(network_name, project)
            container = client.containers.create(
                service.image,
                name=name,
                detach=True,
                ports=ports_to_docker(service.ports) or None,
                environment=dict(service.environment) or None,
                labels=labels,
                volumes=binds or None,
                restart_policy=restart_policy_to_docker(service.restart),
                network=networks[0],
                hostname=service.service_name,
            )
            for network_name in networks[1:]:
                client.networks.get(network_name).connect(container)
            for target in anonymous:
                logger.debug("Anonymous volume {} left to image defaults", target)
            container.start()
        except DockerException as exc:
            raise ContainerRecreateError(f"Cannot run {name}: {exc}") from exc
        logger.info("Recreated {} from baseline service {}", name, service.service_name)
        return str(container.id)

    def _ensure_network(self, network_name: str, project: str) -> None:
        """Create a Compose-style project network if it does not exist."""
        client = self.connect()
        if client.networks.list(names=[network_name]):
            return
        suffix = (
            network_name[len(project) + 1 :]
            if network_name.startswith(f"{project}_")
            else network_name
        )
        client.networks.create(
            network_name,
            driver="bridge",
            labels={COMPOSE_PROJECT_LABEL: project, "com.docker.compose.network": suffix},
        )
        logger.info("Created network {}", network_name)

    # ------------------------------------------------------------- event pump

    async def pump_events(
        self, queue: "asyncio.Queue[RawDockerEvent]", stop_event: threading.Event
    ) -> None:
        """Stream state-changing container events into ``queue`` until stopped.

        Reconnects forever with ``DOCKER_EVENT_RECONNECT_DELAY`` between attempts.

        Args:
            queue: Queue on the running loop that receives events.
            stop_event: Set by the lifespan to end the pump.
        """
        loop = asyncio.get_running_loop()
        while not stop_event.is_set():
            try:
                await loop.run_in_executor(None, self._stream_events, loop, queue, stop_event)
            except DockerConnectionError as exc:
                logger.warning("Docker event stream unavailable: {}", exc)
            except Exception as exc:  # the socket can die in many ways
                self._reset(str(exc))
                logger.warning("Docker event stream dropped: {}", exc)
            if stop_event.is_set():
                break
            await asyncio.sleep(self._settings.docker_event_reconnect_delay)
        logger.info("Docker event pump stopped")

    def _stream_events(
        self,
        loop: asyncio.AbstractEventLoop,
        queue: "asyncio.Queue[RawDockerEvent]",
        stop_event: threading.Event,
    ) -> None:
        """Blocking worker-thread body: read events and hand them to the loop."""
        client = self.connect()
        stream = client.events(decode=True, filters={"type": "container"})
        self._stream = stream
        logger.info("Subscribed to Docker container events")
        try:
            for payload in stream:
                if stop_event.is_set():
                    break
                if not isinstance(payload, dict):
                    continue
                action = str(payload.get("Action", "")).split(":", 1)[0]
                if action not in STATE_CHANGING_EVENTS:
                    continue
                event = RawDockerEvent.extract(payload)
                loop.call_soon_threadsafe(queue.put_nowait, event)
        finally:
            self._stream = None
            try:
                stream.close()
            except Exception:
                logger.debug("Event stream already closed")
