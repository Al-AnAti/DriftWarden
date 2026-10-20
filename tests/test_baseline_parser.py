"""Compose normalisation and project-name resolution."""

import pytest

from shared.constants import BaselineSource
from shared.exceptions import BaselineParseError, ConfigurationError
from warden.core.baseline_parser import (
    Baseline,
    interpolate,
    normalise_mapping,
    normalise_port,
    normalise_service,
    normalise_volume,
    parse_baseline,
    render_service_yaml,
)


def test_fixture_parses_into_canonical_services(baseline: Baseline) -> None:
    assert baseline.project_name == "infrastructure-test"
    assert baseline.service_names == ["web", "cache"]
    web = baseline.services["web"]
    assert web.image == "nginx:alpine"
    assert web.ports == ["8080:80/tcp"]
    assert web.labels == {"warden.managed": "true", "warden.repo": "my-cs-project"}
    assert web.environment == {}
    assert web.restart is None
    assert web.networks == []


def test_name_key_wins_over_namespace() -> None:
    raw = "name: from-yaml\nservices:\n  a:\n    image: x\n"
    parsed = parse_baseline(
        raw, fallback_namespace="from-env", sha=None, source=BaselineSource.MOCK
    )
    assert parsed.project_name == "from-yaml"


def test_namespace_is_fallback_when_name_absent() -> None:
    raw = "services:\n  a:\n    image: x\n"
    parsed = parse_baseline(
        raw, fallback_namespace="from-env", sha=None, source=BaselineSource.MOCK
    )
    assert parsed.project_name == "from-env"


def test_missing_name_and_namespace_is_configuration_error() -> None:
    with pytest.raises(ConfigurationError):
        parse_baseline(
            "services:\n  a:\n    image: x\n",
            fallback_namespace=None,
            sha=None,
            source=BaselineSource.MOCK,
        )


@pytest.mark.parametrize(
    "raw",
    ["not: [valid", "- just\n- a list", "name: x\nservices: 12\n"],
)
def test_malformed_documents_raise(raw: str) -> None:
    with pytest.raises(BaselineParseError):
        parse_baseline(raw, fallback_namespace="ns", sha=None, source=BaselineSource.MOCK)


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("8080:80", ["8080:80/tcp"]),
        (8080, ["8080/tcp"]),
        ("80", ["80/tcp"]),
        ("127.0.0.1:8080:80/udp", ["127.0.0.1:8080:80/udp"]),
        ("8080-8081:80-81", ["8080:80/tcp", "8081:81/tcp"]),
        ({"target": 80, "published": "8080", "protocol": "tcp"}, ["8080:80/tcp"]),
        (
            {"target": 53, "published": 5353, "host_ip": "0.0.0.0", "protocol": "udp"},
            ["0.0.0.0:5353:53/udp"],
        ),
    ],
)
def test_normalise_port(entry: object, expected: list[str]) -> None:
    assert normalise_port(entry) == expected


@pytest.mark.parametrize(
    ("entry", "expected"),
    [
        ("./data:/data:ro", "./data:/data:ro"),
        ("db:/var/lib/mysql", "db:/var/lib/mysql:rw"),
        ("/var/cache", ":/var/cache:rw"),
        ({"type": "volume", "source": "db", "target": "/x", "read_only": True}, "db:/x:ro"),
        ("C:\\data:/data:ro", "C:\\data:/data:ro"),
    ],
)
def test_normalise_volume(entry: object, expected: str) -> None:
    assert normalise_volume(entry) == expected


def test_mapping_accepts_list_and_dict_forms() -> None:
    environ = {"FROM_ENV": "resolved"}
    assert normalise_mapping(["A=1", "B=two=2"]) == {"A": "1", "B": "two=2"}
    assert normalise_mapping({"A": 1, "B": True, "C": None}) == {"A": "1", "B": "true", "C": ""}
    assert normalise_mapping(["FROM_ENV"], environ, lookup_missing=True) == {"FROM_ENV": "resolved"}
    assert normalise_mapping({"FROM_ENV": None}, environ, lookup_missing=True) == {
        "FROM_ENV": "resolved"
    }
    with pytest.raises(BaselineParseError):
        normalise_mapping("A=1")


def test_interpolation_forms() -> None:
    environ = {"TAG": "1.2", "EMPTY": ""}
    assert interpolate("redis:${TAG}", environ) == "redis:1.2"
    assert interpolate("redis:$TAG", environ) == "redis:1.2"
    assert interpolate("${MISSING:-fallback}", environ) == "fallback"
    assert interpolate("${EMPTY:-fallback}", environ) == "fallback"
    assert interpolate("${EMPTY-fallback}", environ) == ""
    assert interpolate("${MISSING}", environ) == ""
    assert interpolate("cost $$5", environ) == "cost $5"


def test_service_normalisation_covers_every_field() -> None:
    node = {
        "image": "app:${TAG}",
        "container_name": "my-app",
        "ports": ["80", {"target": 443, "published": 8443}],
        "environment": {"A": 1},
        "volumes": ["data:/data"],
        "labels": ["x=y"],
        "restart": "unless-stopped",
        "networks": {"backend": {"aliases": ["db"]}},
    }
    service = normalise_service("app", node, {"TAG": "9"})
    assert service.image == "app:9"
    assert service.container_name == "my-app"
    assert service.ports == ["80/tcp", "8443:443/tcp"]
    assert service.environment == {"A": "1"}
    assert service.volumes == ["data:/data:rw"]
    assert service.labels == {"x": "y"}
    assert service.restart == "unless-stopped"
    assert service.networks == ["backend"]
    with pytest.raises(BaselineParseError):
        normalise_service("bad", "not-a-mapping")


def test_render_service_yaml_roots_at_service_name(baseline: Baseline) -> None:
    text = render_service_yaml(baseline.services["cache"])
    assert text.startswith("cache:\n")
    assert "image: redis:alpine" in text
    assert "6379:6379/tcp" in text
