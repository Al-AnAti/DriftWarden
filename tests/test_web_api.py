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
    with TestClient(web_app.app, raise_server_exceptions=False) as test_client:
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
        "awaiting_restart": 0,
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
    assert "[ Enforce ]" in page and "[ Adopt ]" in page
    assert "Spin Up" not in page and "Field by field" not in page
    assert "Live Observed State" not in page
    rogue = client.get("/diff/rogue-db").text
    assert "[ Enforce ]" in rogue and "Live Observed State" in rogue
    assert "Enforce Kill" not in rogue
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


def test_diff_page_marks_only_changed_lines_in_a_gutter(client: TestClient) -> None:
    page = client.get(f"/diff/{PROJECT}-cache-1").text
    assert '<span class="ln">1</span>' in page
    assert 'class="line del"' in page and 'class="line add"' in page
    # The parent key is shared by both panes and must not carry a marker.
    assert '<span class="code">  labels:</span>' in page
    assert 'class="line del"><span class="ln">' in page


def test_line_diff_targets_children_not_parents() -> None:
    from web.routes.views import line_diff

    expected = "cache:\n  image: redis:alpine\n  labels:\n    a: '1'\n    b: '2'\n"
    observed = "cache:\n  image: redis:alpine\n  labels:\n    a: '1'\n    b: '3'\n"
    left, right = line_diff(expected, observed)
    assert [(ln.number, ln.sign) for ln in left if ln.sign] == [(5, "-")]
    assert [(ln.number, ln.sign) for ln in right if ln.sign] == [(5, "+")]
    assert left[2].text == "  labels:" and left[2].sign == ""
    rogue_left, rogue_right = line_diff(None, "x:\n  image: y\n")
    assert rogue_left == [] and [ln.sign for ln in rogue_right] == ["+", "+"]


def test_settings_endpoints(client: TestClient) -> None:
    views = client.get("/api/settings").json()
    assert {v["key"] for v in views} >= {"warden_mode", "enforcement_grace_seconds"}
    response = client.put("/api/settings", json={"key": "warden_mode", "value": "enforce"})
    assert response.status_code == 409 and "ENABLE AUTONOMOUS" in response.json()["message"]
    response = client.put(
        "/api/settings",
        json={"key": "warden_mode", "value": "enforce", "confirmation": "ENABLE AUTONOMOUS"},
    )
    assert response.status_code == 200 and response.json()["value"] == "enforce"
    assert client.get("/api/health").json()["mode"] == "enforce"
    assert "ENABLE AUTONOMOUS" in client.get("/config").text
    client.put("/api/settings", json={"key": "warden_mode", "value": "observe"})


def test_batch_endpoint(client: TestClient, fake_watcher: FakeWatcher) -> None:
    response = client.post("/api/actions/batch", json={"operation": "enforce", "scope": "rogue"})
    assert response.status_code == 200
    body = response.json()
    assert body["succeeded"] == 1 and body["results"][0]["target"] == "rogue-db"
    assert any(c.name == "rogue-db" for c in fake_watcher.containers) is False
    assert client.post("/api/actions/batch", json={"operation": "nope"}).status_code == 422


def test_revoke_buffer_endpoint_requires_active_entry(client: TestClient) -> None:
    response = client.post("/api/buffer/123/revoke")
    assert response.status_code == 409 and "No active buffer entry" in response.json()["message"]


def test_sse_payload_carries_transitions(client: TestClient, fake_watcher: FakeWatcher) -> None:
    projector = web_app.app.state.warden.projector
    queue = projector.subscribe()
    fake_watcher.containers = [c for c in fake_watcher.containers if c.name != "rogue-db"]
    client.post("/api/reconcile")
    message = json.loads(queue.get_nowait())
    projector.unsubscribe(queue)
    assert message["transitions"] == [] or all("status" in t for t in message["transitions"])
    assert message["snapshot"]["counts"]["rogue"] == 0


def test_batch_missing_scope_and_consolidated_adopt_refused_without_github(
    client: TestClient, fake_watcher: FakeWatcher
) -> None:
    fake_watcher.containers = [c for c in fake_watcher.containers if "cache" not in c.name]
    client.post("/api/reconcile")
    response = client.post("/api/actions/batch", json={"operation": "adopt", "scope": "missing"})
    assert response.status_code == 200
    body = response.json()
    assert body["failed"] == 1 and "GitHub" in body["results"][0]["message"]
    assert body["pr_url"] is None
    page = client.get("/").text
    assert "🔄 Refresh" in page and "event-toasts" not in page
    assert "Spin up" not in page and ">Kill<" not in page and ">Revert<" not in page
    assert 'src="/static/img/logo.png"' in page and "Awaiting restart" in page
    assert "🔄 Refresh" in client.get("/events").text


