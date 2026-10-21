"""Round-trip Compose mutations for adoption pull requests."""

import pytest

from shared.exceptions import YAMLMutationError
from warden.automation.yaml_mutator import (
    AdoptionSpec,
    add_service,
    port_to_compose,
    remove_service,
    sanitise_service_name,
    unique_service_name,
    update_service,
)
from warden.core.baseline_parser import load_document


def test_add_service_preserves_comments_and_appends(fixture_yaml: str) -> None:
    spec = AdoptionSpec(
        service_name="rogue-db",
        image="mariadb:latest",
        container_name="rogue-db",
        ports=["0.0.0.0:3306:3306/tcp"],
        networks=["finpulse_default"],
        external_networks={"finpulse_default": "finpulse_default"},
        labels={"team": "db"},
        restart="always",
        environment_keys=["MARIADB_ROOT_PASSWORD"],
    )
    text, name = add_service(fixture_yaml, spec)
    assert name == "rogue-db"
    assert text.startswith("# docker-compose.test.yml")  # leading comment survives
    assert '- "8080:80"' in text  # original quoting survives
    document = load_document(text)
    node = document["services"]["rogue-db"]
    assert node["image"] == "mariadb:latest"
    assert node["container_name"] == "rogue-db"
    assert list(node["ports"]) == ["3306:3306"]
    assert list(node["environment"]) == ["MARIADB_ROOT_PASSWORD=${MARIADB_ROOT_PASSWORD}"]
    assert list(node["labels"]) == ["team=db"]
    assert node["restart"] == "always"
    assert list(node["networks"]) == ["finpulse_default"]
    assert document["networks"]["finpulse_default"]["external"] is True
    assert "rogue" not in text.split("environment")[1].split("\n")[1]  # value not committed


def test_add_service_avoids_name_collisions(fixture_yaml: str) -> None:
    text, name = add_service(fixture_yaml, AdoptionSpec(service_name="web", image="x:1"))
    assert name == "web-adopted"
    text2, name2 = add_service(text, AdoptionSpec(service_name="web", image="x:2"))
    assert name2 == "web-adopted-2"
    assert load_document(text2)["services"]["web"]["image"] == "nginx:alpine"


def test_add_service_requires_services_mapping() -> None:
    with pytest.raises(YAMLMutationError):
        add_service("name: x\n", AdoptionSpec(service_name="a", image="b"))


def test_update_service_rewrites_only_requested_fields(fixture_yaml: str) -> None:
    spec = AdoptionSpec(
        service_name="cache", image="redis:latest", ports=["6378:6378/tcp"], restart="no"
    )
    text, name = update_service(fixture_yaml, spec, ["image", "ports", "restart"])
    assert name == "cache"
    node = load_document(text)["services"]["cache"]
    assert node["image"] == "redis:latest"
    assert list(node["ports"]) == ["6378:6378"]
    assert "restart" not in node
    assert list(node["labels"]) == ["warden.managed=true", "warden.repo=my-cs-project"]
    assert load_document(text)["services"]["web"]["image"] == "nginx:alpine"


def test_update_service_drops_emptied_blocks(fixture_yaml: str) -> None:
    spec = AdoptionSpec(service_name="cache", image="redis:alpine", labels={})
    text, _ = update_service(fixture_yaml, spec, ["labels", "volumes"])
    assert "labels" not in load_document(text)["services"]["cache"]


def test_update_unknown_service_raises(fixture_yaml: str) -> None:
    with pytest.raises(YAMLMutationError):
        update_service(fixture_yaml, AdoptionSpec(service_name="ghost", image="x"), ["image"])


@pytest.mark.parametrize(
    ("container", "project", "expected"),
    [
        ("/infrastructure-test-cache-1", "infrastructure-test", "cache"),
        ("infrastructure-test_cache_1", "infrastructure-test", "cache"),
        ("rogue-db", "infrastructure-test", "rogue-db"),
        ("Weird Name!!", None, "weird-name"),
        ("", None, "adopted-service"),
    ],
)
def test_sanitise_service_name(container: str, project: str | None, expected: str) -> None:
    assert sanitise_service_name(container, project) == expected


def test_unique_service_name() -> None:
    assert unique_service_name("a", {}) == "a"
    assert unique_service_name("a", {"a": 1}) == "a-adopted"
    assert unique_service_name("a", {"a": 1, "a-adopted": 1}) == "a-adopted-2"


@pytest.mark.parametrize(
    ("canonical", "expected"),
    [
        ("8080:80/tcp", "8080:80"),
        ("0.0.0.0:53:53/udp", "53:53/udp"),
        ("80/tcp", "80"),
        ("127.0.0.1:8080:80/tcp", "127.0.0.1:8080:80"),
    ],
)
def test_port_to_compose(canonical: str, expected: str) -> None:
    assert port_to_compose(canonical) == expected


def test_remove_service_deletes_only_that_block(fixture_yaml: str) -> None:
    text = remove_service(fixture_yaml, "web")
    document = load_document(text)
    assert list(document["services"]) == ["cache"]
    assert text.startswith("# docker-compose.test.yml")
    assert '- "6379:6379"' in text
    with pytest.raises(YAMLMutationError):
        remove_service(fixture_yaml, "ghost")


def test_added_service_keys_are_siblings_under_the_service(fixture_yaml: str) -> None:
    spec = AdoptionSpec(
        service_name="svc",
        image="img:1",
        ports=["8000:80/tcp"],
        labels={"a": "b"},
        restart="always",
        networks=["backend"],
        environment_keys=["TOKEN"],
    )
    text, _ = add_service(fixture_yaml, spec)
    block = text.split("  svc:\n", 1)[1]
    keys = [ln for ln in block.splitlines() if ln.startswith("    ") and not ln.startswith("     ")]
    assert keys == [
        "    image: img:1",
        "    ports:",
        "    environment:",
        "    labels:",
        "    restart: always",
        "    networks:",
    ]
    assert "container_name" not in block
