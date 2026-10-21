"""Control-plane pages and JSON API against a fake Docker watcher."""

import json
from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

import web.app as web_app
from shared.constants import DriftStatus
from tests.conftest import PROJECT
from tests.fakes import FakeWatcher
from warden.core.docker_watcher import LiveContainer


@pytest.fixture
def fake_watcher() -> FakeWatcher:
    return FakeWatcher()


@pytest.fixture
def client(
    monkeypatch: pytest.MonkeyPatch,
    fake_watcher: FakeWatcher,
    web_container: LiveContainer,
    drifted_cache: LiveContainer,
    rogue_container: LiveContainer,
) -> Iterator[TestClient]:
    fake_watcher.containers = [web_container, drifted_cache, rogue_container]
    monkeypatch.setattr(web_app, "DockerWatcher", lambda settings: fake_watcher)
    with TestClient(web_app.app) as test_client:
        yield test_client


def test_pages_render(client: TestClient) -> None:
    for path in (
        "/",
        "/events",
        "/buffer",
        "/config",
        f"/diff/{PROJECT}-cache-1",
        "/diff/web",
        "/diff/rogue-db",
    ):
        response = client.get(path)
        assert response.status_code == 200, path
        assert "DRIFT" in response.text and "WARDEN" in response.text
    assert client.get("/diff/nope").status_code == 404
    home = client.get("/").text
    assert "DRIFT DETECTED" in home and "rogue-db" in home and "Mode: <span" in home


def test_state_and_health(client: TestClient) -> None:
    health = client.get("/api/health").json()
    assert health["status"] == "healthy" and health["baseline_source"] == "mock"
    assert health["overall"] == "drift_detected" and health["docker_connected"]
    state = client.get("/api/state").json()["snapshot"]
    assert state["counts"] == {
        "matched": 1,
        "rogue": 1,
        "missing": 0,
        "drifted": 1,
        "pending_adoption": 0,
    }
    assert "raw_yaml" not in state["baseline"]
    detail = client.get(f"/api/containers/{PROJECT}-cache-1").json()
    assert detail["drift_status"] == "drifted"
    assert [d["field"] for d in detail["diffs"] if d["changed"]] == ["image", "ports", "networks"]
    assert client.get("/api/containers/ghost").status_code == 404


def test_sse_subscribers_receive_published_snapshots(client: TestClient) -> None:
    projector = web_app.app.state.warden.projector
    queue = projector.subscribe()
    assert projector.subscriber_count == 1
    client.post("/api/reconcile")
    message = json.loads(queue.get_nowait())
    assert message["version"] == projector.version
    assert message["snapshot"]["overall"] == "drift_detected"
    projector.unsubscribe(queue)
    assert projector.subscriber_count == 0


def test_actions(client: TestClient, fake_watcher: FakeWatcher) -> None:
    response = client.post(f"/api/actions/revert/{PROJECT}-cache-1")
    assert response.status_code == 200 and response.json()["ok"]
    assert fake_watcher.recreated[0][0] == "cache"

    response = client.post("/api/actions/kill/rogue-db")
    assert response.status_code == 200 and "rogue-db" in response.json()["message"]

    response = client.post(f"/api/actions/kill/{PROJECT}-web-1")
    assert response.status_code == 409
    body = response.json()
    assert body["ok"] is False and body["error"] == "DriftWardenError"

    response = client.post(
        "/api/actions/adopt/rogue-db", json={"container_id": "rogue-db", "note": "x"}
    )
    assert response.status_code == 409  # already killed above

    audit = client.get("/api/audit").json()
    kill_rows = [row for row in audit if row["action"] == "enforce_kill"]
    assert kill_rows and kill_rows[0]["target"] == "rogue-db"
    assert all(row["hostname"] == "test-host" for row in audit)


def test_adopt_refused_with_mock_baseline(client: TestClient) -> None:
    response = client.post("/api/actions/adopt/rogue-db")
    assert response.status_code == 409 and "GitHub" in response.json()["message"]
    response = client.post("/api/actions/adopt-deletion/rogue-db")
    assert response.status_code == 409 and "only missing" in response.json()["message"]


def test_missing_diff_page_is_simplified(client: TestClient, fake_watcher: FakeWatcher) -> None:
    fake_watcher.containers = [c for c in fake_watcher.containers if "cache" not in c.name]
    assert client.post("/api/reconcile").json()["snapshot"]["counts"]["missing"] == 1
    response = client.post("/api/actions/adopt-deletion/cache")
    assert response.status_code == 409 and "GitHub" in response.json()["message"]
    page = client.get("/diff/cache").text
    assert "Enforce (Spin Up)" in page and "Adopt Deletion" in page
    assert "Enforce Kill" not in page and "Field by field" not in page
    assert "Live Observed State" not in page
    rogue = client.get("/diff/rogue-db").text
    assert "Enforce Kill" in rogue and "Live Observed State" in rogue
    assert 'id="modal-root"' in rogue and "window.confirm" not in rogue


def test_whitelist_roundtrip(client: TestClient) -> None:
    created = client.post(
        "/api/whitelist",
        json={"target_type": "CONTAINER", "target_identifier": "rogue-db", "reason": "known"},
    ).json()
    assert created["target_type"] == "CONTAINER"
    assert [w["id"] for w in client.get("/api/whitelist").json()] == [created["id"]]
    state = client.post("/api/reconcile").json()["snapshot"]
    assert state["counts"]["rogue"] == 0 and state["exempt"] == ["rogue-db (whitelisted container)"]
    assert "rogue-db" in client.get("/config").text
    assert client.delete(f"/api/whitelist/{created['id']}").json()["ok"]
    assert client.delete(f"/api/whitelist/{created['id']}").status_code == 404
    assert (
        client.post(
            "/api/whitelist", json={"target_type": "NOPE", "target_identifier": "x"}
        ).status_code
        == 422
    )


def test_buffer_and_events_endpoints(client: TestClient) -> None:
    assert client.get("/api/buffer").json() == []
    events = client.get("/api/events").json()
    assert {e["drift_status"] for e in events} >= {
        DriftStatus.ROGUE.value,
        DriftStatus.DRIFTED.value,
    }
