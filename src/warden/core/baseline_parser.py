"""Compose baseline parsing and normalisation.

The parser turns a single-file ``docker-compose.yml`` into a ``Baseline`` whose
services are stored in one canonical shape, independent of the short or long
Compose syntax used in the source document. The diff engine and the enforcement
path only ever see the canonical form.
"""

import os
import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timezone
from io import StringIO
from typing import Any

from loguru import logger
from ruamel.yaml import YAML

from shared.constants import BaselineSource
from shared.exceptions import BaselineParseError, ConfigurationError
from shared.models import ParsedService

_INTERPOLATION = re.compile(
    r"\$(?:(?P<escaped>\$)|\{(?P<braced>[A-Za-z_][A-Za-z0-9_]*)(?::?-(?P<default>[^}]*))?\}"
    r"|(?P<named>[A-Za-z_][A-Za-z0-9_]*))"
)


@dataclass(slots=True)
class Baseline:
    """A parsed and normalised Compose baseline held in memory.

    Attributes:
        project_name: Resolved Compose project namespace.
        services: Canonical service definitions keyed by service name.
        raw_yaml: The original document text, preserved for display and mutation.
        sha: Git blob SHA of the document, when it came from GitHub.
        source: Where the baseline was loaded from.
        fetched_at: When the content was last confirmed fresh.
        declared_networks: Top-level network keys declared in the document.
        declared_volumes: Top-level volume keys declared in the document.
    """

    project_name: str
    services: dict[str, ParsedService]
    raw_yaml: str
    sha: str | None
    source: BaselineSource
    fetched_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))
    declared_networks: set[str] = field(default_factory=set)
    declared_volumes: set[str] = field(default_factory=set)

    @property
    def service_names(self) -> list[str]:
        """Service names in document order."""
        return list(self.services)


def new_yaml() -> YAML:
    """Create a round-trip ``ruamel.yaml`` loader/dumper with stable formatting.

    Returns:
        A ``YAML`` instance configured for comment-preserving round trips.
    """
    yaml = YAML(typ="rt")
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    yaml.width = 4096
    return yaml


def load_document(raw: str) -> Any:
    """Load a Compose document as a round-trip AST.

    Args:
        raw: YAML text.

    Returns:
        The ``CommentedMap`` root node.

    Raises:
        BaselineParseError: If the text is not valid YAML or not a mapping.
    """
    try:
        document = new_yaml().load(raw)
    except Exception as exc:  # ruamel raises many concrete error types
        raise BaselineParseError(f"Baseline is not valid YAML: {exc}") from exc
    if not isinstance(document, Mapping):
        raise BaselineParseError("Baseline root must be a mapping with a `services:` key")
    return document


def dump_yaml(data: Any) -> str:
    """Serialise a mapping to YAML text.

    Args:
        data: Any ruamel-compatible structure.

    Returns:
        YAML text without a trailing document marker.
    """
    buffer = StringIO()
    new_yaml().dump(data, buffer)
    return buffer.getvalue()


def interpolate(value: str, environ: Mapping[str, str] | None = None) -> str:
    """Resolve ``${VAR}``, ``${VAR:-default}`` and ``$VAR`` references.

    Missing variables resolve to an empty string, matching Compose behaviour.

    Args:
        value: The raw scalar.
        environ: Variable source, defaulting to the process environment.

    Returns:
        The interpolated scalar.
    """
    source = os.environ if environ is None else environ

    def replace(match: re.Match[str]) -> str:
        if match.group("escaped"):
            return "$"
        name = match.group("braced") or match.group("named")
        default = match.group("default")
        resolved = source.get(name)
        if resolved is None or (resolved == "" and default is not None and ":-" in match.group(0)):
            return default if default is not None else ""
        return resolved

    return _INTERPOLATION.sub(replace, value)


