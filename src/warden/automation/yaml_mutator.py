"""Comment-preserving Compose AST mutations for Reverse-GitOps adoption.

Mutations always operate on a round-trip ``ruamel.yaml`` AST so comments,
quoting and key order in the operator's file survive the pull request.
"""

import re
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any

from loguru import logger
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.scalarstring import DoubleQuotedScalarString

from shared.exceptions import YAMLMutationError
from warden.core.baseline_parser import dump_yaml, load_document

_SERVICE_NAME_RE = re.compile(r"[^a-z0-9_.-]+")


@dataclass(slots=True)
class AdoptionSpec:
    """Everything the mutator needs to write one service into the baseline.

    Attributes:
        service_name: Key to write under ``services:``.
        image: Image reference.
        container_name: Explicit container name to pin, if any.
        ports: Canonical port strings (``host:container/proto``).
        networks: Network names after the project prefix has been stripped.
        external_networks: Networks that belong to another project and must be
            declared ``external`` at the top level.
        labels: Custom labels to write.
        restart: Restart policy, or ``None`` for the Compose default.
        environment_keys: Keys written as ``KEY=${KEY}`` placeholders.
    """

    service_name: str
    image: str
    container_name: str | None = None
    ports: list[str] = field(default_factory=list)
    networks: list[str] = field(default_factory=list)
    external_networks: dict[str, str] = field(default_factory=dict)
    labels: dict[str, str] = field(default_factory=dict)
    restart: str | None = None
    environment_keys: list[str] = field(default_factory=list)


def sanitise_service_name(container_name: str, project: str | None = None) -> str:
    """Derive a Compose service name from a container name.

    Args:
        container_name: Live container name (leading ``/`` tolerated).
        project: Managed project name; a ``<project>-`` prefix is stripped.

    Returns:
        A lowercase service name made of ``[a-z0-9_.-]``.
    """
    name = container_name.lstrip("/").lower()
    if project:
        prefix = project.lower()
        for sep in ("-", "_"):
            if name.startswith(prefix + sep):
                name = name[len(prefix) + 1 :]
                break
        name = re.sub(r"[-_]\d+$", "", name) or name
    name = _SERVICE_NAME_RE.sub("-", name).strip("-_.")
    return name or "adopted-service"


def unique_service_name(candidate: str, existing: Mapping[str, Any]) -> str:
    """Avoid collisions with services already declared in the baseline.

    Args:
        candidate: Desired service name.
        existing: Current ``services:`` mapping.

    Returns:
        ``candidate`` or ``candidate-adopted``, ``candidate-adopted-2`` ...
    """
    if candidate not in existing:
        return candidate
    base = f"{candidate}-adopted"
    if base not in existing:
        return base
    index = 2
    while f"{base}-{index}" in existing:
        index += 1
    return f"{base}-{index}"


def port_to_compose(canonical: str) -> str:
    """Convert a canonical port string back to Compose short syntax.

    Args:
        canonical: ``[host_ip:]host:container/proto`` or ``container/proto``.

    Returns:
        ``host:container`` with ``/udp`` kept and ``/tcp`` dropped.
    """
    text, _, proto = canonical.rpartition("/")
    if not text:
        text, proto = canonical, "tcp"
    if text.startswith("0.0.0.0:"):
        text = text[len("0.0.0.0:") :]
    return text if proto in ("", "tcp") else f"{text}/{proto}"


def _service_node(spec: AdoptionSpec) -> CommentedMap:
    """Build the AST node for a brand-new service."""
    node = CommentedMap()
    node["image"] = spec.image
    if spec.container_name:
        node["container_name"] = spec.container_name
    if spec.ports:
        node["ports"] = _ports_node(spec.ports)
    if spec.environment_keys:
        node["environment"] = _environment_node(spec.environment_keys)
    if spec.labels:
        node["labels"] = _labels_node(spec.labels)
    if spec.restart and spec.restart != "no":
        node["restart"] = spec.restart
    if spec.networks:
        node["networks"] = _networks_node(spec.networks)
    return node


def _ports_node(ports: list[str]) -> CommentedSeq:
    seq = CommentedSeq()
    for port in ports:
        seq.append(DoubleQuotedScalarString(port_to_compose(port)))
    return seq


def _environment_node(keys: list[str]) -> CommentedSeq:
    seq = CommentedSeq()
    for key in keys:
        seq.append(f"{key}=${{{key}}}")
    return seq


