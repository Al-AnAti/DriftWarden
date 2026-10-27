"""Resolve the name of the Docker host DriftWarden is watching.

Inside a container ``socket.gethostname()`` and ``platform.node()`` return the
container's own 12-character id, which is useless in audit rows, pull request
bodies and the dashboard header. The resolution cascade is:

1. ``WARDEN_HOSTNAME`` / ``WARDEN_HOST_NAME`` from the environment or ``.env``.
2. ``/etc/host_hostname`` when the host's ``/etc/hostname`` is mounted read-only.
3. The Docker daemon's own name (``docker info`` → ``Name``).
4. ``platform.node()`` as the last resort.
"""

import platform
from collections.abc import Callable, Mapping
from pathlib import Path
from typing import Any

from loguru import logger

HOST_HOSTNAME_FILE = Path("/etc/host_hostname")
"""Where ``make start`` mounts the host's ``/etc/hostname`` read-only."""


def _read_mounted_hostname(path: Path = HOST_HOSTNAME_FILE) -> str | None:
    try:
        text = path.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return text or None


def _daemon_hostname(docker_info: Callable[[], Mapping[str, Any]] | None) -> str | None:
    if docker_info is None:
        return None
    try:
        info = docker_info()
    except Exception as exc:  # the daemon may be down; every error means "unknown"
        logger.debug("Docker daemon info unavailable for host identity: {}", exc)
        return None
    name = info.get("Name") if isinstance(info, Mapping) else None
    return str(name).strip() if name else None


def resolve_host_identity(
    configured: str | None,
    docker_info: Callable[[], Mapping[str, Any]] | None = None,
    *,
    mounted_file: Path = HOST_HOSTNAME_FILE,
) -> tuple[str, str]:
    """Resolve the host name and say where it came from.

    Args:
        configured: Value of ``WARDEN_HOSTNAME`` / ``WARDEN_HOST_NAME``, if set.
        docker_info: Callable returning ``docker info`` as a mapping; ``None`` skips it.
        mounted_file: Path of the mounted host ``/etc/hostname``.

    Returns:
        ``(hostname, source)`` where source is ``env``, ``mount``, ``docker`` or ``platform``.
    """
    if configured and configured.strip():
        return configured.strip(), "env"
    mounted = _read_mounted_hostname(mounted_file)
    if mounted:
        return mounted, "mount"
    daemon = _daemon_hostname(docker_info)
    if daemon:
        return daemon, "docker"
    return platform.node() or "unknown-host", "platform"