def has_unresolved_reference(value: str, environ: Mapping[str, str] | None = None) -> bool:
    """True when ``value`` references a variable that is unset and has no default.

    Args:
        value: Raw scalar before interpolation.
        environ: Variable source, defaulting to the process environment.

    Returns:
        Whether at least one ``${VAR}`` / ``$VAR`` cannot be resolved here.
    """
    source = os.environ if environ is None else environ
    for match in _INTERPOLATION.finditer(value):
        if match.group("escaped"):
            continue
        name = match.group("braced") or match.group("named")
        if match.group("default") is not None:
            continue
        if source.get(name) is None:
            return True
    return False


def unresolved_environment_keys(node: Any, environ: Mapping[str, str] | None = None) -> set[str]:
    """Environment keys whose declared value cannot be resolved where DriftWarden runs.

    Args:
        node: The raw ``environment:`` block (mapping or list).
        environ: Variable source, defaulting to the process environment.

    Returns:
        Keys such as ``MARIADB_ROOT_PASSWORD`` declared as ``${MARIADB_ROOT_PASSWORD}``.
    """
    source = os.environ if environ is None else environ
    keys: set[str] = set()
    if isinstance(node, Mapping):
        for key, value in node.items():
            if value is None and source.get(str(key)) is None:
                keys.add(str(key))
            elif isinstance(value, str) and has_unresolved_reference(value, environ):
                keys.add(str(key))
    elif isinstance(node, list):
        for item in node:
            text = str(item)
            key, sep, value = text.partition("=")
            if not sep:
                if source.get(key.strip()) is None:
                    keys.add(key.strip())
            elif has_unresolved_reference(value, environ):
                keys.add(key.strip())
    return keys


def _scalar(value: Any, environ: Mapping[str, str] | None) -> str:
    """Render any YAML scalar as an interpolated string."""
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return ""
    return interpolate(str(value), environ)


def normalise_mapping(
    node: Any, environ: Mapping[str, str] | None = None, *, lookup_missing: bool = False
) -> dict[str, str]:
    """Normalise a Compose ``labels``/``environment`` block to a string dict.

    Args:
        node: A mapping, a list of ``KEY=value`` strings, or ``None``.
        environ: Variable source for interpolation.
        lookup_missing: For ``environment`` blocks, resolve bare ``KEY`` entries
            from the variable source instead of leaving them empty.

    Returns:
        Keys and string values in declaration order.

    Raises:
        BaselineParseError: If the block is neither a mapping nor a list.
    """
    source = os.environ if environ is None else environ
    result: dict[str, str] = {}
    if node is None:
        return result
    if isinstance(node, Mapping):
        for key, value in node.items():
            name = str(key)
            if value is None and lookup_missing:
                result[name] = source.get(name, "")
            else:
                result[name] = _scalar(value, environ)
        return result
    if isinstance(node, list):
        for item in node:
            text = interpolate(str(item), environ)
            if "=" in text:
                key, _, value = text.partition("=")
                result[key.strip()] = value
            elif lookup_missing:
                result[text.strip()] = source.get(text.strip(), "")
            else:
                result[text.strip()] = ""
        return result
    raise BaselineParseError(f"Expected a mapping or list, got {type(node).__name__}")


def normalise_port(entry: Any, environ: Mapping[str, str] | None = None) -> list[str]:
    """Canonicalise one Compose port entry.

    The canonical form is ``[host_ip:]host_port:container_port/proto``; entries
    without a published host port become ``container_port/proto``. Equal-length
    ranges on both sides are expanded into one entry per port.

    Args:
        entry: Short-syntax string/int or long-syntax mapping.
        environ: Variable source for interpolation.

    Returns:
        One or more canonical port strings.
    """
    host_ip = ""
    published = ""
    target = ""
    proto = "tcp"
    if isinstance(entry, Mapping):
        target = _scalar(entry.get("target"), environ)
        published = _scalar(entry.get("published"), environ)
        host_ip = _scalar(entry.get("host_ip"), environ)
        proto = _scalar(entry.get("protocol"), environ) or "tcp"
    else:
        text = _scalar(entry, environ)
        if "/" in text:
            text, proto = text.rsplit("/", 1)
        parts = text.split(":")
        if len(parts) == 1:
            target = parts[0]
        elif len(parts) == 2:
            published, target = parts
        else:
            host_ip = ":".join(parts[:-2])
            published, target = parts[-2], parts[-1]
    return [
        _format_port(host_ip, pub, tgt, proto.lower())
        for pub, tgt in _expand_port_ranges(published, target)
    ]