def _labels_node(labels: dict[str, str]) -> CommentedSeq:
    seq = CommentedSeq()
    for key, value in labels.items():
        seq.append(DoubleQuotedScalarString(f"{key}={value}"))
    return seq


def _networks_node(networks: list[str]) -> CommentedSeq:
    seq = CommentedSeq()
    for name in networks:
        seq.append(name)
    return seq


def _ensure_networks_declared(document: CommentedMap, spec: AdoptionSpec) -> None:
    """Declare any non-default networks the adopted service joins."""
    needed = [n for n in spec.networks if n != "default"]
    if not needed:
        return
    networks = document.get("networks")
    if networks is None:
        networks = CommentedMap()
        document["networks"] = networks
    if not isinstance(networks, CommentedMap):
        raise YAMLMutationError("Top-level `networks:` is not a mapping")
    for name in needed:
        if name in networks:
            continue
        entry = CommentedMap()
        full_name = spec.external_networks.get(name)
        if full_name:
            entry["external"] = True
            entry["name"] = full_name
        networks[name] = entry or None


def add_service(raw: str, spec: AdoptionSpec) -> tuple[str, str]:
    """Append a new service to the baseline for a ROGUE container.

    Args:
        raw: Current baseline text.
        spec: What to write.

    Returns:
        The mutated YAML text and the final (collision-free) service name.

    Raises:
        YAMLMutationError: If the document has no ``services:`` mapping.
    """
    document = load_document(raw)
    services = document.get("services")
    if not isinstance(services, CommentedMap):
        raise YAMLMutationError("Baseline has no `services:` mapping to extend")
    name = unique_service_name(spec.service_name, services)
    services[name] = _service_node(spec)
    _ensure_networks_declared(document, spec)
    logger.info("Mutated baseline: added service {}", name)
    return dump_yaml(document), name


def update_service(raw: str, spec: AdoptionSpec, fields: list[str]) -> tuple[str, str]:
    """Rewrite the changed fields of an existing service for a DRIFTED container.

    Args:
        raw: Current baseline text.
        spec: Observed values to write.
        fields: Which canonical fields changed (``image``, ``ports`` ...).

    Returns:
        The mutated YAML text and the service name.

    Raises:
        YAMLMutationError: If the service does not exist in the document.
    """
    document = load_document(raw)
    services = document.get("services")
    if not isinstance(services, CommentedMap) or spec.service_name not in services:
        raise YAMLMutationError(f"Service `{spec.service_name}` is not declared in the baseline")
    node = services[spec.service_name]
    if not isinstance(node, CommentedMap):
        raise YAMLMutationError(f"Service `{spec.service_name}` is not a mapping")
    for name in fields:
        if name == "image":
            node["image"] = spec.image
        elif name == "ports":
            _replace(node, "ports", _ports_node(spec.ports) if spec.ports else None)
        elif name == "environment":
            _replace(
                node,
                "environment",
                _environment_node(spec.environment_keys) if spec.environment_keys else None,
            )
        elif name == "labels":
            _replace(node, "labels", _labels_node(spec.labels) if spec.labels else None)
        elif name == "restart":
            _replace(
                node, "restart", spec.restart if spec.restart and spec.restart != "no" else None
            )
        elif name == "networks":
            _replace(node, "networks", _networks_node(spec.networks) if spec.networks else None)
            _ensure_networks_declared(document, spec)
        elif name == "volumes":
            logger.warning(
                "Volume adoption is not written to Git; skipping `volumes` for {}",
                spec.service_name,
            )
    logger.info("Mutated baseline: updated {} fields {}", spec.service_name, fields)
    return dump_yaml(document), spec.service_name


def remove_service(raw: str, service_name: str) -> str:
    """Delete a service block from the baseline (Adopt Deletion for a MISSING service).

    Args:
        raw: Current baseline text.
        service_name: Service key to remove.

    Returns:
        The mutated YAML text.

    Raises:
        YAMLMutationError: If the service is not declared.
    """
    document = load_document(raw)
    services = document.get("services")
    if not isinstance(services, CommentedMap) or service_name not in services:
        raise YAMLMutationError(f"Service `{service_name}` is not declared in the baseline")
    del services[service_name]
    logger.info("Mutated baseline: removed service {}", service_name)
    return dump_yaml(document)


def _replace(node: CommentedMap, key: str, value: Any) -> None:
    """Set or delete a key on a service node."""
    if value is None:
        node.pop(key, None)
    else:
        node[key] = value