def test_unexpected_errors_reach_the_ui_as_json(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    runtime = web_app.app.state.warden

    async def boom(ref: str) -> None:
        raise RuntimeError("socket write refused")

    monkeypatch.setattr(runtime.reconciler, "revert", boom)
    response = client.post(f"/api/actions/revert/{PROJECT}-cache-1")
    assert response.status_code == 500
    body = response.json()
    assert body["ok"] is False and "socket write refused" in body["message"]
    assert 'id="banner-action"' in client.get("/").text


def test_batch_buttons_disable_without_actionable_drift(
    client: TestClient, fake_watcher: FakeWatcher, web_container: LiveContainer
) -> None:
    def tag(page: str, button_id: str) -> str:
        return page.split(f'id="{button_id}"')[1].split(">")[0]

    page = client.get("/").text
    assert "No active drift" not in tag(page, "btn-enforce-all")
    assert "No active drift" not in tag(page, "btn-adopt-all")
    fake_watcher.containers = [web_container]  # cache MISSING still counts as drift
    client.post("/api/reconcile")
    assert "No active drift" not in tag(client.get("/").text, "btn-enforce-all")


def test_batch_buttons_disabled_when_only_matched(
    monkeypatch: pytest.MonkeyPatch,
    fake_watcher: FakeWatcher,
    web_container: LiveContainer,
    cache_container: LiveContainer,
) -> None:
    fake_watcher.containers = [web_container, cache_container]
    monkeypatch.setattr(web_app, "DockerWatcher", lambda settings: fake_watcher)
    with TestClient(web_app.app, raise_server_exceptions=False) as test_client:
        page = test_client.get("/").text
        for button_id in ("btn-enforce-all", "btn-adopt-all"):
            tag = page.split(f'id="{button_id}"')[1].split(">")[0]
            assert 'disabled title="No active drift to reconcile"' in tag
        assert "updateBatchButtons" in test_client.get("/static/js/dashboard.js").text


def test_reverse_endpoint_and_events_button(client: TestClient) -> None:
    response = client.post("/api/actions/kill/rogue-db")
    assert response.status_code == 200
    audit = client.get("/api/audit").json()
    kill = next(a for a in audit if a["action"] == "enforce_kill")
    assert kill["reversible"] and kill["reversal_warning"] and "snapshot" not in kill
    page = client.get("/events").text
    assert f'onclick=\'wardenReverse({kill["id"]}, "enforce_kill"' in page
    response = client.post(f"/api/audit/{kill['id']}/reverse")
    assert response.status_code == 200 and response.json()["action"] == "reverse"
    again = client.post(f"/api/audit/{kill['id']}/reverse")
    assert again.status_code == 409 and "cannot be reversed again" in again.json()["message"]
    assert client.post("/api/audit/999999/reverse").status_code == 409
    page = client.get("/events").text
    assert "Already reversed by #" in page and "reversal_completed" in page


def test_audit_meta_covers_every_action() -> None:
    from shared.constants import AuditAction
    from web.routes.views import AUDIT_CATEGORIES, AUDIT_META

    keys = {key for key, _ in AUDIT_CATEGORIES}
    assert set(AUDIT_META) == {action.value for action in AuditAction}
    assert all(meta["category"] in keys - {"all"} for meta in AUDIT_META.values())


def test_pr_links_escape_and_link() -> None:
    from web.routes.views import pr_links, pull_base_url

    base = pull_base_url("owner/infra", mock=False)
    assert base == "https://github.com/owner/infra/pull/"
    assert pull_base_url("owner/infra", mock=True) is None and pull_base_url(None, False) is None
    html = str(pr_links("PR #38 merged; service '<web>' is tracked", base))
    assert (
        '<a class="text-indigo-300 hover:underline" href="https://github.com/owner/infra/pull/38" '
        'target="_blank" rel="noopener noreferrer">PR #38</a>' in html
    )
    assert "&lt;web&gt;" in html and "<web>" not in html
    legacy = str(pr_links("batch PR #7 https://github.com/owner/infra/pull/7", base))
    assert legacy.count("https://github.com/owner/infra/pull/7") == 1  # raw URL folded in
    assert str(pr_links("PR #7 opened", None)) == "PR #7 opened"
    assert str(pr_links(None, base)) == ""


def test_events_page_badges_and_filters(client: TestClient) -> None:
    assert client.post("/api/actions/kill/rogue-db").status_code == 200
    page = client.get("/events").text
    for key, label in (
        ("all", "All"),
        ("actions", "Actions"),
        ("detections", "Detections"),
        ("reversals", "Reversals"),
    ):
        assert f'data-audit-filter="{key}"' in page and f">{label} <span" in page
    assert 'title="enforce_kill">Enforced (Removed)</span>' in page
    assert 'title="rogue_detected">Rogue Detected</span>' in page
    assert 'data-category="actions"' in page and 'data-category="detections"' in page
    assert "break-all" not in page  # names wrap instead of being cut mid-way


def test_events_page_transition_columns(client: TestClient) -> None:
    page = client.get("/events").text
    assert "From &rarr; To" in page and ">Trigger</th>" in page and ">Reason</th>" in page
    assert ">Engine Startup</td>" in page
    assert "Not in baseline (no Compose project labels)" in page
    assert "SHADOW IT" not in page.split("Audit log")[0]  # no prose in the timeline
    events = client.get("/api/events").json()
    assert all(e["trigger"] and e["reason"] for e in events)


def test_dashboard_has_no_removed_counter(client: TestClient) -> None:
    home = client.get("/").text
    assert 'data-count="removed"' not in home and 'data-count="rogue"' in home