def _expand_port_ranges(published: str, target: str) -> list[tuple[str, str]]:
    """Expand ``8080-8081`` / ``80-81`` style ranges into pairs."""

    def span(text: str) -> list[str]:
        if "-" in text:
            start, _, end = text.partition("-")
            if start.isdigit() and end.isdigit() and int(end) >= int(start):
                return [str(port) for port in range(int(start), int(end) + 1)]
        return [text]

    published_ports = span(published) if published else [""]
    target_ports = span(target)
    if len(published_ports) == len(target_ports):
        return list(zip(published_ports, target_ports))
    if len(target_ports) == 1:
        return [(pub, target_ports[0]) for pub in published_ports]
    return [(published, target)]


def _format_port(host_ip: str, published: str, target: str, proto: str) -> str:
    """Format the canonical port string."""
    if not published:
        return f"{target}/{proto}"
    prefix = f"{host_ip}:" if host_ip else ""
    return f"{prefix}{published}:{target}/{proto}"


def normalise_volume(entry: Any, environ: Mapping[str, str] | None = None) -> str:
    """Canonicalise one Compose volume entry to ``source:target:mode``.

    Anonymous volumes have an empty source. Mode is ``ro`` or ``rw``.

    Args:
        entry: Short-syntax string or long-syntax mapping.
        environ: Variable source for interpolation.

    Returns:
        The canonical volume string.
    """
    if isinstance(entry, Mapping):
        source = _scalar(entry.get("source"), environ)
        target = _scalar(entry.get("target"), environ)
        read_only = bool(entry.get("read_only", False))
        return f"{source}:{target}:{'ro' if read_only else 'rw'}"
    text = _scalar(entry, environ)
    parts = text.split(":")
    if len(parts) == 1:
        return f":{parts[0]}:rw"
    if len(parts) == 2:
        return f"{parts[0]}:{parts[1]}:rw"
    # Windows bind sources may contain a drive letter: "C:\\data:/data:ro".
    mode = "ro" if "ro" in parts[-1].split(",") else "rw"
    if len(parts[0]) == 1 and parts[0].isalpha():
        return f"{parts[0]}:{parts[1]}:{parts[2]}:{mode}" if len(parts) > 3 else f"{text}:rw"
    return f"{parts[0]}:{parts[1]}:{mode}"


def normalise_networks(node: Any, environ: Mapping[str, str] | None = None) -> list[str]:
    """Canonicalise a service ``networks:`` block to a list of names.

    Args:
        node: A list of names or a mapping of name to options.
        environ: Variable source for interpolation.

    Returns:
        Network names in declaration order.
    """
    if node is None:
        return []
    if isinstance(node, Mapping):
        return [_scalar(key, environ) for key in node]
    if isinstance(node, list):
        return [_scalar(item, environ) for item in node]
    return [_scalar(node, environ)]


def normalise_service(
    name: str, node: Any, environ: Mapping[str, str] | None = None
) -> ParsedService:
    """Normalise one ``services:`` entry.

    Args:
        name: Service key.
        node: The service mapping from the AST.
        environ: Variable source for interpolation.

    Returns:
        The canonical service record.

    Raises:
        BaselineParseError: If the service is not a mapping.
    """
    if not isinstance(node, Mapping):
        raise BaselineParseError(f"Service `{name}` must be a mapping")
    ports: list[str] = []
    for entry in node.get("ports") or []:
        ports.extend(normalise_port(entry, environ))
    image = node.get("image")
    restart = node.get("restart")
    container_name = node.get("container_name")
    return ParsedService(
        service_name=name,
        image=_scalar(image, environ) if image is not None else None,
        ports=ports,
        labels=normalise_mapping(node.get("labels"), environ),
        environment=normalise_mapping(node.get("environment"), environ, lookup_missing=True),
        volumes=[normalise_volume(entry, environ) for entry in node.get("volumes") or []],
        restart=_scalar(restart, environ) if restart is not None else None,
        networks=normalise_networks(node.get("networks"), environ),
        container_name=_scalar(container_name, environ) if container_name is not None else None,
        unresolved_env=unresolved_environment_keys(node.get("environment"), environ),
    )


def resolve_project_name(document: Mapping[str, Any], fallback_namespace: str | None) -> str:
    """Resolve the managed Compose namespace.

    The document's top-level ``name:`` wins; ``COMPOSE_NAMESPACE`` is the fallback.

    Args:
        document: Parsed Compose root mapping.
        fallback_namespace: Value of ``COMPOSE_NAMESPACE``.

    Returns:
        The project namespace.

    Raises:
        ConfigurationError: If neither source provides a name.
    """
    declared = document.get("name")
    if declared is not None and str(declared).strip():
        return str(declared).strip()
    if fallback_namespace:
        return fallback_namespace
    raise ConfigurationError(
        "Managed namespace cannot be determined: the baseline has no top-level `name:` "
        "and COMPOSE_NAMESPACE is not set"
    )


def parse_baseline(
    raw: str,
    *,
    fallback_namespace: str | None,
    sha: str | None,
    source: BaselineSource,
    environ: Mapping[str, str] | None = None,
) -> Baseline:
    """Parse a Compose document into a canonical ``Baseline``.

    Args:
        raw: YAML text of the Compose file.
        fallback_namespace: ``COMPOSE_NAMESPACE`` used when ``name:`` is absent.
        sha: Git blob SHA of the document, if known.
        source: Where the document came from.
        environ: Variable source for interpolation (defaults to ``os.environ``).

    Returns:
        The parsed baseline.

    Raises:
        BaselineParseError: If the document is malformed.
        ConfigurationError: If the project namespace cannot be resolved.
    """
    document = load_document(raw)
    services_node = document.get("services")
    if not isinstance(services_node, Mapping):
        raise BaselineParseError("Baseline must declare a `services:` mapping")
    project_name = resolve_project_name(document, fallback_namespace)
    services = {
        str(name): normalise_service(str(name), node, environ)
        for name, node in services_node.items()
    }
    networks_node = document.get("networks")
    volumes_node = document.get("volumes")
    baseline = Baseline(
        project_name=project_name,
        services=services,
        raw_yaml=raw,
        sha=sha,
        source=source,
        declared_networks={str(k) for k in networks_node}
        if isinstance(networks_node, Mapping)
        else set(),
        declared_volumes={str(k) for k in volumes_node}
        if isinstance(volumes_node, Mapping)
        else set(),
    )
    logger.debug(
        "Parsed baseline project={} services={} sha={}",
        project_name,
        baseline.service_names,
        sha,
    )
    return baseline


def service_to_mapping(service: ParsedService) -> dict[str, Any]:
    """Render a canonical service as a Compose-style mapping for display.

    Args:
        service: The canonical service.

    Returns:
        A plain mapping suitable for YAML dumping.
    """
    mapping: dict[str, Any] = {}
    if service.image:
        mapping["image"] = service.image
    if service.container_name:
        mapping["container_name"] = service.container_name
    if service.ports:
        mapping["ports"] = list(service.ports)
    if service.environment:
        mapping["environment"] = dict(service.environment)
    if service.volumes:
        mapping["volumes"] = list(service.volumes)
    if service.labels:
        mapping["labels"] = dict(service.labels)
    if service.restart:
        mapping["restart"] = service.restart
    if service.networks:
        mapping["networks"] = list(service.networks)
    return mapping


def render_service_yaml(service: ParsedService) -> str:
    """Render a canonical service as the YAML shown in the diff viewer.

    Args:
        service: The canonical service.

    Returns:
        YAML text rooted at the service name.
    """
    return dump_yaml({service.service_name: service_to_mapping(service)})
